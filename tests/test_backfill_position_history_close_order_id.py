import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "backfill_position_history_close_order_id.py"
SPEC = importlib.util.spec_from_file_location("backfill_position_history_close_order_id", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_find_best_order_match_prefers_exact_close_order_metrics():
    history_row = {
        "id": 65,
        "user_id": 2,
        "symbol": "BTCUSDC",
        "side": "LONG",
        "quantity": 0.004,
        "close_price": 75460.0,
        "realized_pnl": -0.31,
        "commission": 0.120736,
        "position_id": 585,
        "created_at": "2026-05-23 04:29:17",
    }
    candidate_orders = [
        {
            "id": 303,
            "user_id": 2,
            "symbol": "BTCUSDC",
            "side": "SELL",
            "trade_direction": "CLOSE",
            "status": "FILLED",
            "filled_qty": 0.004,
            "avg_price": 75483.4,
            "realized_pnl": 0.3496,
            "commission": 0.12077344,
            "position_id": 585,
            "filled_at": "2026-05-23 03:11:00",
            "updated_at": "2026-05-23 03:11:00",
            "created_at": "2026-05-23 03:11:00",
        },
        {
            "id": 308,
            "user_id": 2,
            "symbol": "BTCUSDC",
            "side": "SELL",
            "trade_direction": "CLOSE",
            "status": "FILLED",
            "filled_qty": 0.004,
            "avg_price": 75460.0,
            "realized_pnl": -0.31,
            "commission": 0.120736,
            "position_id": 585,
            "filled_at": "2026-05-23 04:29:17",
            "updated_at": "2026-05-23 04:29:22",
            "created_at": "2026-05-23 04:13:18",
        },
    ]

    decision = MODULE.find_best_order_match(history_row, candidate_orders, used_order_ids=set())

    assert decision.order_id == 308
    assert decision.reason == "matched"
    assert decision.score >= MODULE.MIN_CONFIDENCE_SCORE


def test_find_best_order_match_skips_used_order_ids():
    history_row = {
        "id": 64,
        "user_id": 2,
        "symbol": "BTCUSDC",
        "side": "LONG",
        "quantity": 0.004,
        "close_price": 75483.4,
        "realized_pnl": 0.3496,
        "commission": 0.12077344,
        "position_id": 585,
        "created_at": "2026-05-23 03:11:00",
    }
    candidate_orders = [
        {
            "id": 303,
            "user_id": 2,
            "symbol": "BTCUSDC",
            "side": "SELL",
            "trade_direction": "CLOSE",
            "status": "FILLED",
            "filled_qty": 0.004,
            "avg_price": 75483.4,
            "realized_pnl": 0.3496,
            "commission": 0.12077344,
            "position_id": 585,
            "filled_at": "2026-05-23 03:11:00",
            "updated_at": "2026-05-23 03:11:00",
            "created_at": "2026-05-23 03:11:00",
        },
    ]

    decision = MODULE.find_best_order_match(history_row, candidate_orders, used_order_ids={303})

    assert decision.order_id is None
    assert decision.reason == "no_candidate"