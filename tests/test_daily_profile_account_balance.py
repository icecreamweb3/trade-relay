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


def test_refresh_historical_daily_profile_preserves_existing_account_balance(monkeypatch):
    cursor = FakeCursor([
        {
            "trade_count": 2,
            "win_count": 1,
            "pnl": 3.5,
            "commission": 0.2,
            "latest_username": "alice",
        },
        {"account_balance": 188.125},
    ])

    fetch_called = False

    def fake_fetch_live_wallet_balance(username: str):
        nonlocal fetch_called
        fetch_called = True
        return 205.4321

    monkeypatch.setattr(db, "_fetch_live_wallet_balance", fake_fetch_live_wallet_balance)
    monkeypatch.setattr(db, "_utc_now_naive", lambda: datetime(2026, 5, 20, 11, 30, 0))

    db._refresh_daily_profile_for_user_date(cursor, 7, "alice", date(2026, 5, 19))

    assert fetch_called is False
    assert len(cursor.executed) == 3
    lookup_sql, lookup_params = cursor.executed[1]
    assert "SELECT account_balance FROM daily_profile" in lookup_sql
    assert lookup_params == (7, date(2026, 5, 19))
    insert_sql, insert_params = cursor.executed[2]
    assert "account_balance" in insert_sql
    assert insert_params == (
        7,
        "alice",
        date(2026, 5, 19),
        3.5,
        188.125,
        2,
        1,
        50.0,
        0.2,
        datetime(2026, 5, 20, 11, 30, 0),
    )


class RebuildCursor:
    def __init__(self):
        self.executed: list[tuple[str, object]] = []
        self.rowcount = 0
        self._fetchall_result = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        normalized_sql = " ".join(sql.split())
        if normalized_sql.startswith("SELECT user_id, profile_date, account_balance FROM daily_profile"):
            self._fetchall_result = [{"user_id": 7, "profile_date": date(2026, 5, 19), "account_balance": 188.125}]
            self.rowcount = 1
        elif normalized_sql.startswith("SELECT user_id,") and "AS username" in normalized_sql and "GROUP BY user_id, DATE(created_at)" in normalized_sql:
            self._fetchall_result = [{"user_id": 7, "username": "alice", "profile_date": date(2026, 5, 19)}]
            self.rowcount = 1
        elif normalized_sql.startswith("DELETE FROM daily_profile"):
            self.rowcount = 1
        elif normalized_sql.startswith("INSERT INTO daily_profile"):
            self.rowcount = 1
        else:
            self._fetchall_result = []
            self.rowcount = 0

    def fetchall(self):
        return list(self._fetchall_result)


def test_rebuild_daily_profile_preserves_historical_account_balance(monkeypatch):
    cursor = RebuildCursor()
    refresh_calls: list[dict] = []

    def fake_refresh(cur, user_id, username, profile_date, historical_account_balance=db._ACCOUNT_BALANCE_MISSING):
        refresh_calls.append(
            {
                "user_id": user_id,
                "username": username,
                "profile_date": profile_date,
                "historical_account_balance": historical_account_balance,
            }
        )

    monkeypatch.setattr(db, "_refresh_daily_profile_for_user_date", fake_refresh)

    result = db._rebuild_daily_profile_from_history(cursor, user_id=7)

    assert result == {"deleted": 1, "rebuilt": 1}
    assert refresh_calls == [
        {
            "user_id": 7,
            "username": "alice",
            "profile_date": date(2026, 5, 19),
            "historical_account_balance": 188.125,
        }
    ]