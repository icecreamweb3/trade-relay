import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.routers import orders as orders_router
from backend.routers.orders import _plan_close_limit_rebalance


def _order(order_id: int, quantity: float, filled_qty: float = 0) -> dict:
    return {
        "id": order_id,
        "quantity": quantity,
        "filled_qty": filled_qty,
    }


def test_full_position_take_profit_is_reduced_for_new_half_position_tier():
    existing = _order(10, 0.020)

    changes = _plan_close_limit_rebalance(
        [existing],
        position_quantity=0.020,
        new_quantity=0.010,
    )

    assert changes == [(existing, 0.010)]


def test_partially_filled_take_profit_uses_its_remaining_quantity():
    existing = _order(11, 0.020, filled_qty=0.005)

    changes = _plan_close_limit_rebalance(
        [existing],
        position_quantity=0.015,
        new_quantity=0.007,
    )

    assert changes == [(existing, 0.008)]


def test_largest_existing_tier_is_reduced_first():
    smaller = _order(20, 0.008)
    larger = _order(21, 0.012)

    changes = _plan_close_limit_rebalance(
        [smaller, larger],
        position_quantity=0.020,
        new_quantity=0.005,
    )

    assert changes == [(larger, 0.007)]


def test_existing_tiers_are_unchanged_when_free_quantity_is_sufficient():
    assert _plan_close_limit_rebalance(
        [_order(30, 0.005)],
        position_quantity=0.020,
        new_quantity=0.010,
    ) == []


def test_new_tier_cannot_exceed_the_live_position():
    with pytest.raises(ValueError, match="exceeds position quantity"):
        _plan_close_limit_rebalance(
            [_order(40, 0.020)],
            position_quantity=0.020,
            new_quantity=0.021,
        )


def test_rebalance_amends_old_price_and_leaves_half_for_new_tier(monkeypatch):
    existing = {
        **_order(50, 0.020),
        "symbol": "BTCUSDC",
        "side": "SELL",
        "order_type": "LIMIT",
        "order_category": "Basic",
        "status": "NEW",
        "trade_direction": "CLOSE",
        "reduce_only": True,
        "exchange_order_id": "9001",
        "price": 78070.7,
    }
    monkeypatch.setattr(orders_router.db_module, "get_active_orders", lambda user_id: [existing])
    monkeypatch.setattr(
        orders_router.db_module,
        "get_position",
        lambda *args, **kwargs: {"quantity": 0.020},
    )
    async def immediate_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(orders_router.asyncio, "to_thread", immediate_to_thread)
    amended = []

    async def fake_amend(order_id, body, user):
        amended.append((order_id, body.quantity, body.price, user["username"]))
        return {"order_id": 51}

    monkeypatch.setattr(orders_router, "amend_order", fake_amend)

    result = asyncio.run(orders_router._rebalance_close_limit_orders(
        user={"sub": "7", "username": "Will", "role": "user"},
        symbol="BTCUSDC",
        side="SELL",
        new_quantity=0.010,
    ))

    assert amended == [(50, 0.010, 78070.7, "Will")]
    assert result == [{
        "order_id": 50,
        "replacement_order_id": 51,
        "action": "reduced",
        "old_remaining_quantity": 0.020,
        "new_remaining_quantity": 0.010,
    }]
