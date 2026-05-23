import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import diagnose_position_history_entry_price as task


def test_analyze_row_reports_ok_when_within_tolerance():
    status, expected, delta = task._analyze_row(
        {
            "side": "LONG",
            "quantity": 0.004,
            "close_price": 75483.4,
            "realized_pnl": 0.3496,
            "entry_price": 75396.005,
        },
        tolerance=0.01,
    )

    assert status == "ok"
    assert expected == 75396.0
    assert round(delta or 0.0, 10) == 0.005


def test_analyze_row_reports_mismatch_when_outside_tolerance():
    status, expected, delta = task._analyze_row(
        {
            "side": "LONG",
            "quantity": 0.004,
            "close_price": 75483.4,
            "realized_pnl": 0.3496,
            "entry_price": 75395.8,
        },
        tolerance=0.01,
    )

    assert status == "mismatch"
    assert expected == 75396.0
    assert round(delta or 0.0, 10) == 0.2


def test_analyze_row_reports_unverifiable_for_invalid_inputs():
    status, expected, delta = task._analyze_row(
        {
            "side": "LONG",
            "quantity": 0,
            "close_price": 75483.4,
            "realized_pnl": 0.3496,
            "entry_price": 75396.0,
        },
        tolerance=0.01,
    )

    assert status == "unverifiable"
    assert expected is None
    assert delta is None