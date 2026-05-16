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

class DailyPnl(BaseModel):
    date: str
    pnl: float
    commission: float


class ProfileOverview(BaseModel):
    stats: ProfileStats
    daily_pnl: list[DailyPnl]


def _build_profile_overview(user_id: int) -> ProfileOverview:
    rows = db_module.get_daily_pnl(user_id)
    daily_pnl = [
        DailyPnl(
            date=str(r["date"]),
            pnl=round(float(r.get("pnl") or 0), 4),
            commission=round(float(r.get("commission") or 0), 4),
        )
        for r in rows
    ]

    total_pnl = sum(item.pnl for item in daily_pnl)
    total_commission = sum(item.commission for item in daily_pnl)

    conn = db_module.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) AS cnt,
                          SUM(CASE WHEN side='SELL' AND avg_price > 0 THEN 1 ELSE 0 END) AS wins
                   FROM orders
                   WHERE user_id = %s AND status = 'FILLED' AND filled_qty > 0""",
                (user_id,),
            )
            row = cur.fetchone()
            total_trades = int(row["cnt"]) if row else 0
            wins = int(row["wins"] or 0) if row else 0
    finally:
        conn.close()

    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

    return ProfileOverview(
        stats=ProfileStats(
            total_pnl=round(total_pnl, 4),
            win_rate=round(win_rate, 2),
            total_trades=total_trades,
            total_commission=round(total_commission, 4),
        ),
        daily_pnl=daily_pnl,
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
