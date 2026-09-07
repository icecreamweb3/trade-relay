from datetime import datetime, timezone

import pytest

from trade_relay.trading.excursion_metrics import (
    calculate_excursion_metrics,
    choose_position_cycle,
    fetch_cycle_klines,
)


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
