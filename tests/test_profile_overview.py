from backend.routers import profile as profile_router
from trade_relay import database as db_module


def test_build_profile_overview_uses_daily_profile_for_win_rate(monkeypatch):
    monkeypatch.setattr(
        profile_router.db_module,
        "get_daily_pnl",
        lambda user_id: [{"date": "2026-05-16", "pnl": 12.5, "account_balance": 1288.3366, "commission": 0.7, "trades": 5, "win_rate": 60.0}],
    )
    monkeypatch.setattr(
        profile_router.db_module,
        "get_total_commission_by_asset",
        lambda user_id: [{"asset": "USDC", "total": 0.7}],
    )
    monkeypatch.setattr(
        profile_router.db_module,
        "get_daily_profile_leaderboard",
        lambda: [{"username": "alice", "date": "2026-05-16", "pnl": 12.5, "account_balance": 188.5, "trades": 5, "win_rate": 60.0, "commission": 0.7}],
    )
    monkeypatch.setattr(
        profile_router.db_module,
        "get_all_time_profile_leaderboard_for_days",
        lambda *, days=None, limit=20: [{"username": "alice", "pnl": 21.5, "trades": 8, "win_rate": 62.5, "commission": 1.2}],
    )
    monkeypatch.setattr(
        profile_router.db_module,
        "get_account_summary_from_db",
        lambda user_id, symbol: {"wallet_balance": 1288.3366} if user_id == 5 and symbol is None else None,
    )

    overview = profile_router._build_profile_overview(5)

    assert overview.stats.total_trades == 5
    assert overview.stats.win_rate == 60.0
    assert overview.stats.total_pnl == 12.5
    assert overview.stats.total_commission == 0.7
    assert overview.stats.account_balance == 1288.3366
    assert overview.stats.total_commission_by_asset == [{"asset": "USDC", "total": 0.7}]
    assert overview.daily_pnl[0].account_balance == 1288.3366
    assert overview.daily_leaderboard[0].username == "alice"
    assert overview.daily_leaderboard[0].rank == 1
    assert overview.daily_leaderboard[0].account_balance == 188.5
    assert overview.all_time_leaderboard[0].username == "alice"
    assert overview.all_time_leaderboard[0].rank == 1
    assert overview.all_time_days is None


def test_build_profile_overview_totals_follow_daily_profile_aggregation(monkeypatch):
    monkeypatch.setattr(
        profile_router.db_module,
        "get_daily_pnl",
        lambda user_id: [
            {"date": "2026-05-16", "pnl": 5.0229, "account_balance": 105.25, "commission": 0.0, "trades": 3, "win_rate": 66.6667},
            {"date": "2026-05-17", "pnl": -1.5, "account_balance": 103.5, "commission": 0.25, "trades": 1, "win_rate": 0.0},
        ],
    )
    monkeypatch.setattr(
        profile_router.db_module,
        "get_total_commission_by_asset",
        lambda user_id: [{"asset": "USDC", "total": 0.25}],
    )
    monkeypatch.setattr(
        profile_router.db_module,
        "get_daily_profile_leaderboard",
        lambda: [],
    )
    monkeypatch.setattr(
        profile_router.db_module,
        "get_all_time_profile_leaderboard_for_days",
        lambda *, days=None, limit=20: [],
    )
    monkeypatch.setattr(
        profile_router.db_module,
        "get_account_summary_from_db",
        lambda user_id, symbol: None,
    )

    overview = profile_router._build_profile_overview(5)

    assert overview.stats.total_pnl == 3.5229
    assert overview.stats.total_commission == 0.25
    assert overview.stats.account_balance is None
    assert overview.stats.total_commission_by_asset == [{"asset": "USDC", "total": 0.25}]
    assert overview.stats.total_trades == 4
    assert overview.stats.win_rate == 50.0
    assert [entry.account_balance for entry in overview.daily_pnl] == [105.25, 103.5]
    assert overview.daily_pnl[0].pnl == 5.0229
    assert overview.daily_pnl[1].commission == 0.25


