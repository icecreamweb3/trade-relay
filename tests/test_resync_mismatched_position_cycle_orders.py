from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "task" / "resync_mismatched_position_cycle_orders.py"
SPEC = importlib.util.spec_from_file_location("resync_mismatched_position_cycle_orders", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_weighted_open_average_uses_filled_quantity_and_average_price():
    average, quantity = MODULE.weighted_open_average(
        [
            {"trade_direction": "OPEN", "filled_qty": 1, "avg_price": 100},
            {"trade_direction": "OPEN", "filled_qty": 3, "avg_price": 120},
            {"trade_direction": "CLOSE", "filled_qty": 4, "avg_price": 130},
        ]
    )

    assert quantity == 4
    assert average == 115


def test_weighted_open_average_accounts_for_partial_close_before_reentry():
    average, total_open_quantity = MODULE.weighted_open_average(
        [
            {"trade_direction": "OPEN", "filled_qty": 0.01, "avg_price": 77246.3},
            {"trade_direction": "OPEN", "filled_qty": 0.01, "avg_price": 77170.4},
            {"trade_direction": "OPEN", "filled_qty": 0.01, "avg_price": 76959.1},
            {"trade_direction": "CLOSE", "filled_qty": 0.015, "avg_price": 77338.0},
            {"trade_direction": "OPEN", "filled_qty": 0.01, "avg_price": 77523.6},
            {"trade_direction": "OPEN", "filled_qty": 0.01, "avg_price": 77494.4},
            {"trade_direction": "CLOSE", "filled_qty": 0.035, "avg_price": 77057.8},
        ]
    )

    assert total_open_quantity == 0.05
    assert average == pytest.approx(77344.5428571)


def test_find_mismatched_cycles_uses_position_average(monkeypatch):
    row = {
        "id": 7,
        "user_id": 1,
        "exchange": "binance",
        "symbol": "BTCUSDC",
        "position_side": "LONG",
        "avg_entry_price": 100,
        "final_entry_avg_price": 999,
        "target_close_order_ids": "12",
    }
    cycle = [
        {"id": 11, "side": "BUY", "trade_direction": "OPEN", "filled_qty": 1, "avg_price": 110,
         "created_at": "2026-01-01 00:00:00"},
        {"id": 12, "side": "SELL", "trade_direction": "CLOSE", "filled_qty": 1, "avg_price": 120,
         "created_at": "2026-01-01 01:00:00"},
    ]
    monkeypatch.setattr(MODULE.db, "get_filled_orders_for_position_excursion", lambda _row: cycle)

    mismatches, skipped, stale_final = MODULE.find_mismatched_cycles(
        [row], absolute_tolerance=0.01, relative_tolerance=1e-6
    )

    assert not skipped
    assert not stale_final
    assert len(mismatches) == 1
    assert mismatches[0]["expected"] == 100
    assert mismatches[0]["actual"] == 110


def test_find_mismatched_cycles_reuses_orders_for_same_account_symbol(monkeypatch):
    rows = [
        {
            "id": position_id,
            "user_id": 1,
            "exchange": "binance",
            "symbol": "BTCUSDC",
            "position_side": "LONG",
            "avg_entry_price": 100,
            "target_close_order_ids": str(position_id + 100),
        }
        for position_id in (1, 2)
    ]
    calls = []
    cycle = [
        {"id": 10, "side": "BUY", "trade_direction": "OPEN", "filled_qty": 1, "avg_price": 110},
        {"id": 11, "side": "SELL", "trade_direction": "CLOSE", "filled_qty": 1, "avg_price": 120},
    ]
    monkeypatch.setattr(
        MODULE.db,
        "get_filled_orders_for_position_excursion",
        lambda row: calls.append(row["id"]) or cycle,
    )
    monkeypatch.setattr(MODULE, "choose_position_cycle", lambda *args, **kwargs: cycle)

    mismatches, skipped, stale_final = MODULE.find_mismatched_cycles(
        rows, absolute_tolerance=0.01, relative_tolerance=1e-6
    )

    assert len(mismatches) == 2
    assert not skipped
    assert not stale_final
    assert calls == [1]


def test_find_mismatched_cycles_rejects_cross_position_cycle(monkeypatch):
    row = {
        "id": 7,
        "user_id": 1,
        "exchange": "binance",
        "symbol": "BTCUSDC",
        "position_side": "LONG",
        "avg_entry_price": 100,
        "target_close_order_ids": "12",
    }
    cycle = [
        {"id": 11, "position_id": 99, "trade_direction": "OPEN", "filled_qty": 1, "avg_price": 110},
        {"id": 12, "position_id": 7, "trade_direction": "CLOSE", "filled_qty": 1, "avg_price": 120},
    ]
    monkeypatch.setattr(MODULE.db, "get_filled_orders_for_position_excursion", lambda _row: cycle)
    monkeypatch.setattr(MODULE, "choose_position_cycle", lambda *args, **kwargs: cycle)

    mismatches, skipped, stale_final = MODULE.find_mismatched_cycles(
        [row], absolute_tolerance=0.01, relative_tolerance=1e-6
    )

    assert not mismatches
    assert not stale_final
    assert skipped[0][1] == "cycle_crosses_other_positions:99"


def test_matching_cycle_reports_stale_final_average(monkeypatch):
    row = {
        "id": 7,
        "user_id": 1,
        "exchange": "binance",
        "symbol": "BTCUSDC",
        "position_side": "LONG",
        "avg_entry_price": 110,
        "final_entry_avg_price": 105,
        "target_close_order_ids": "12",
    }
    cycle = [
        {"id": 11, "position_id": 7, "trade_direction": "OPEN", "filled_qty": 1, "avg_price": 110},
        {"id": 12, "position_id": 7, "trade_direction": "CLOSE", "filled_qty": 1, "avg_price": 120},
    ]
    monkeypatch.setattr(MODULE.db, "get_filled_orders_for_position_excursion", lambda _row: cycle)
    monkeypatch.setattr(MODULE, "choose_position_cycle", lambda *args, **kwargs: cycle)

    mismatches, skipped, stale_final = MODULE.find_mismatched_cycles(
        [row], absolute_tolerance=0.01, relative_tolerance=1e-6
    )

    assert not mismatches
    assert not skipped
    assert stale_final[0]["current"] == 105
    assert stale_final[0]["expected"] == 110


def test_resync_order_refreshes_status_then_trade_fills(monkeypatch):
    before = {
        "id": 8,
        "username": "alice",
        "symbol": "BTCUSDC",
        "exchange_order_id": "88",
        "status": "FILLED",
        "filled_qty": 1,
        "avg_price": 100,
        "trade_direction": "OPEN",
    }
    after_status = {**before, "filled_qty": 2, "avg_price": 105}
    rows = iter([before, after_status, after_status])
    updates = []
    fill_syncs = []
    monkeypatch.setattr(MODULE.db, "get_order_by_id", lambda _order_id: next(rows))
    monkeypatch.setattr(MODULE.db, "update_order_status", lambda *args, **kwargs: updates.append((args, kwargs)))
    monkeypatch.setattr(
        MODULE,
        "sync_filled_order_trade_details",
        lambda **kwargs: fill_syncs.append(kwargs),
    )
    client = SimpleNamespace(
        get_order_status=lambda symbol, order_id: {
            "status": "FILLED",
            "executedQty": "2",
            "avgPrice": "105",
            "updateTime": 1_700_000_000_000,
        }
    )

    changed, message = MODULE._resync_order(username="alice", client=client, order=before)

    assert changed is True
    assert message == "updated"
    assert updates[0][0] == (8, "FILLED")
    assert updates[0][1]["filled_qty"] == 2
    assert updates[0][1]["avg_price"] == 105
    assert fill_syncs[0]["order_row"] == after_status


def test_dry_run_never_builds_client_or_rebuilds(monkeypatch, capsys):
    args = SimpleNamespace(
        username=None,
        position_id=None,
        limit=10,
        absolute_tolerance=0.01,
        relative_tolerance=1e-6,
        max_cycle_orders=100,
        dry_run=True,
    )
    item = {
        "position": {"id": 1, "username": "alice", "symbol": "BTCUSDC", "position_side": "LONG"},
        "cycle": [{"id": 2}],
        "expected": 100.0,
        "actual": 101.0,
        "difference": 1.0,
        "tolerance": 0.01,
    }
    monkeypatch.setattr(MODULE, "parse_args", lambda: args)
    monkeypatch.setattr(MODULE, "_fetch_closed_positions", lambda **kwargs: [{"id": 1}])
    monkeypatch.setattr(MODULE, "find_mismatched_cycles", lambda *args, **kwargs: ([item], [], []))
    monkeypatch.setattr(MODULE, "_build_client", lambda _username: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(MODULE, "_rebuild_position_cycle", lambda _item: (_ for _ in ()).throw(AssertionError()))

    assert MODULE.main() == 0
    assert "mode=dry-run" in capsys.readouterr().out
