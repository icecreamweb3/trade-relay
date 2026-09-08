from datetime import datetime, timezone

import pytest

from trade_relay.trading.excursion_metrics import (
    ExcursionCalculationError,
    calculate_cycle_entry_average,
    calculate_excursion_metrics,
    choose_position_cycle,
    fetch_cycle_klines,
)
from trade_relay.trading import excursion_retry_worker


def _order(order_id, at, direction, side, qty, price, **extra):
    return {
        "id": order_id,
        "filled_at": at,
        "trade_direction": direction,
        "side": side,
        "status": "FILLED",
        "filled_qty": qty,
        "avg_price": price,
        **extra,
    }


def _kline(at, low, high):
    open_ms = int(at.replace(tzinfo=timezone.utc).timestamp() * 1000) - 59_999
    close_ms = int(at.replace(tzinfo=timezone.utc).timestamp() * 1000)
    return [open_ms, "100", str(high), str(low), "100", "1", close_ms]


def test_long_cycle_metrics_use_net_equity_and_initial_risk():
    start = datetime(2026, 9, 5, 0, 0)
    cycle = [
        _order(1, start, "OPEN", "BUY", 1, 100, commission=1, sl_price=95),
        _order(2, datetime(2026, 9, 5, 0, 2), "CLOSE", "SELL", 1, 105,
               commission=1, realized_pnl=5),
    ]
    result = calculate_excursion_metrics(
        cycle,
        [_kline(datetime(2026, 9, 5, 0, 1), low=90, high=110)],
    )

    assert result["mfe_usdc"] == pytest.approx(9)
    assert result["mae_usdc"] == pytest.approx(11)
    assert result["net_pnl"] == pytest.approx(3)
    assert result["initial_risk_usdc"] == pytest.approx(6)
    assert result["mfe_r"] == pytest.approx(1.5)
    assert result["profit_capture_rate"] == pytest.approx(1 / 3)
    assert result["profit_giveback_rate"] == pytest.approx(2 / 3)


def test_choose_cycle_by_linked_close_order_and_handle_partial_close():
    orders = [
        _order(1, datetime(2026, 9, 5, 0, 0), "OPEN", "SELL", 2, 100),
        _order(2, datetime(2026, 9, 5, 0, 1), "CLOSE", "BUY", 1, 95),
        _order(3, datetime(2026, 9, 5, 0, 2), "CLOSE", "BUY", 1, 90),
        _order(4, datetime(2026, 9, 5, 1, 0), "OPEN", "SELL", 1, 110),
        _order(5, datetime(2026, 9, 5, 1, 1), "CLOSE", "BUY", 1, 105),
    ]

    selected = choose_position_cycle(orders, "SHORT", target_close_order_ids=[2])
    assert [row["id"] for row in selected] == [1, 2, 3]


def test_choose_cycle_does_not_fall_back_when_target_close_is_missing():
    orders = [
        _order(1, datetime(2026, 9, 5, 0, 0), "OPEN", "BUY", 1, 100),
        _order(2, datetime(2026, 9, 5, 0, 1), "CLOSE", "SELL", 1, 101),
    ]

    with pytest.raises(ExcursionCalculationError, match="目标平仓订单"):
        choose_position_cycle(
            orders,
            "LONG",
            target_close_order_ids=[999],
            closed_at=datetime(2026, 9, 5, 0, 1),
        )


def test_choose_cycle_reconstructs_backwards_without_older_quantity_contamination():
    orders = [
        _order(1, datetime(2026, 9, 5, 0, 0), "OPEN", "BUY", 2, 90),
        _order(2, datetime(2026, 9, 5, 0, 1), "CLOSE", "SELL", 1, 91),
        _order(3, datetime(2026, 9, 5, 1, 0), "OPEN", "BUY", 1, 100),
        _order(4, datetime(2026, 9, 5, 1, 1), "CLOSE", "SELL", 1, 101),
    ]

    selected = choose_position_cycle(orders, "LONG", target_close_order_ids=[4])
    assert [row["id"] for row in selected] == [3, 4]


def test_cycle_entry_average_handles_partial_close_then_reentry():
    start = datetime(2026, 9, 3, 0, 0)
    cycle = [
        _order(1, start, "OPEN", "BUY", 0.01, 77246.3),
        _order(2, datetime(2026, 9, 3, 0, 1), "OPEN", "BUY", 0.01, 77170.4),
        _order(3, datetime(2026, 9, 3, 0, 2), "OPEN", "BUY", 0.01, 76959.1),
        _order(4, datetime(2026, 9, 3, 0, 3), "CLOSE", "SELL", 0.015, 77338.0),
        _order(5, datetime(2026, 9, 3, 0, 4), "OPEN", "BUY", 0.01, 77523.6),
        _order(6, datetime(2026, 9, 3, 0, 5), "OPEN", "BUY", 0.01, 77494.4),
        _order(7, datetime(2026, 9, 3, 0, 6), "CLOSE", "SELL", 0.035, 77057.8),
    ]

    average, total_open_quantity = calculate_cycle_entry_average(cycle)

    assert total_open_quantity == pytest.approx(0.05)
    assert average == pytest.approx(77344.5428571)
    excursion_retry_worker._validate_cycle_identity(
        {"id": 5216, "avg_entry_price": 77344.5428571},
        cycle,
    )