def test_build_profile_overview_uses_win_count_for_total_win_rate(monkeypatch):
    monkeypatch.setattr(
        profile_router.db_module,
        "get_daily_pnl",
        lambda user_id: [
            {"date": "2026-05-16", "pnl": 3.0, "account_balance": 103.0, "commission": 0.1, "trades": 3, "win_rate": 33.33, "win_count": 1},
            {"date": "2026-05-17", "pnl": -1.0, "account_balance": 102.0, "commission": 0.2, "trades": 1, "win_rate": 0.0, "win_count": 0},
        ],
    )
    monkeypatch.setattr(profile_router.db_module, "get_total_commission_by_asset", lambda user_id: [{"asset": "USDC", "total": 0.3}])
    monkeypatch.setattr(profile_router.db_module, "get_daily_profile_leaderboard", lambda: [])
    monkeypatch.setattr(profile_router.db_module, "get_all_time_profile_leaderboard_for_days", lambda *, days=None, limit=20: [])
    monkeypatch.setattr(profile_router.db_module, "get_account_summary_from_db", lambda user_id, symbol: None)

    overview = profile_router._build_profile_overview(5)

    assert overview.stats.total_trades == 4
    assert overview.stats.win_rate == 25.0


def test_get_daily_pnl_aggregates_from_position_history(monkeypatch):
    queries = []

    class _StubCursor:
        def execute(self, sql, params):
            queries.append((sql, params))

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _StubConn:
        def cursor(self):
            return _StubCursor()

        def close(self):
            return None

    monkeypatch.setattr(db_module, "get_connection", lambda: _StubConn())

    rows = db_module.get_daily_pnl(5)

    assert rows == []
    sql, params = queries[-1]
    assert "FROM position_history" in sql
    assert "LEFT JOIN daily_profile" in sql
    assert params == (5, 5)


def test_get_all_time_profile_leaderboard_aggregates_from_position_history(monkeypatch):
    queries = []

    class _StubCursor:
        def execute(self, sql, params):
            queries.append((sql, params))

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _StubConn:
        def cursor(self):
            return _StubCursor()

        def close(self):
            return None

    monkeypatch.setattr(db_module, "get_connection", lambda: _StubConn())

    rows = db_module.get_all_time_profile_leaderboard_for_days(days=7, limit=20)

    assert rows == []
    sql, params = queries[-1]
    assert "FROM position_history" in sql
    assert "GROUP BY user_id" in sql
    assert params[-1] == 20


def test_build_profile_overview_preserves_leaderboard_order(monkeypatch):
    monkeypatch.setattr(profile_router.db_module, "get_daily_pnl", lambda user_id: [])
    monkeypatch.setattr(profile_router.db_module, "get_total_commission_by_asset", lambda user_id: [])
    monkeypatch.setattr(profile_router.db_module, "get_account_summary_from_db", lambda user_id, symbol: None)
    monkeypatch.setattr(profile_router.db_module, "get_all_active_users_with_api_keys", lambda: [])
    monkeypatch.setattr(profile_router.db_module, "get_all_time_profile_leaderboard_for_days", lambda *, days=None, limit=20: [])
    monkeypatch.setattr(
        profile_router.db_module,
        "get_daily_profile_leaderboard",
        lambda: [
            {"username": "bob", "date": "2026-05-16", "pnl": 8.0, "account_balance": 108.0, "trades": 2, "win_rate": 50.0, "commission": 0.2},
            {"username": "alice", "date": "2026-05-16", "pnl": 5.0, "account_balance": 205.0, "trades": 1, "win_rate": 100.0, "commission": 0.1},
        ],
    )

    overview = profile_router._build_profile_overview(5)

    assert [entry.rank for entry in overview.daily_leaderboard] == [1, 2]
    assert [entry.username for entry in overview.daily_leaderboard] == ["bob", "alice"]
    assert [entry.account_balance for entry in overview.daily_leaderboard] == [108.0, 205.0]


def test_build_profile_overview_preserves_all_time_leaderboard_order(monkeypatch):
    monkeypatch.setattr(profile_router.db_module, "get_daily_pnl", lambda user_id: [])
    monkeypatch.setattr(profile_router.db_module, "get_total_commission_by_asset", lambda user_id: [])
    monkeypatch.setattr(profile_router.db_module, "get_account_summary_from_db", lambda user_id, symbol: None)
    monkeypatch.setattr(profile_router.db_module, "get_all_active_users_with_api_keys", lambda: [])
    monkeypatch.setattr(profile_router.db_module, "get_daily_profile_leaderboard", lambda: [])
    monkeypatch.setattr(
        profile_router.db_module,
        "get_all_time_profile_leaderboard_for_days",
        lambda *, days=None, limit=20: [
            {"username": "Will", "pnl": 15.0, "trades": 10, "win_rate": 70.0, "commission": 1.5},
            {"username": "Simba", "pnl": 11.0, "trades": 8, "win_rate": 75.0, "commission": 1.1},
        ],
    )

    overview = profile_router._build_profile_overview(5)

    assert [entry.rank for entry in overview.all_time_leaderboard] == [1, 2]
    assert [entry.username for entry in overview.all_time_leaderboard] == ["Will", "Simba"]


