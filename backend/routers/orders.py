"""
Orders router: submit orders, list active/history.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import asyncio
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from trade_relay import database as db_module
from trade_relay.auth.manager import Session
from trade_relay.trading.order_manager import submit_order
from backend.routers.auth import get_current_user, require_admin

router = APIRouter(prefix="/api/orders", tags=["orders"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class OrderRequest(BaseModel):
    symbol: str
    side: str          # BUY | SELL
    order_type: str    # LIMIT | MARKET
    quantity: float
    price: Optional[float] = None

class OrderOut(BaseModel):
    id: int
    username: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: Optional[float]
    status: str
    filled_qty: float
    avg_price: Optional[float]
    exchange_order_id: Optional[str]
    error_message: Optional[str]
    created_at: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_out(r: dict) -> OrderOut:
    return OrderOut(
        id=r["id"],
        username=r["username"],
        symbol=r["symbol"],
        side=r["side"],
        order_type=r["order_type"],
        quantity=float(r["quantity"]),
        price=float(r["price"]) if r.get("price") is not None else None,
        status=r["status"],
        filled_qty=float(r.get("filled_qty") or 0),
        avg_price=float(r["avg_price"]) if r.get("avg_price") is not None else None,
        exchange_order_id=r.get("exchange_order_id"),
        error_message=r.get("error_message"),
        created_at=str(r["created_at"]),
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("")
async def place_order(body: OrderRequest, user: dict = Depends(get_current_user)):
    session = Session(int(user["sub"]), user["username"], user["role"])
    result = await submit_order(
        session,
        body.symbol,
        body.side,
        body.order_type,
        body.quantity,
        body.price,
    )
    if not result.success:
        raise HTTPException(status_code=400, detail=result.message)
    return {"ok": True, "order_id": result.order_id, "message": result.message}


@router.get("/active", response_model=list[OrderOut])
def get_active_orders(user: dict = Depends(get_current_user)):
    user_id = int(user["sub"]) if user["role"] != "admin" else None
    rows = db_module.get_active_orders(user_id=user_id)
    return [_row_to_out(r) for r in rows]


@router.get("/history", response_model=list[OrderOut])
def get_order_history(user: dict = Depends(get_current_user)):
    user_id = int(user["sub"]) if user["role"] != "admin" else None
    rows = db_module.get_order_history(user_id=user_id)
    return [_row_to_out(r) for r in rows]


@router.get("/fills", response_model=list[OrderOut])
def get_fills(user: dict = Depends(get_current_user)):
    rows = db_module.get_recent_platform_trades(limit=50)
    return [
        OrderOut(
            id=0, username=r["username"], symbol=r["symbol"], side=r["side"],
            order_type="", quantity=float(r.get("filled_qty") or 0),
            price=None, status="FILLED",
            filled_qty=float(r.get("filled_qty") or 0),
            avg_price=float(r["avg_price"]) if r.get("avg_price") is not None else None,
            exchange_order_id=None, error_message=None,
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]
