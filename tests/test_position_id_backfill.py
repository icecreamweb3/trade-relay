import asyncio
from datetime import datetime, timedelta

from backend.routers import orders as orders_router
from trade_relay.trading import position_id_backfill as task


def _order(order_id, at, direction, side, quantity, price, realized_pnl=0):
    return {
        "id": order_id,
        "user_id": 5,
        "username": "Will",
        "exchange": "binance",
        "symbol": "ETHUSDC",
        "trade_direction": direction,
        "side": side,
        "status": "FILLED",
        "filled_qty": quantity,
        "avg_price": price,
        "filled_at": at,
        "realized_pnl": realized_pnl,
        "commission": 0,
        "position_id": None,
    }


def test_backfill_promotes_a_strictly_validated_complete_cycle(monkeypatch):
    opened_at = datetime(2026, 9, 8, 7, 42, 11)
    candidate = {
        "id": 1816,
        "user_id": 5,
        "username": "Will",
        "exchange": "binance",
        "symbol": "ETHUSDC",
        "side": "SHORT",
        "avg_entry_price": 100,
        "target_close_order_ids": "2",
        "final_close_time": opened_at + timedelta(minutes=12),
    }
    cycle = [
        _order(1, opened_at, "OPEN", "SELL", 1, 100),
        _order(2, opened_at + timedelta(minutes=12), "CLOSE", "BUY", 1, 90, 10),
    ]
    histories = [{
        "id": 1816,
        "close_order_id": 2,
        "realized_pnl": 10,
    }]
    promoted = []
    monkeypatch.setattr(task.db, "get_unlinked_position_cycle_candidates", lambda **kwargs: [candidate])
    monkeypatch.setattr(task.db, "get_filled_orders_for_position_excursion", lambda row: cycle)
    monkeypatch.setattr(task.db, "get_unlinked_position_history_for_close_orders", lambda *args: histories)
    monkeypatch.setattr(
        task.db,
        "promote_unlinked_position_cycle",
        lambda history_ids, order_ids, **kwargs: promoted.append((history_ids, order_ids, kwargs)) or 77,
    )

    result = task.backfill_missing_position_ids(user_id=5)

    assert result == {"scanned": 1, "repaired": 1, "skipped": 0, "failed": 0, "warnings": []}
    assert promoted == [([1816], [1, 2], {"entry_avg_price": 100.0, "opened_at": opened_at})]


def test_backfill_skips_cycle_with_incomplete_close_history(monkeypatch):
    opened_at = datetime(2026, 9, 8, 7, 42, 11)
    candidate = {
        "id": 1816,
        "user_id": 5,
        "username": "Will",
        "exchange": "binance",
        "symbol": "ETHUSDC",
        "side": "SHORT",
        "avg_entry_price": 100,
        "target_close_order_ids": "3",
    }
    cycle = [
        _order(1, opened_at, "OPEN", "SELL", 1, 100),
        _order(2, opened_at + timedelta(minutes=5), "CLOSE", "BUY", 0.5, 95, 2.5),
        _order(3, opened_at + timedelta(minutes=10), "CLOSE", "BUY", 0.5, 90, 5),
    ]
    monkeypatch.setattr(task.db, "get_unlinked_position_cycle_candidates", lambda **kwargs: [candidate])
    monkeypatch.setattr(task.db, "get_filled_orders_for_position_excursion", lambda row: cycle)
    monkeypatch.setattr(
        task.db,
        "get_unlinked_position_history_for_close_orders",
        lambda *args: [{"id": 1816, "close_order_id": 3, "realized_pnl": 5}],
    )

    result = task.backfill_missing_position_ids(user_id=5)

    assert result["repaired"] == 0
    assert result["skipped"] == 1
    assert "平仓历史不齐全" in result["warnings"][0]


def test_orders_endpoint_scopes_backfill_to_current_user(monkeypatch):
    calls = []

    async def run_inline(function, **kwargs):
        return function(**kwargs)

    monkeypatch.setattr(orders_router.asyncio, "to_thread", run_inline)
    monkeypatch.setattr(
        orders_router,
        "backfill_missing_position_ids",
        lambda **kwargs: calls.append(kwargs) or {
            "scanned": 0, "repaired": 0, "skipped": 0, "failed": 0, "warnings": [],
        },
    )

    result = asyncio.run(orders_router.backfill_order_position_ids(
        orders_router.PositionIdBackfillRequest(),
        {"sub": "5", "username": "Will", "role": "user"},
    ))

    assert result["repaired"] == 0
    assert calls == [{"user_id": 5, "dry_run": False}]
