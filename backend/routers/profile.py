"""
Profile router: trading stats and daily PnL for the current user.
"""
from datetime import datetime, timezone
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from trade_relay import database as db_module
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/api/profile", tags=["profile"])


class ProfileStats(BaseModel):
    total_pnl: float
    win_rate: float
    total_trades: int
    total_commission: float
    account_balance: float | None
    total_commission_by_asset: list[dict[str, float | str]]

class DailyPnl(BaseModel):
    date: str
    pnl: float
    net_pnl: float
    account_balance: float | None
    commission: float
    trades: int
    win_rate: float


class DailyLeaderboardEntry(BaseModel):
    rank: int
    username: str
    date: str
    pnl: float
    net_pnl: float
    account_balance: float | None
    trades: int
    win_rate: float
    commission: float


class AllTimeLeaderboardEntry(BaseModel):
    rank: int
    username: str
    pnl: float
    net_pnl: float
    trades: int
    win_rate: float
    commission: float


class ProfileOverview(BaseModel):
    stats: ProfileStats
    daily_pnl: list[DailyPnl]
    daily_leaderboard: list[DailyLeaderboardEntry]
    all_time_leaderboard: list[AllTimeLeaderboardEntry]
    all_time_days: int | None


def _get_today_utc_date_string() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _build_daily_leaderboard_rows() -> list[dict]:
    leaderboard_rows = list(db_module.get_daily_profile_leaderboard())
    users = db_module.get_all_active_users_with_api_keys()
    existing_usernames = {
        str(row.get("username") or "").strip().lower()
        for row in leaderboard_rows
        if str(row.get("username") or "").strip()
    }
    leaderboard_date = str(leaderboard_rows[0].get("date")) if leaderboard_rows else _get_today_utc_date_string()

    for user in users:
        username = str(user.get("username") or "").strip()
        if not username or username.lower() in existing_usernames:
            continue
        user_id = int(user.get("id") or 0)
        account_summary = db_module.get_account_summary_from_db(user_id, None) if user_id > 0 else None
        account_balance_raw = (account_summary or {}).get("wallet_balance")
        leaderboard_rows.append(
            {
                "username": username,
                "date": leaderboard_date,
                "pnl": 0,
                "net_pnl": 0,
                "account_balance": float(account_balance_raw) if account_balance_raw is not None else None,
                "trades": 0,
                "win_rate": 0,
                "commission": 0,
            }
        )
        existing_usernames.add(username.lower())

    leaderboard_rows.sort(
        key=lambda row: (
            -float(row.get("pnl") or 0),
            -float(row.get("win_rate") or 0),
            -int(row.get("trades") or 0),
            str(row.get("username") or ""),
        )
    )
    return leaderboard_rows[:10]


def _build_profile_overview(user_id: int, all_time_days: int | None = None) -> ProfileOverview:
    rows = db_module.get_daily_pnl(user_id)
    commission_rows = db_module.get_total_commission_by_asset(user_id)
    leaderboard_rows = _build_daily_leaderboard_rows()
    all_time_leaderboard_rows = db_module.get_all_time_profile_leaderboard_for_days(days=all_time_days)
    account_summary = db_module.get_account_summary_from_db(user_id, None) or {}
    daily_pnl = [
        DailyPnl(
            date=str(r["date"]),
            pnl=round(float(r.get("pnl") or 0), 4),
            net_pnl=round(float(r.get("net_pnl") or ((r.get("pnl") or 0) - (r.get("commission") or 0))), 4),
            account_balance=round(float(r.get("account_balance")), 4) if r.get("account_balance") is not None else None,
            commission=round(float(r.get("commission") or 0), 4),
            trades=int(r.get("trades") or 0),
            win_rate=round(float(r.get("win_rate") or 0), 2),
        )
        for r in rows
    ]
    daily_leaderboard = [
        DailyLeaderboardEntry(
            rank=index + 1,
            username=str(row.get("username") or "-"),
            date=str(row.get("date")),
            pnl=round(float(row.get("pnl") or 0), 4),
            net_pnl=round(float(row.get("net_pnl") or ((row.get("pnl") or 0) - (row.get("commission") or 0))), 4),
            account_balance=round(float(row.get("account_balance")), 4) if row.get("account_balance") is not None else None,
            trades=int(row.get("trades") or 0),
            win_rate=round(float(row.get("win_rate") or 0), 2),
            commission=round(float(row.get("commission") or 0), 4),
        )
        for index, row in enumerate(leaderboard_rows)
    ]
    all_time_leaderboard = []
    for index, row in enumerate(all_time_leaderboard_rows):
        pnl = round(float(row.get("pnl") or 0), 4)
        commission = round(float(row.get("commission") or 0), 4)
        net_pnl = round(float(row.get("net_pnl") or (pnl - commission)), 4)

        if all_time_days is None:
            leaderboard_user_id = int(row.get("user_id") or 0)
            if leaderboard_user_id > 0:
                current_balance = db_module.get_profile_current_balance(leaderboard_user_id)
                initial_balance = db_module.get_profile_initial_balance(leaderboard_user_id)
                if current_balance is not None and initial_balance is not None:
                    net_pnl = round(current_balance - initial_balance, 4)

        all_time_leaderboard.append(
            AllTimeLeaderboardEntry(
                rank=index + 1,
                username=str(row.get("username") or "-"),
                pnl=pnl,
                net_pnl=net_pnl,
                trades=int(row.get("trades") or 0),
                win_rate=round(float(row.get("win_rate") or 0), 2),
                commission=commission,
            )
        )

    total_pnl = sum(float(row.get("pnl") or 0) for row in rows)
    total_trades = sum(int(row.get("trades") or 0) for row in rows)
    total_wins = sum(
        int(row.get("win_count") or 0)
        if row.get("win_count") is not None
        else float(row.get("win_rate") or 0) * int(row.get("trades") or 0) / 100.0
        for row in rows
    )
    total_commission_by_asset = [
        {
            "asset": str(row.get("asset") or "UNKNOWN"),
            "total": round(float(row.get("total") or 0), 8),
        }
        for row in commission_rows
    ]
    total_commission = round(sum(float(item["total"]) for item in total_commission_by_asset), 8)
    win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0.0
    account_balance_raw = account_summary.get("wallet_balance")
    account_balance = round(float(account_balance_raw), 4) if account_balance_raw is not None else None

    return ProfileOverview(
        stats=ProfileStats(
            total_pnl=round(total_pnl, 4),
            win_rate=round(win_rate, 2),
            total_trades=total_trades,
            total_commission=round(total_commission, 4),
            account_balance=account_balance,
            total_commission_by_asset=total_commission_by_asset,
        ),
        daily_pnl=daily_pnl,
        daily_leaderboard=daily_leaderboard,
        all_time_leaderboard=all_time_leaderboard,
        all_time_days=all_time_days,
    )


@router.get("/overview", response_model=ProfileOverview)
def get_overview(
    all_time_days: int | None = Query(default=None),
    user: dict = Depends(get_current_user),
):
    user_id = int(user["sub"])
    normalized_days = all_time_days if all_time_days in {7, 30} else None
    return _build_profile_overview(user_id, all_time_days=normalized_days)


@router.get("/stats", response_model=ProfileStats)
def get_stats(user: dict = Depends(get_current_user)):
    user_id = int(user["sub"])
    return _build_profile_overview(user_id).stats


@router.get("/daily-pnl", response_model=list[DailyPnl])
def get_daily_pnl(user: dict = Depends(get_current_user)):
    user_id = int(user["sub"])
    return _build_profile_overview(user_id).daily_pnl
