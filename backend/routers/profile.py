"""
Profile router: trading stats and daily PnL for the current user.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from trade_relay import database as db_module
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/api/profile", tags=["profile"])


class ProfileStats(BaseModel):
    total_pnl: float
    win_rate: float
    total_trades: int
    total_commission: float
    total_commission_by_asset: list[dict[str, float | str]]

class DailyPnl(BaseModel):
    date: str
    pnl: float
    commission: float
    trades: int
    win_rate: float


class DailyLeaderboardEntry(BaseModel):
    rank: int
    username: str
    date: str
    pnl: float
    trades: int
    win_rate: float
    commission: float


class ProfileOverview(BaseModel):
    stats: ProfileStats
    daily_pnl: list[DailyPnl]
    daily_leaderboard: list[DailyLeaderboardEntry]


def _build_profile_overview(user_id: int) -> ProfileOverview:
    rows = db_module.get_daily_pnl(user_id)
    commission_rows = db_module.get_total_commission_by_asset(user_id)
    leaderboard_rows = db_module.get_daily_profile_leaderboard()
    daily_pnl = [
        DailyPnl(
            date=str(r["date"]),
            pnl=round(float(r.get("pnl") or 0), 4),
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
            trades=int(row.get("trades") or 0),
            win_rate=round(float(row.get("win_rate") or 0), 2),
            commission=round(float(row.get("commission") or 0), 4),
        )
        for index, row in enumerate(leaderboard_rows)
    ]

    total_pnl = sum(item.pnl for item in daily_pnl)
    total_trades = sum(item.trades for item in daily_pnl)
    total_wins = sum(1 for item in daily_pnl for _ in range(0))
    total_wins = int(sum((item.win_rate * item.trades / 100.0) for item in daily_pnl))
    total_commission_by_asset = [
        {
            "asset": str(row.get("asset") or "UNKNOWN"),
            "total": round(float(row.get("total") or 0), 8),
        }
        for row in commission_rows
    ]
    total_commission = round(sum(float(item["total"]) for item in total_commission_by_asset), 8)
    win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0.0

    return ProfileOverview(
        stats=ProfileStats(
            total_pnl=round(total_pnl, 4),
            win_rate=round(win_rate, 2),
            total_trades=total_trades,
            total_commission=round(total_commission, 4),
            total_commission_by_asset=total_commission_by_asset,
        ),
        daily_pnl=daily_pnl,
        daily_leaderboard=daily_leaderboard,
    )


@router.get("/overview", response_model=ProfileOverview)
def get_overview(user: dict = Depends(get_current_user)):
    user_id = int(user["sub"])
    return _build_profile_overview(user_id)


@router.get("/stats", response_model=ProfileStats)
def get_stats(user: dict = Depends(get_current_user)):
    user_id = int(user["sub"])
    return _build_profile_overview(user_id).stats


@router.get("/daily-pnl", response_model=list[DailyPnl])
def get_daily_pnl(user: dict = Depends(get_current_user)):
    user_id = int(user["sub"])
    return _build_profile_overview(user_id).daily_pnl
