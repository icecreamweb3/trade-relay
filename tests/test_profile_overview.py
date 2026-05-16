from backend.routers import profile as profile_router


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def execute(self, sql, params):
        assert "FROM position_history" in sql
        assert params == (5,)

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _FakeCursor(self._row)

    def close(self):
        return None


def test_build_profile_overview_uses_position_history_for_win_rate(monkeypatch):
    monkeypatch.setattr(
        profile_router.db_module,
        "get_daily_pnl",
        lambda user_id: [{"date": "2026-05-16", "pnl": 12.5, "commission": 0.7}],
    )
    monkeypatch.setattr(
        profile_router.db_module,
        "get_connection",
        lambda: _FakeConnection({"cnt": 5, "wins": 3}),
    )

    overview = profile_router._build_profile_overview(5)

    assert overview.stats.total_trades == 5
    assert overview.stats.win_rate == 60.0
    assert overview.stats.total_pnl == 12.5
    assert overview.stats.total_commission == 0.7