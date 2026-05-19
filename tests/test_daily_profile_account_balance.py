from datetime import date, datetime

from trade_relay import database as db


class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)
        self.executed: list[tuple[str, object]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        if not self._rows:
            return None
        return self._rows.pop(0)


def test_refresh_daily_profile_persists_live_account_balance(monkeypatch):
    cursor = FakeCursor([
        {
            "trade_count": 2,
            "win_count": 1,
            "pnl": 3.5,
            "commission": 0.2,
            "latest_username": "alice",
        }
    ])

    monkeypatch.setattr(db, "_fetch_live_wallet_balance", lambda username: 205.4321)
    monkeypatch.setattr(db, "_utc_now_naive", lambda: datetime(2026, 5, 19, 11, 30, 0))

    db._refresh_daily_profile_for_user_date(cursor, 7, "alice", date(2026, 5, 19))

    assert len(cursor.executed) == 2
    insert_sql, insert_params = cursor.executed[1]
    assert "account_balance" in insert_sql
    assert insert_params == (
        7,
        "alice",
        date(2026, 5, 19),
        3.5,
        205.4321,
        2,
        1,
        50.0,
        0.2,
        datetime(2026, 5, 19, 11, 30, 0),
    )