def test_worker_rejects_cycle_linked_to_another_position():
    cycle = [
        _order(1, datetime(2026, 9, 3, 0, 0), "OPEN", "BUY", 1, 100, position_id=99),
        _order(2, datetime(2026, 9, 3, 0, 1), "CLOSE", "SELL", 1, 101, position_id=10),
    ]

    with pytest.raises(ExcursionCalculationError, match="跨越其他持仓"):
        excursion_retry_worker._validate_cycle_identity(
            {"id": 10, "avg_entry_price": 100},
            cycle,
        )


def test_fetch_cycle_klines_pages_with_explicit_time_range():
    class Client:
        def __init__(self):
            self.calls = []

        def get_kline_data(self, **kwargs):
            self.calls.append(kwargs)
            cursor = kwargs["start_time"]
            return [[cursor, "1", "2", "0.5", "1", "1", cursor + 59_999]]

    client = Client()
    start = datetime(2026, 9, 5, 0, 0)
    end = datetime(2026, 9, 5, 0, 1)
    rows = fetch_cycle_klines(client, "BTCUSDC", start, end)

    assert rows
    assert client.calls[0]["interval"] == "1m"
    assert client.calls[0]["start_time"] < client.calls[0]["end_time"]


def test_worker_rejects_cycle_before_replacing_links_when_realized_pnl_mismatches(monkeypatch):
    start = datetime(2026, 9, 5, 0, 0)
    orders = [
        _order(1, start, "OPEN", "BUY", 1, 100),
        _order(2, datetime(2026, 9, 5, 0, 1), "CLOSE", "SELL", 1, 101,
               realized_pnl=1),
    ]
    replaced = []
    monkeypatch.setattr(excursion_retry_worker.db_module, "get_filled_orders_for_position_excursion", lambda row: orders)
    monkeypatch.setattr(excursion_retry_worker.db_module, "replace_filled_orders_for_position", lambda *args: replaced.append(args))

    class Client:
        def get_kline_data(self, **kwargs):
            return [_kline(datetime(2026, 9, 5, 0, 1), low=99, high=102)]

    row = {
        "id": 10,
        "username": "Will",
        "symbol": "BTCUSDC",
        "position_side": "LONG",
        "target_close_order_ids": "2",
        "updated_at": datetime(2026, 9, 5, 0, 1),
        "realized_pnl": 2,
    }

    with pytest.raises(ExcursionCalculationError, match="已实现盈亏"):
        excursion_retry_worker._process_candidate(row, {"Will": Client()})
    assert replaced == []


def test_worker_refreshes_order_ids_before_kline_sync(monkeypatch):
    start = datetime(2026, 9, 5, 0, 0)
    orders = [
        _order(1, start, "OPEN", "BUY", 1, 100),
        _order(2, datetime(2026, 9, 5, 0, 1), "CLOSE", "SELL", 1, 101,
               realized_pnl=1),
    ]
    calls = []
    monkeypatch.setattr(excursion_retry_worker.db_module, "get_filled_orders_for_position_excursion", lambda row: orders)
    monkeypatch.setattr(excursion_retry_worker.db_module, "replace_filled_orders_for_position", lambda *args: calls.append(("replace", args)))
    monkeypatch.setattr(excursion_retry_worker.db_module, "upsert_position_history_final", lambda *args: calls.append(("upsert", args)))

    class Client:
        def get_kline_data(self, **kwargs):
            return []

    row = {
        "id": 10,
        "username": "Will",
        "symbol": "BTCUSDC",
        "position_side": "LONG",
        "target_close_order_ids": "2",
        "updated_at": datetime(2026, 9, 5, 0, 1),
        "realized_pnl": 1,
    }

    with pytest.raises(ExcursionCalculationError, match="K 线"):
        excursion_retry_worker._process_candidate(row, {"Will": Client()})
    assert calls == [("replace", (10, [1, 2])), ("upsert", (10,))]


def test_local_order_link_backfill_does_not_require_klines(monkeypatch):
    start = datetime(2026, 9, 5, 0, 0)
    row = {
        "id": 10,
        "user_id": 5,
        "exchange": "binance",
        "symbol": "BTCUSDC",
        "position_side": "LONG",
        "target_close_order_ids": "2",
        "realized_pnl": 1,
    }
    orders = [
        _order(1, start, "OPEN", "BUY", 1, 100),
        _order(2, datetime(2026, 9, 5, 0, 1), "CLOSE", "SELL", 1, 101,
               realized_pnl=1),
    ]
    calls = []
    monkeypatch.setattr(excursion_retry_worker.db_module, "get_missing_position_order_link_candidates", lambda limit: [row])
    monkeypatch.setattr(excursion_retry_worker.db_module, "get_missing_legacy_order_link_candidates", lambda limit: [])
    monkeypatch.setattr(excursion_retry_worker.db_module, "get_filled_orders_for_position_excursion", lambda candidate: orders)
    monkeypatch.setattr(excursion_retry_worker.db_module, "replace_filled_orders_for_position", lambda *args: calls.append(("replace", args)))
    monkeypatch.setattr(excursion_retry_worker.db_module, "upsert_position_history_final", lambda *args: calls.append(("upsert", args)))

    assert excursion_retry_worker._repair_missing_order_links(100) == (1, 0)
    assert calls == [("replace", (10, [1, 2])), ("upsert", (10,))]