def test_build_profile_overview_passes_all_time_days_filter(monkeypatch):
    captured = {}

    monkeypatch.setattr(profile_router.db_module, "get_daily_pnl", lambda user_id: [])
    monkeypatch.setattr(profile_router.db_module, "get_total_commission_by_asset", lambda user_id: [])
    monkeypatch.setattr(profile_router.db_module, "get_daily_profile_leaderboard", lambda: [])
    monkeypatch.setattr(profile_router.db_module, "get_all_active_users_with_api_keys", lambda: [])
    monkeypatch.setattr(profile_router.db_module, "get_account_summary_from_db", lambda user_id, symbol: None)

    def fake_all_time_leaderboard_for_days(*, days=None, limit=20):
        captured["days"] = days
        captured["limit"] = limit
        return []

    monkeypatch.setattr(
        profile_router.db_module,
        "get_all_time_profile_leaderboard_for_days",
        fake_all_time_leaderboard_for_days,
    )

    overview = profile_router._build_profile_overview(5, all_time_days=7)

    assert overview.all_time_days == 7
    assert captured == {"days": 7, "limit": 20}


def test_build_profile_overview_supplements_daily_leaderboard_with_active_users(monkeypatch):
    monkeypatch.setattr(profile_router.db_module, "get_daily_pnl", lambda user_id: [])
    monkeypatch.setattr(profile_router.db_module, "get_total_commission_by_asset", lambda user_id: [])
    monkeypatch.setattr(
        profile_router.db_module,
        "get_daily_profile_leaderboard",
        lambda: [
            {"username": "alice", "date": "2026-05-20", "pnl": 8.0, "account_balance": 108.0, "trades": 2, "win_rate": 50.0, "commission": 0.2},
        ],
    )
    monkeypatch.setattr(
        profile_router.db_module,
        "get_all_active_users_with_api_keys",
        lambda: [{"id": 1, "username": "alice"}, {"id": 2, "username": "bob"}],
    )
    monkeypatch.setattr(
        profile_router.db_module,
        "get_account_summary_from_db",
        lambda user_id, symbol: {"wallet_balance": 205.5} if user_id == 2 and symbol is None else None,
    )
    monkeypatch.setattr(profile_router.db_module, "get_all_time_profile_leaderboard_for_days", lambda *, days=None, limit=20: [])

    overview = profile_router._build_profile_overview(5)

    assert [entry.username for entry in overview.daily_leaderboard] == ["alice", "bob"]
    assert [entry.rank for entry in overview.daily_leaderboard] == [1, 2]
    assert overview.daily_leaderboard[1].trades == 0
    assert overview.daily_leaderboard[1].account_balance == 205.5


def test_build_profile_overview_supplemented_users_are_sorted_and_limited(monkeypatch):
    monkeypatch.setattr(profile_router.db_module, "get_daily_pnl", lambda user_id: [])
    monkeypatch.setattr(profile_router.db_module, "get_total_commission_by_asset", lambda user_id: [])
    monkeypatch.setattr(profile_router.db_module, "get_daily_profile_leaderboard", lambda: [])
    monkeypatch.setattr(
        profile_router.db_module,
        "get_all_active_users_with_api_keys",
        lambda: [{"id": index, "username": f"user{index:02d}"} for index in range(1, 13)],
    )
    monkeypatch.setattr(
        profile_router.db_module,
        "get_account_summary_from_db",
        lambda user_id, symbol: {"wallet_balance": 100 + user_id} if symbol is None else None,
    )
    monkeypatch.setattr(profile_router.db_module, "get_all_time_profile_leaderboard_for_days", lambda *, days=None, limit=20: [])

    overview = profile_router._build_profile_overview(5)

    assert len(overview.daily_leaderboard) == 10
    assert [entry.username for entry in overview.daily_leaderboard] == [
        "user01", "user02", "user03", "user04", "user05", "user06", "user07", "user08", "user09", "user10",
    ]