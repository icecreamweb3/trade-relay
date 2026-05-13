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
from trade_relay import config as cfg
from trade_relay.auth.manager import Session
from trade_relay.trading.order_manager import submit_order
from trade_relay.exchange.binance_client import BinanceClient as FuturesBinanceClient
from backend.routers.auth import get_current_user, require_admin
from backend.logger import get_logger

router = APIRouter(prefix="/api/orders", tags=["orders"])
_log = get_logger(__name__)


# ── Schemas ───────────────────────────────────────────────────────────────────

class OrderRequest(BaseModel):
    symbol: str
    side: str          # BUY | SELL
    order_type: str    # LIMIT | MARKET
    quantity: float
    price: Optional[float] = None
    leverage: int = 10
    position_direction: str = 'OPEN'  # OPEN | CLOSE

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


class OrderUserOption(BaseModel):
    id: int
    username: str


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
    _log.info("Place order: user=%s symbol=%s side=%s type=%s qty=%s price=%s pos_dir=%s",
              user["username"], body.symbol, body.side, body.order_type, body.quantity, body.price, body.position_direction)
    session = Session(int(user["sub"]), user["username"], user["role"])
    result = await submit_order(
        session,
        body.symbol,
        body.side,
        body.order_type,
        body.quantity,
        body.price,
        body.leverage,
        body.position_direction,
    )
    if not result.success:
        _log.warning("Order failed: user=%s reason=%s", user["username"], result.message)
        raise HTTPException(status_code=400, detail=result.message)
    _log.info("Order placed: order_id=%s user=%s", result.order_id, user["username"])
    return {"ok": True, "order_id": result.order_id, "message": result.message}


@router.get("", response_model=list[OrderOut])
def list_orders(
    limit: int = 200,
    username: Optional[str] = None,
    order_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    status: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    rows = db_module.query_orders(
        limit=limit,
        user_id=None,
        username=username,
        order_id=order_id,
        start_time=start_time,
        end_time=end_time,
        status=status,
    )
    return [_row_to_out(r) for r in rows]


@router.get("/users", response_model=list[OrderUserOption])
def list_order_users(user: dict = Depends(get_current_user)):
    rows = db_module.get_all_users()
    return [
        OrderUserOption(id=row["id"], username=row["username"])
        for row in rows
        if row.get("role") != "admin"
    ]


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


class CancelOrderRequest(BaseModel):
    symbol: str
    exchange_order_id: str


@router.post("/{order_id}/cancel")
async def cancel_order(order_id: int, body: CancelOrderRequest, user: dict = Depends(get_current_user)):
    """Cancel an open order on Binance and mark it CANCELED in DB."""
    username = user["username"]

    # Verify the order belongs to this user (or admin can cancel any)
    rows = db_module.query_orders(user_id=None, username=username if user["role"] != "admin" else None)
    order_row = next((r for r in rows if r["id"] == order_id), None)
    if not order_row:
        raise HTTPException(status_code=404, detail="Order not found")
    if order_row.get("exchange_order_id") != body.exchange_order_id:
        raise HTTPException(status_code=400, detail="exchange_order_id mismatch")

    # Determine whose credentials to use (admin acts on behalf of order owner)
    target_username = order_row["username"]

    mock = cfg.is_mock_mode(target_username)
    if mock:
        db_module.update_order_status(order_id, "CANCELED")
        _log.info("Mock cancel: order_id=%s", order_id)
        return {"ok": True}

    api_key = cfg.get_api_key(target_username)
    api_secret = cfg.get_api_secret(target_username)
    if not api_key or not api_secret:
        raise HTTPException(status_code=400, detail="No API credentials configured")

    testnet = cfg.is_testnet(target_username)
    try:
        client = FuturesBinanceClient(api_key=api_key, secret_key=api_secret, testnet=testnet)
        import asyncio
        result = await asyncio.to_thread(client.cancel_order, body.symbol, body.exchange_order_id)
        _log.info("Binance cancel result: order_id=%s result=%s", order_id, result)
    except Exception as exc:
        _log.warning("Cancel order failed on Binance: order_id=%s error=%s", order_id, exc)
        raise HTTPException(status_code=502, detail=f"Binance cancel failed: {exc}")

    db_module.update_order_status(order_id, "CANCELED")
    return {"ok": True}

