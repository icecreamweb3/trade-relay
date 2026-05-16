from backend.routers import profile as profile_router


def test_build_profile_overview_uses_daily_profile_for_win_rate(monkeypatch):
    monkeypatch.setattr(
        profile_router.db_module,
        "get_daily_pnl",
        lambda user_id: [{"date": "2026-05-16", "pnl": 12.5, "commission": 0.7, "trades": 5, "win_rate": 60.0}],
    )
    monkeypatch.setattr(
        profile_router.db_module,
        "get_total_commission_by_asset",
        lambda user_id: [{"asset": "USDC", "total": 0.7}],
    )
    monkeypatch.setattr(
        profile_router.db_module,
        "get_daily_profile_leaderboard",
        lambda: [{"username": "alice", "date": "2026-05-16", "pnl": 12.5, "trades": 5, "win_rate": 60.0, "commission": 0.7}],
    )
    monkeypatch.setattr(
        profile_router.db_module,
        "get_all_time_profile_leaderboard",
        lambda: [{"username": "alice", "pnl": 21.5, "trades": 8, "win_rate": 62.5, "commission": 1.2}],
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
    assert overview.daily_leaderboard[0].username == "alice"
    assert overview.daily_leaderboard[0].rank == 1
    assert overview.all_time_leaderboard[0].username == "alice"
    assert overview.all_time_leaderboard[0].rank == 1


def test_build_profile_overview_totals_follow_daily_profile_aggregation(monkeypatch):
    monkeypatch.setattr(
        profile_router.db_module,
        "get_daily_pnl",
        lambda user_id: [
            {"date": "2026-05-16", "pnl": 5.0229, "commission": 0.0, "trades": 3, "win_rate": 66.6667},
            {"date": "2026-05-17", "pnl": -1.5, "commission": 0.25, "trades": 1, "win_rate": 0.0},
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
        "get_all_time_profile_leaderboard",
        lambda: [],
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
    assert overview.daily_pnl[0].pnl == 5.0229
    assert overview.daily_pnl[1].commission == 0.25


def test_build_profile_overview_preserves_leaderboard_order(monkeypatch):
    monkeypatch.setattr(profile_router.db_module, "get_daily_pnl", lambda user_id: [])
    monkeypatch.setattr(profile_router.db_module, "get_total_commission_by_asset", lambda user_id: [])
    monkeypatch.setattr(profile_router.db_module, "get_account_summary_from_db", lambda user_id, symbol: None)
    monkeypatch.setattr(profile_router.db_module, "get_all_time_profile_leaderboard", lambda: [])
    monkeypatch.setattr(
        profile_router.db_module,
        "get_daily_profile_leaderboard",
        lambda: [
            {"username": "bob", "date": "2026-05-16", "pnl": 8.0, "trades": 2, "win_rate": 50.0, "commission": 0.2},
            {"username": "alice", "date": "2026-05-16", "pnl": 5.0, "trades": 1, "win_rate": 100.0, "commission": 0.1},
        ],
    )

    overview = profile_router._build_profile_overview(5)

    assert [entry.rank for entry in overview.daily_leaderboard] == [1, 2]
    assert [entry.username for entry in overview.daily_leaderboard] == ["bob", "alice"]


def test_build_profile_overview_preserves_all_time_leaderboard_order(monkeypatch):
    monkeypatch.setattr(profile_router.db_module, "get_daily_pnl", lambda user_id: [])
    monkeypatch.setattr(profile_router.db_module, "get_total_commission_by_asset", lambda user_id: [])
    monkeypatch.setattr(profile_router.db_module, "get_account_summary_from_db", lambda user_id, symbol: None)
    monkeypatch.setattr(profile_router.db_module, "get_daily_profile_leaderboard", lambda: [])
    monkeypatch.setattr(
        profile_router.db_module,
        "get_all_time_profile_leaderboard",
        lambda: [
            {"username": "Will", "pnl": 15.0, "trades": 10, "win_rate": 70.0, "commission": 1.5},
            {"username": "Simba", "pnl": 11.0, "trades": 8, "win_rate": 75.0, "commission": 1.1},
        ],
    )

    overview = profile_router._build_profile_overview(5)

    assert [entry.rank for entry in overview.all_time_leaderboard] == [1, 2]
    assert [entry.username for entry in overview.all_time_leaderboard] == ["Will", "Simba"]