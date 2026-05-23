import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import backfill_position_history_entry_price as task


def test_derive_entry_price_for_long_position():
    entry_price = task._derive_entry_price(
        side="LONG",
        quantity=0.004,
        close_price=75483.4,
        realized_pnl=0.3496,
    )

    assert entry_price == 75396.0


def test_derive_entry_price_for_short_position():
    entry_price = task._derive_entry_price(
        side="SHORT",
        quantity=0.005,
        close_price=40000.0,
        realized_pnl=1.25,
    )

    assert entry_price == 40250.0


def test_resolve_entry_price_falls_back_to_close_order_formula():
    history_row = {
        "side": "LONG",
        "quantity": None,
        "close_price": None,
        "realized_pnl": None,
    }
    order_row = {
        "filled_qty": 0.004,
        "avg_price": 75460.0,
        "realized_pnl": -0.31,
    }

    entry_price, source = task._resolve_entry_price(history_row, order_row)

    assert round(entry_price or 0.0, 10) == 75537.5
    assert source == "close_order_formula"


def test_resolve_entry_price_skips_when_required_inputs_missing():
    entry_price, source = task._resolve_entry_price(
        {
            "side": "LONG",
            "quantity": None,
            "close_price": None,
            "realized_pnl": None,
        },
        None,
    )

    assert entry_price is None
    assert source == "missing_inputs"