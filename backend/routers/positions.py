"""
Positions router: current positions, open orders, order history, trade history.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from trade_relay import database as db_module
from backend.routers.auth import get_current_user

router = APIRouter(prefix="/api/positions", tags=["positions"])


class PositionOut(BaseModel):
    id: int
    username: str
    symbol: str
    position_side: str
    quantity: float
    avg_entry_price: Optional[float]
    unrealized_pnl: Optional[float]
    realized_pnl: float
    leverage: int
    margin_type: str
    updated_at: str


@router.get("", response_model=list[PositionOut])
def get_positions(user: dict = Depends(get_current_user)):
    user_id = int(user["sub"]) if user["role"] != "admin" else None
    rows = db_module.get_positions(user_id=user_id)
    return [
        PositionOut(
            id=r["id"],
            username=r["username"],
            symbol=r["symbol"],
            position_side=r["position_side"],
            quantity=float(r["quantity"]),
            avg_entry_price=float(r["avg_entry_price"]) if r.get("avg_entry_price") is not None else None,
            unrealized_pnl=float(r["unrealized_pnl"]) if r.get("unrealized_pnl") is not None else None,
            realized_pnl=float(r.get("realized_pnl") or 0),
            leverage=r["leverage"],
            margin_type=r["margin_type"],
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]
