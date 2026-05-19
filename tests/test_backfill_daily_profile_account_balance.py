from decimal import Decimal
import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "backfill_daily_profile_account_balance.py"
SPEC = importlib.util.spec_from_file_location("backfill_daily_profile_account_balance", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_build_balance_updates_accumulates_net_profit_per_user():
    rows = [
        {"id": 11, "user_id": 1, "pnl": Decimal("5.0000"), "commission": Decimal("1.2000")},
        {"id": 12, "user_id": 1, "pnl": Decimal("-2.0000"), "commission": Decimal("0.3000")},
        {"id": 21, "user_id": 2, "pnl": Decimal("0.0000"), "commission": Decimal("0.0000")},
        {"id": 22, "user_id": 2, "pnl": Decimal("1.5555"), "commission": Decimal("0.1111")},
    ]

    updates = MODULE.build_balance_updates(rows, Decimal("200"))

    assert updates == [
        (Decimal("203.8000"), 11),
        (Decimal("201.5000"), 12),
        (Decimal("200.0000"), 21),
        (Decimal("201.4444"), 22),
    ]