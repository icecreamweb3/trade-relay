"""
Profile router: trading stats and daily PnL for the current user.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

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


@router.get("/stats", response_model=ProfileStats)
def get_stats(user: dict = Depends(get_current_user)):
    user_id = int(user["sub"])
    rows = db_module.get_daily_pnl(user_id)

    total_pnl = sum(float(r.get("pnl") or 0) for r in rows)
    total_commission = sum(float(r.get("commission") or 0) for r in rows)

    # Count wins vs total using filled orders
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

    return ProfileStats(
        total_pnl=round(total_pnl, 4),
        win_rate=round(win_rate, 2),
        total_trades=total_trades,
        total_commission=round(total_commission, 4),
    )


@router.get("/daily-pnl", response_model=list[DailyPnl])
def get_daily_pnl(user: dict = Depends(get_current_user)):
    user_id = int(user["sub"])
    rows = db_module.get_daily_pnl(user_id)
    return [
        DailyPnl(
            date=str(r["date"]),
            pnl=round(float(r.get("pnl") or 0), 4),
            commission=round(float(r.get("commission") or 0), 4),
        )
        for r in rows
    ]
