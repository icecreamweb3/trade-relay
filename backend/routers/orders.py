"""
Orders router: submit orders, list active/history.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import asyncio
import time
from threading import Lock
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from trade_relay import database as db_module
from trade_relay import config as cfg
from trade_relay.auth.manager import Session
from trade_relay.trading.order_manager import submit_order
from trade_relay.trading.close_trade_sync import sync_filled_order_trade_details
from trade_relay.exchange.binance_client import BinanceClient as FuturesBinanceClient
from backend.routers.auth import get_current_user, require_admin
from backend.logger import get_logger
from backend.time_utils import serialize_utc_timestamp_required

router = APIRouter(prefix="/api/orders", tags=["orders"])
_log = get_logger(__name__)
_RECENT_FILLS_CACHE_TTL = 2.0
_recent_fills_cache_lock = Lock()
_recent_fills_cache: tuple[float, list["OrderOut"]] | None = None


# ── Schemas ───────────────────────────────────────────────────────────────────

class OrderRequest(BaseModel):
    symbol: str
    side: str          # BUY | SELL
    order_type: str    # LIMIT | MARKET | STOP | STOP_MARKET
    quantity: float
    price: Optional[float] = None
    stop_price: Optional[float] = None   # trigger price for conditional orders
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    post_only: bool = False
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
    realized_pnl: Optional[float] = None
    commission: Optional[float] = None
    commission_asset: Optional[str] = None
    algo_id: Optional[str] = None
    algo_client_id: Optional[str] = None
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
        realized_pnl=float(r["realized_pnl"]) if r.get("realized_pnl") is not None else None,
        commission=float(r["commission"]) if r.get("commission") is not None else None,
        commission_asset=str(r["commission_asset"]) if r.get("commission_asset") is not None else None,
        algo_id=str(r["algo_id"]) if r.get("algo_id") is not None else None,
        algo_client_id=str(r["algo_client_id"]) if r.get("algo_client_id") is not None else None,
        exchange_order_id=r.get("exchange_order_id"),
        error_message=r.get("error_message"),
        created_at=serialize_utc_timestamp_required(r.get("created_at")),
    )


def _recent_fill_to_out(r: dict) -> OrderOut:
    return OrderOut(
        id=0,
        username=r["username"],
        symbol=r["symbol"],
        side=r["side"],
        order_type=str(r.get("order_type") or ""),
        trade_direction=str(r["trade_direction"]).upper() if r.get("trade_direction") else None,
        quantity=float(r.get("filled_qty") or 0),
        price=float(r["price"]) if r.get("price") else None,
        order_category=str(r.get("order_category") or "Basic"),
        status="FILLED",
        filled_qty=float(r.get("filled_qty") or 0),
        avg_price=float(r["avg_price"]) if r.get("avg_price") is not None else None,
        realized_pnl=float(r["realized_pnl"]) if r.get("realized_pnl") is not None else None,
        commission=float(r["commission"]) if r.get("commission") is not None else None,
        commission_asset=str(r["commission_asset"]) if r.get("commission_asset") is not None else None,
        algo_id=None,
        algo_client_id=None,
        exchange_order_id=None,
        error_message=None,
        created_at=serialize_utc_timestamp_required(r.get("created_at")),
    )


def _get_recent_fills_cached() -> list[OrderOut]:
    global _recent_fills_cache

    now = time.monotonic()
    with _recent_fills_cache_lock:
        cached = _recent_fills_cache
        if cached and now - cached[0] < _RECENT_FILLS_CACHE_TTL:
            return cached[1]

    rows = db_module.get_recent_platform_trades(limit=50)
    result = [_recent_fill_to_out(r) for r in rows]

    with _recent_fills_cache_lock:
        _recent_fills_cache = (time.monotonic(), result)
    return result


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("")
async def place_order(body: OrderRequest, user: dict = Depends(get_current_user)):
    _log.info(
        "[ORDER_FLOW] phase=request username=%s symbol=%s side=%s type=%s qty=%s price=%s pos_dir=%s",
        user["username"], body.symbol, body.side, body.order_type, body.quantity, body.price, body.position_direction,
    )
    session = Session(int(user["sub"]), user["username"], user["role"])
    result = await submit_order(
        session,
        body.symbol,
        body.side,
        body.order_type,
        body.quantity,
        body.price,
        body.stop_price,
        body.tp_price,
        body.sl_price,
        body.post_only,
        body.leverage,
        body.position_direction,
    )
    if not result.success:
        _log.warning("[ORDER_FLOW] phase=failed username=%s reason=%s", user["username"], result.message)
        raise HTTPException(status_code=400, detail=result.message)
    _log.info("[ORDER_FLOW] phase=success order_id=%s username=%s", result.order_id, user["username"])
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
    return _get_recent_fills_cached()


class CancelOrderRequest(BaseModel):
    symbol: str
    exchange_order_id: str


class AmendOrderRequest(BaseModel):
    quantity: float
    price: float


def _get_symbol_leverage(client: FuturesBinanceClient, symbol: str) -> int:
    try:
        rows = client.get_position_information(symbol=symbol)
    except Exception:
        return 10

    for row in rows or []:
        row_symbol = str(row.get("symbol") or "").upper()
        if row_symbol and row_symbol != symbol.upper():
            continue
        try:
            leverage = int(float(row.get("leverage") or 0))
        except (TypeError, ValueError):
            leverage = 0
        if leverage > 0:
            return leverage
    return 10


@router.post("/{order_id:int}/cancel")
async def cancel_order(order_id: int, body: CancelOrderRequest, user: dict = Depends(get_current_user)):
    """Cancel an open order on Binance and mark it CANCELED in DB."""
    username = user["username"]
    _log.info(
        "[ORDER_FLOW] phase=cancel_request username=%s order_id=%s symbol=%s exchange_order_id=%s",
        username,
        order_id,
        body.symbol,
        body.exchange_order_id,
    )

    order_row = db_module.get_order_by_id(order_id)
    if not order_row:
        raise HTTPException(status_code=404, detail="Order not found")
    if user["role"] != "admin" and order_row.get("username") != username:
        raise HTTPException(status_code=404, detail="Order not found")
    if order_row.get("exchange_order_id") != body.exchange_order_id:
        raise HTTPException(status_code=400, detail="exchange_order_id mismatch")

    # Determine whose credentials to use (admin acts on behalf of order owner)
    target_username = order_row["username"]

    mock = cfg.is_mock_mode(target_username)
    if mock:
        db_module.update_order_status(order_id, "CANCELED")
        _log.info("[ORDER_FLOW] phase=cancel_mock_success order_id=%s username=%s", order_id, username)
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
        _log.info("[ORDER_FLOW] phase=cancel_exchange_success order_id=%s username=%s result=%s", order_id, username, result)
    except Exception as exc:
        _log.warning("[ORDER_FLOW] phase=cancel_exchange_error order_id=%s username=%s error=%s", order_id, username, exc)
        raise HTTPException(status_code=502, detail=f"Binance cancel failed: {exc}")

    db_module.update_order_status(order_id, "CANCELED")
    _log.info("[ORDER_FLOW] phase=cancel_db_success order_id=%s username=%s", order_id, username)
    return {"ok": True}


@router.post("/{order_id:int}/amend")
async def amend_order(order_id: int, body: AmendOrderRequest, user: dict = Depends(get_current_user)):
    """Amend an open basic LIMIT order by canceling it and placing a replacement order."""
    username = user["username"]
    order_row = db_module.get_order_by_id(order_id)
    if not order_row:
        raise HTTPException(status_code=404, detail="Order not found")
    if user["role"] != "admin" and order_row.get("username") != username:
        raise HTTPException(status_code=404, detail="Order not found")

    quantity = float(body.quantity)
    price = float(body.price)
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be greater than 0")
    if price <= 0:
        raise HTTPException(status_code=400, detail="price must be greater than 0")

    order_type = str(order_row.get("order_type") or "").upper()
    order_category = str(order_row.get("order_category") or "Basic")
    status = str(order_row.get("status") or "").upper()
    exchange_order_id = str(order_row.get("exchange_order_id") or "").strip()
    filled_qty = float(order_row.get("filled_qty") or 0)

    if order_category not in {"Basic", ""}:
        raise HTTPException(status_code=400, detail="Only basic orders can be amended")
    if order_type != "LIMIT":
        raise HTTPException(status_code=400, detail="Only LIMIT orders support amend")
    if status not in {"NEW", "PARTIALLY_FILLED"}:
        raise HTTPException(status_code=400, detail="Only open orders can be amended")
    if not exchange_order_id:
        raise HTTPException(status_code=400, detail="Order has no exchange_order_id")

    replacement_quantity = quantity - filled_qty
    if replacement_quantity <= 0:
        raise HTTPException(status_code=400, detail=f"quantity must be greater than filled quantity {filled_qty}")

    target_username = str(order_row.get("username") or username)
    target_user_id = int(order_row.get("user_id") or user["sub"])
    mock = cfg.is_mock_mode(target_username)

    _log.info(
        "[ORDER_FLOW] phase=amend_request username=%s order_id=%s symbol=%s old_qty=%s filled_qty=%s target_qty=%s replacement_qty=%s old_price=%s new_price=%s",
        username,
        order_id,
        order_row.get("symbol"),
        order_row.get("quantity"),
        filled_qty,
        quantity,
        replacement_quantity,
        order_row.get("price"),
        price,
    )

    if mock:
        db_module.update_order_status(order_id, "CANCELED")
        session = Session(target_user_id, target_username, user["role"])
        result = await submit_order(
            session,
            str(order_row.get("symbol") or ""),
            str(order_row.get("side") or ""),
            order_type,
            replacement_quantity,
            price,
            float(order_row["stop_price"]) if order_row.get("stop_price") is not None else None,
            None,
            None,
            bool(order_row.get("post_only") or False),
            10,
            str(order_row.get("trade_direction") or "OPEN"),
        )
        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)
        return {"ok": True, "order_id": result.order_id, "message": result.message}

    api_key = cfg.get_api_key(target_username)
    api_secret = cfg.get_api_secret(target_username)
    if not api_key or not api_secret:
        raise HTTPException(status_code=400, detail="No API credentials configured")

    testnet = cfg.is_testnet(target_username)
    symbol = str(order_row.get("symbol") or "").upper()
    try:
        client = FuturesBinanceClient(api_key=api_key, secret_key=api_secret, testnet=testnet)
        cancel_result = await asyncio.to_thread(client.cancel_order, symbol, exchange_order_id)
        if isinstance(cancel_result, dict) and cancel_result.get("error"):
            raise RuntimeError(str(cancel_result.get("error_message") or cancel_result))
    except Exception as exc:
        _log.warning(
            "[ORDER_FLOW] phase=amend_cancel_error username=%s order_id=%s error=%s",
            username,
            order_id,
            exc,
        )
        raise HTTPException(status_code=502, detail=f"Binance cancel failed: {exc}")

    db_module.update_order_status(order_id, "CANCELED")

    leverage = await asyncio.to_thread(_get_symbol_leverage, client, symbol)
    session = Session(target_user_id, target_username, user["role"])
    result = await submit_order(
        session,
        symbol,
        str(order_row.get("side") or ""),
        order_type,
        replacement_quantity,
        price,
        float(order_row["stop_price"]) if order_row.get("stop_price") is not None else None,
        None,
        None,
        bool(order_row.get("post_only") or False),
        leverage,
        str(order_row.get("trade_direction") or "OPEN"),
    )
    if not result.success:
        _log.warning(
            "[ORDER_FLOW] phase=amend_replace_failed username=%s order_id=%s symbol=%s reason=%s",
            username,
            order_id,
            symbol,
            result.message,
        )
        raise HTTPException(status_code=502, detail=f"Order canceled but replacement failed: {result.message}")

    _log.info(
        "[ORDER_FLOW] phase=amend_success username=%s old_order_id=%s new_order_id=%s symbol=%s",
        username,
        order_id,
        result.order_id,
        symbol,
    )
    return {"ok": True, "order_id": result.order_id, "message": result.message}


class ConditionalOrderOut(BaseModel):
    algo_id: int
    algo_client_id: Optional[str] = None
    symbol: str
    side: str
    position_side: str
    order_type: str       # TAKE_PROFIT_MARKET | STOP_MARKET
    quantity: float
    trigger_price: float
    status: str
    created_at: str
    trade_direction: Optional[str] = None
    exchange_order_id: Optional[str] = None
    client_order_id: Optional[str] = None


def _derive_conditional_position_side(side: str, trade_direction: Optional[str]) -> str:
    side_upper = str(side or "").upper()
    direction_upper = str(trade_direction or "").upper()
    if direction_upper == "OPEN":
        return "LONG" if side_upper == "BUY" else "SHORT"
    if direction_upper == "CLOSE":
        return "SHORT" if side_upper == "BUY" else "LONG"
    return "SHORT" if side_upper == "BUY" else "LONG"


def _map_algo_status_to_db_status(algo_status: Optional[str]) -> Optional[str]:
    normalized = str(algo_status or "").upper()
    if not normalized:
        return None
    if normalized in {"NEW", "WORKING", "TRIGGERING", "TRIGGERED", "PARTIALLY_FILLED"}:
        return "NEW"
    if normalized in {"FINISHED", "FILLED"}:
        return "FILLED"
    if normalized in {"CANCELED", "EXPIRED", "REJECTED"}:
        return normalized
    return normalized


def _resolve_close_fill_entry_context(row: dict) -> tuple[int | None, float | None, str | None]:
    user_id = int(row.get("user_id") or 0)
    if user_id <= 0:
        return None, None, None

    symbol = str(row.get("symbol") or "").upper()
    position_side = _derive_conditional_position_side(
        str(row.get("side") or ""),
        str(row.get("trade_direction") or ""),
    )
    requested_position_id = int(row["position_id"]) if row.get("position_id") else None

    positions = db_module.get_positions(user_id=user_id)
    matched_position = None
    if requested_position_id is not None:
        matched_position = next(
            (position for position in positions if int(position.get("id") or 0) == requested_position_id),
            None,
        )
    if matched_position is None:
        matched_position = next(
            (
                position for position in positions
                if str(position.get("symbol") or "").upper() == symbol
                and str(position.get("position_side") or "").upper() == position_side
            ),
            None,
        )
    if matched_position is not None:
        entry_price = float(matched_position.get("avg_entry_price") or 0)
        if entry_price > 0:
            return int(matched_position.get("id") or requested_position_id or 0) or requested_position_id, entry_price, position_side

    filled_rows = db_module.query_orders(user_id=user_id, status="FILLED", limit=500)
    for candidate in filled_rows:
        if str(candidate.get("trade_direction") or "").upper() != "OPEN":
            continue
        if str(candidate.get("symbol") or "").upper() != symbol:
            continue
        candidate_side = _derive_conditional_position_side(
            str(candidate.get("side") or ""),
            str(candidate.get("trade_direction") or ""),
        )
        if candidate_side != position_side:
            continue
        candidate_position_id = int(candidate["position_id"]) if candidate.get("position_id") else None
        if requested_position_id is not None and candidate_position_id not in {None, requested_position_id}:
            continue
        entry_price = float(candidate.get("avg_price") or 0)
        if entry_price > 0:
            return candidate_position_id or requested_position_id, entry_price, position_side

    return requested_position_id, None, position_side


def _record_close_fill_history_from_conditional(row: dict, filled_qty: float, avg_price: float) -> None:
    if str(row.get("trade_direction") or "").upper() != "CLOSE":
        return
    if filled_qty <= 0 or avg_price <= 0:
        return

    user_id = int(row.get("user_id") or 0)
    username = str(row.get("username") or "")
    symbol = str(row.get("symbol") or "").upper()
    if user_id <= 0 or not username or not symbol:
        return

    position_id, entry_price, position_side = _resolve_close_fill_entry_context(row)
    if entry_price is None or entry_price <= 0 or position_side not in {"LONG", "SHORT"}:
        _log.warning(
            "[ORDER_FLOW] phase=conditional_fill_history_skipped order_id=%s reason=missing_entry_context symbol=%s user_id=%s",
            row.get("id"),
            symbol,
            user_id,
        )
        return

    realized_pnl = (avg_price - entry_price) * filled_qty if position_side == "LONG" else (entry_price - avg_price) * filled_qty
    db_module.add_position_history(
        user_id=user_id,
        username=username,
        symbol=symbol,
        side=position_side,
        entry_price=entry_price,
        close_price=avg_price,
        quantity=filled_qty,
        realized_pnl=realized_pnl,
        commission=0.0,
        position_id=position_id,
    )
    _log.info(
        "[ORDER_FLOW] phase=conditional_fill_history_created order_id=%s symbol=%s side=%s qty=%s entry=%s close=%s",
        row.get("id"),
        symbol,
        position_side,
        filled_qty,
        entry_price,
        avg_price,
    )


@router.get("/conditional", response_model=list[ConditionalOrderOut])
async def get_conditional_orders(user: dict = Depends(get_current_user)):
    """Fetch open conditional (algo) orders — merged from Binance Algo API and local DB."""
    username = user["username"]
    user_id = int(user["sub"]) if user["role"] != "admin" else None

    result: list[ConditionalOrderOut] = []
    seen_algo_ids: set[int] = set()
    client: FuturesBinanceClient | None = None
    _TPSL_TYPES = {"TAKE_PROFIT_MARKET", "STOP_MARKET"}
    db_rows = db_module.query_orders(user_id=user_id, status="NEW", limit=500)
    conditional_rows = [row for row in db_rows if str(row.get("order_type") or "") in _TPSL_TYPES]
    rows_by_algo_id = {
        str(row.get("algo_id") or row.get("exchange_order_id")): row
        for row in conditional_rows
        if row.get("algo_id") or row.get("exchange_order_id")
    }
    rows_by_client_id = {
        str(row.get("algo_client_id") or row.get("client_order_id")): row
        for row in conditional_rows
        if row.get("algo_client_id") or row.get("client_order_id")
    }

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
                client_algo_id = str(o.get("clientAlgoId") or o.get("clientOrderId") or "") or None
                local_row = rows_by_algo_id.get(str(algo_id)) if algo_id else None
                if local_row is None and client_algo_id:
                    local_row = rows_by_client_id.get(client_algo_id)
                trade_direction = str(local_row.get("trade_direction") or "").upper() if local_row else None
                order_type = str(o.get("orderType") or o.get("type") or "")
                trigger_price = float(o.get("triggerPrice") or (local_row.get("stop_price") if local_row and str(local_row.get("order_type") or "") == "STOP_MARKET" else local_row.get("price") if local_row else 0) or 0)
                if local_row:
                    backfill_fields = {}
                    if algo_id and not local_row.get("algo_id"):
                        backfill_fields["algo_id"] = str(algo_id)
                    if client_algo_id and not local_row.get("algo_client_id"):
                        backfill_fields["algo_client_id"] = client_algo_id
                    if trigger_price > 0:
                        if order_type == "STOP_MARKET" and local_row.get("stop_price") is None:
                            backfill_fields["stop_price"] = trigger_price
                        if order_type == "TAKE_PROFIT_MARKET" and local_row.get("price") is None:
                            backfill_fields["price"] = trigger_price
                    if backfill_fields:
                        db_module.update_order_metadata(int(local_row["id"]), **backfill_fields)
                seen_algo_ids.add(algo_id)
                result.append(ConditionalOrderOut(
                    algo_id=algo_id,
                    algo_client_id=str(local_row.get("algo_client_id") or client_algo_id or "") if local_row or client_algo_id else None,
                    symbol=str(o.get("symbol") or ""),
                    side=str(o.get("side") or ""),
                    position_side=str(o.get("positionSide") or _derive_conditional_position_side(o.get("side") or "", trade_direction)),
                    order_type=order_type,
                    quantity=float(o.get("quantity") or o.get("qty") or o.get("executedQty") or (local_row.get("quantity") if local_row else 0) or 0),
                    trigger_price=trigger_price,
                    status=str(o.get("algoStatus") or o.get("status") or ""),
                    created_at=serialize_utc_timestamp_required(o.get("createTime") or o.get("bookTime") or o.get("time") or (local_row.get("created_at") if local_row else None)),
                    trade_direction=trade_direction,
                    exchange_order_id=str(local_row.get("exchange_order_id") or "") if local_row and local_row.get("exchange_order_id") else None,
                    client_order_id=str(local_row.get("client_order_id") or "") if local_row and local_row.get("client_order_id") else None,
                ))
            except Exception:
                continue

    # 2. Merge DB-stored conditional orders when Algo Order openOrders API is unavailable.
    for row in conditional_rows:
        exchange_id = row.get("algo_id") or row.get("exchange_order_id")
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

        algo_detail = None
        if client and (algo_id or row.get("algo_client_id") or row.get("client_order_id")):
            try:
                algo_detail = await asyncio.to_thread(
                    client.get_algo_order,
                    algo_id=algo_id or None,
                    client_algo_id=None if algo_id else str(row.get("algo_client_id") or row.get("client_order_id") or "") or None,
                )
            except Exception:
                algo_detail = None

        if isinstance(algo_detail, dict):
            algo_status = str(algo_detail.get("algoStatus") or algo_detail.get("status") or "")
            db_status = _map_algo_status_to_db_status(algo_status)
            actual_order_id = str(algo_detail.get("actualOrderId") or algo_detail.get("orderId") or "").strip() or None
            client_algo_id = str(algo_detail.get("clientAlgoId") or "").strip() or None
            if actual_order_id and actual_order_id != str(row.get("exchange_order_id") or ""):
                db_module.update_order_metadata(int(row["id"]), exchange_order_id=actual_order_id)
                row = {**row, "exchange_order_id": actual_order_id}
            if client_algo_id and client_algo_id != str(row.get("algo_client_id") or ""):
                db_module.update_order_metadata(int(row["id"]), algo_client_id=client_algo_id)
                row = {**row, "algo_client_id": client_algo_id}
            if db_status and db_status != "NEW":
                filled_qty = float(algo_detail.get("quantity") or row.get("quantity") or 0) if db_status == "FILLED" else 0.0
                avg_price = float(algo_detail.get("actualPrice") or 0) if db_status == "FILLED" and float(algo_detail.get("actualPrice") or 0) > 0 else 0.0
                db_module.update_order_status(
                    int(row["id"]),
                    db_status,
                    filled_qty=filled_qty if db_status == "FILLED" else None,
                    avg_price=avg_price if db_status == "FILLED" and avg_price > 0 else None,
                )
                if db_status == "FILLED":
                    if str(row.get("trade_direction") or "").upper() == "CLOSE":
                        _record_close_fill_history_from_conditional(row, filled_qty, avg_price)
                    sync_filled_order_trade_details(username=username, client=client, order_row={**row, "status": db_status, "exchange_order_id": actual_order_id or row.get("exchange_order_id")})
                continue
            if trigger <= 0:
                trigger = float(algo_detail.get("triggerPrice") or trigger or 0)
            if order_type == str(row.get("order_type") or ""):
                order_type = str(algo_detail.get("orderType") or order_type or "")

        if isinstance(algo_detail, dict) and algo_detail.get("_order_not_found"):
            db_module.update_order_status(int(row["id"]), "EXPIRED")
            continue

        trade_direction = str(row.get("trade_direction") or "").upper()
        position_side = _derive_conditional_position_side(side, trade_direction)
        result.append(ConditionalOrderOut(
            algo_id=algo_id,
            algo_client_id=str(row.get("algo_client_id") or "") or None,
            symbol=str(row.get("symbol") or ""),
            side=side,
            position_side=position_side,
            order_type=order_type,
            quantity=float(row.get("quantity") or 0),
            trigger_price=trigger,
            status=str(row.get("status") or "NEW"),
            created_at=serialize_utc_timestamp_required(row.get("created_at")),
            trade_direction=trade_direction or None,
            exchange_order_id=str(row.get("exchange_order_id") or "") or None,
            client_order_id=str(row.get("client_order_id") or "") or None,
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
            if str(row.get("algo_id") or row.get("exchange_order_id") or "") == str(body.algo_id)
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

