import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.routers import account as account_router
from backend.routers import positions as positions_router


def test_trigger_price_prefers_live_mark_price_over_db_snapshot(monkeypatch):
    monkeypatch.setattr(
        account_router,
        "_fetch_public_mark_price",
        lambda symbol, username: 2497.8,
    )

    def fail_if_db_is_read(user_id, symbol):
        raise AssertionError("DB snapshot must not override a live mark price")

    monkeypatch.setattr(
        positions_router.db_module,
        "get_account_summary_from_db",
        fail_if_db_is_read,
    )

    assert positions_router._fetch_current_trigger_price(7, "alice", "ethusdc") == 2497.8


def test_trigger_price_falls_back_to_db_snapshot_when_live_lookup_fails(monkeypatch):
    def fail_live_lookup(symbol, username):
        raise RuntimeError("Binance unavailable")

    monkeypatch.setattr(account_router, "_fetch_public_mark_price", fail_live_lookup)
    monkeypatch.setattr(
        positions_router.db_module,
        "get_account_summary_from_db",
        lambda user_id, symbol: {"rest_mark_price": 2482.57},
    )

    assert positions_router._fetch_current_trigger_price(7, "alice", "ETHUSDC") == 2482.57
