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
    order_type: str    # LIMIT | MARKET | STOP | STOP_MARKET
    quantity: float
    price: Optional[float] = None
    stop_price: Optional[float] = None   # trigger price for conditional orders
    leverage: int = 10
    position_direction: str = 'OPEN'  # OPEN | CLOSE

class OrderOut(BaseModel):
    id: int
    username: str
    symbol: str
    side: str
    order_type: str
    trade_direction: Optional[str] = None
    quantity: float
    price: Optional[float]
    stop_price: Optional[float] = None
    reduce_only: bool = False
    post_only: bool = False
    order_category: str = 'Basic'
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
    trade_dir = str(r["trade_direction"]).upper() if r.get("trade_direction") else None
    order_category = str(r.get("order_category") or "Basic")
    if order_category == "Condition":
        order_category = "Conditional"
    return OrderOut(
        id=r["id"],
        username=r["username"],
        symbol=r["symbol"],
        side=r["side"],
        order_type=r["order_type"],
        trade_direction=trade_dir,
        quantity=float(r["quantity"]),
        price=float(r["price"]) if r.get("price") is not None else None,
        stop_price=float(r["stop_price"]) if r.get("stop_price") is not None else None,
        reduce_only=bool(r.get("reduce_only") or False),
        post_only=bool(r.get("post_only") or False),
        order_category=order_category,
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
        body.stop_price,
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
            order_type="",
            trade_direction=str(r["trade_direction"]).upper() if r.get("trade_direction") else None,
            quantity=float(r.get("filled_qty") or 0),
            price=float(r["price"]) if r.get("price") else None,
            status="FILLED",
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


@router.post("/{order_id:int}/cancel")
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


class ConditionalOrderOut(BaseModel):
    algo_id: int
    symbol: str
    side: str
    position_side: str
    order_type: str       # TAKE_PROFIT_MARKET | STOP_MARKET
    quantity: float
    trigger_price: float
    status: str
    created_at: str


@router.get("/conditional", response_model=list[ConditionalOrderOut])
async def get_conditional_orders(user: dict = Depends(get_current_user)):
    """Fetch open conditional (algo) orders — merged from Binance Algo API and local DB."""
    username = user["username"]
    user_id = int(user["sub"]) if user["role"] != "admin" else None

    result: list[ConditionalOrderOut] = []
    seen_algo_ids: set[int] = set()
    client: FuturesBinanceClient | None = None

    # 1. Try Binance Algo Order API (may not be available for all accounts)
    api_key = cfg.get_api_key(username)
    api_secret = cfg.get_api_secret(username)
    if api_key and api_secret:
        testnet = cfg.is_testnet(username)
        client = FuturesBinanceClient(api_key=api_key, secret_key=api_secret, testnet=testnet)
        binance_orders = await asyncio.to_thread(client.get_open_algo_orders)
        for o in binance_orders:
            try:
                algo_id = int(o.get("algoId") or 0)
                seen_algo_ids.add(algo_id)
                result.append(ConditionalOrderOut(
                    algo_id=algo_id,
                    symbol=str(o.get("symbol") or ""),
                    side=str(o.get("side") or ""),
                    position_side=str(o.get("positionSide") or "BOTH"),
                    order_type=str(o.get("orderType") or o.get("type") or ""),
                    quantity=float(o.get("quantity") or o.get("qty") or o.get("executedQty") or 0),
                    trigger_price=float(o.get("triggerPrice") or 0),
                    status=str(o.get("algoStatus") or o.get("status") or ""),
                    created_at=str(o.get("createTime") or o.get("bookTime") or o.get("time") or ""),
                ))
            except Exception:
                continue

    # 2. Merge DB-stored conditional orders when Algo Order openOrders API is unavailable.
    _TPSL_TYPES = {"TAKE_PROFIT_MARKET", "STOP_MARKET"}
    db_rows = db_module.query_orders(user_id=user_id, status="NEW", limit=500)
    for row in db_rows:
        if str(row.get("order_type") or "") not in _TPSL_TYPES:
            continue
        exchange_id = row.get("exchange_order_id")
        try:
            algo_id = int(exchange_id) if exchange_id else 0
        except (ValueError, TypeError):
            algo_id = 0
        # Skip if already returned from Binance
        if algo_id and algo_id in seen_algo_ids:
            continue
        order_type = str(row.get("order_type") or "")
        side = str(row.get("side") or "")
        # TP uses price as trigger; SL uses stop_price
        if order_type == "TAKE_PROFIT_MARKET":
            trigger = float(row["price"] or 0) if row.get("price") is not None else 0.0
        else:
            trigger = float(row["stop_price"] or 0) if row.get("stop_price") is not None else 0.0

        if trigger <= 0 and client and algo_id:
            try:
                algo_detail = await asyncio.to_thread(client.get_algo_order, algo_id=algo_id)
            except Exception:
                algo_detail = None
            if algo_detail:
                trigger = float(algo_detail.get("triggerPrice") or trigger or 0)
                order_type = str(algo_detail.get("orderType") or order_type or "")

        trade_direction = str(row.get("trade_direction") or "").upper()
        if trade_direction == "OPEN":
            position_side = "LONG" if side == "BUY" else "SHORT"
        elif trade_direction == "CLOSE":
            position_side = "SHORT" if side == "BUY" else "LONG"
        else:
            position_side = "SHORT" if side == "BUY" else "LONG"
        result.append(ConditionalOrderOut(
            algo_id=algo_id,
            symbol=str(row.get("symbol") or ""),
            side=side,
            position_side=position_side,
            order_type=order_type,
            quantity=float(row.get("quantity") or 0),
            trigger_price=trigger,
            status=str(row.get("status") or "NEW"),
            created_at=str(row.get("created_at") or ""),
        ))

    return result


class CancelConditionalOrderRequest(BaseModel):
    algo_id: int


@router.post("/conditional/cancel")
async def cancel_conditional_order(body: CancelConditionalOrderRequest, user: dict = Depends(get_current_user)):
    """Cancel an open conditional (algo) order on Binance."""
    username = user["username"]
    user_id = int(user["sub"]) if user["role"] != "admin" else None
    api_key = cfg.get_api_key(username)
    api_secret = cfg.get_api_secret(username)
    if not api_key or not api_secret:
        raise HTTPException(status_code=400, detail="No API credentials configured")
    testnet = cfg.is_testnet(username)
    client = FuturesBinanceClient(api_key=api_key, secret_key=api_secret, testnet=testnet)
    order_row = next(
        (
            row for row in db_module.query_orders(user_id=user_id, status="NEW", limit=500)
            if str(row.get("exchange_order_id") or "") == str(body.algo_id)
        ),
        None,
    )
    symbol = str(order_row.get("symbol") or "") if order_row else None
    _log.info("Cancel conditional order request: user=%s algo_id=%s symbol=%s", username, body.algo_id, symbol)
    result = await asyncio.to_thread(client.cancel_algo_order, body.algo_id, None, symbol)
    if not result:
        _log.warning("Cancel conditional order failed: user=%s algo_id=%s symbol=%s", username, body.algo_id, symbol)
        raise HTTPException(status_code=502, detail="Failed to cancel algo order on Binance")
    _log.info("Cancel conditional order success: user=%s algo_id=%s symbol=%s result=%s", username, body.algo_id, symbol, result)
    if order_row:
        db_module.update_order_status(int(order_row["id"]), "CANCELED")
    return {"ok": True}

