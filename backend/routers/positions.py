"""
Positions router: current positions, open orders, order history, trade history.
"""
import asyncio
import sys, os
import threading
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from typing import Optional

from trade_relay import database as db_module
from trade_relay import config as cfg_module
from trade_relay.trading.order_status_stream import ensure_user_order_status_stream, register_user_stream_listener, unregister_user_stream_listener, sync_initial_positions_for_user
from trade_relay.trading.tpsl_service import place_tp_sl_orders, validate_tpsl_prices
from backend.routers.auth import decode_token, get_current_user
from backend.logger import get_logger
from backend.time_utils import serialize_utc_timestamp, serialize_utc_timestamp_required

router = APIRouter(prefix="/api/positions", tags=["positions"])
_log = get_logger(__name__)

# In-memory TP/SL store: position_id → (tp_price or None, sl_price or None)
_tpsl_store: dict[int, tuple[float | None, float | None]] = {}
_tpsl_store_lock = __import__('threading').Lock()


class PositionHistoryOut(BaseModel):
    id: int
    username: str
    symbol: str
    side: str
    position_mode: str
    entry_price: float
    close_price: float
    quantity: float
    realized_pnl: float
    commission: float
    commission_asset: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None

# Per-user TTL cache: user_id (None = admin) → (timestamp, result)
_positions_cache: dict[int | None, tuple[float, list]] = {}
_POSITIONS_CACHE_TTL = 0.5  # seconds — short enough that an account_update fetch always sees fresh DB data
_startup_position_sync_inflight: set[str] = set()
_startup_position_sync_lock = threading.Lock()
_STARTUP_POSITION_SYNC_DELAY_SECONDS = 3.0


def _schedule_initial_position_sync(username: str, user_id: int | None, api_key: str, api_secret: str, testnet: bool) -> None:
    with _startup_position_sync_lock:
        if username in _startup_position_sync_inflight:
            return
        _startup_position_sync_inflight.add(username)

    def _worker() -> None:
        try:
            time.sleep(_STARTUP_POSITION_SYNC_DELAY_SECONDS)
            sync_initial_positions_for_user(username, api_key, api_secret, testnet)
            if user_id is not None:
                _positions_cache.pop(user_id, None)
        except Exception:
            _log.exception("Deferred initial position sync failed for user=%s", username)
        finally:
            with _startup_position_sync_lock:
                _startup_position_sync_inflight.discard(username)

    threading.Thread(
        target=_worker,
        daemon=True,
        name=f"positions-startup-sync-{username}",
    ).start()


class PositionOut(BaseModel):
    id: int
    symbol: str
    side: str
    position_mode: str
    quantity: float
    entry_price: Optional[float]
    liquidation_price: Optional[float]
    unrealized_pnl: Optional[float]
    leverage: int
    margin_type: str
    margin: Optional[float]
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None


def _derive_conditional_position_side(side: str, trade_direction: str | None) -> str:
    side_upper = str(side or "").upper()
    direction_upper = str(trade_direction or "").upper()
    if direction_upper == "OPEN":
        return "LONG" if side_upper == "BUY" else "SHORT"
    if direction_upper == "CLOSE":
        return "SHORT" if side_upper == "BUY" else "LONG"
    return "SHORT" if side_upper == "BUY" else "LONG"


def _load_persisted_tpsl(user_id: int | None) -> tuple[dict[int, tuple[float | None, float | None]], dict[tuple[str, str], tuple[float | None, float | None]]]:
    by_position_id: dict[int, tuple[float | None, float | None]] = {}
    by_symbol_side: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    if user_id is None:
        return by_position_id, by_symbol_side

    rows = db_module.query_orders(user_id=user_id, status="NEW", limit=500)
    for row in rows:
        order_type = str(row.get("order_type") or "").upper()
        if order_type not in {"TAKE_PROFIT_MARKET", "STOP_MARKET"}:
            continue

        symbol = str(row.get("symbol") or "").upper()
        position_side = _derive_conditional_position_side(
            str(row.get("side") or ""),
            str(row.get("trade_direction") or ""),
        )
        if not symbol or position_side not in {"LONG", "SHORT"}:
            continue

        tp_price: float | None = None
        sl_price: float | None = None
        if order_type == "TAKE_PROFIT_MARKET":
            tp_price = float(row["price"]) if row.get("price") is not None else None
        else:
            sl_price = float(row["stop_price"]) if row.get("stop_price") is not None else None

        position_id = row.get("position_id")
        if position_id:
            current_tp, current_sl = by_position_id.get(int(position_id), (None, None))
            by_position_id[int(position_id)] = (
                current_tp if current_tp is not None else tp_price,
                current_sl if current_sl is not None else sl_price,
            )

        current_tp, current_sl = by_symbol_side.get((symbol, position_side), (None, None))
        by_symbol_side[(symbol, position_side)] = (
            current_tp if current_tp is not None else tp_price,
            current_sl if current_sl is not None else sl_price,
        )

    return by_position_id, by_symbol_side


def _db_positions(user_id: int | None) -> list[PositionOut]:
    rows = db_module.get_positions(user_id=user_id)
    persisted_by_position_id, persisted_by_symbol_side = _load_persisted_tpsl(user_id)
    positions: list[PositionOut] = []
    for index, row in enumerate(rows, start=1):
        pos_id = int(row.get("id") or index)
        symbol = str(row.get("symbol", "") or "").upper()
        side = str(row.get("position_side", "") or "").upper()
        tp, sl = persisted_by_position_id.get(pos_id) or persisted_by_symbol_side.get((symbol, side)) or (None, None)
        with _tpsl_store_lock:
            memory_tp, memory_sl = _tpsl_store.get(pos_id, (None, None))

        has_persisted_tpsl = tp is not None or sl is not None
        if has_persisted_tpsl:
            if memory_tp is not None:
                tp = memory_tp
            if memory_sl is not None:
                sl = memory_sl
        elif memory_tp is not None or memory_sl is not None:
            with _tpsl_store_lock:
                _tpsl_store.pop(pos_id, None)

        positions.append(
            PositionOut(
                id=pos_id,
                symbol=symbol,
                side=side,
                position_mode=str(row.get("position_mode", "") or "UNKNOWN").upper(),
                quantity=float(row["quantity"]),
                entry_price=float(row["avg_entry_price"]) if row.get("avg_entry_price") is not None else None,
                liquidation_price=float(row["liquidation_price"]) if row.get("liquidation_price") is not None else None,
                unrealized_pnl=float(row["unrealized_pnl"]) if row.get("unrealized_pnl") is not None else None,
                leverage=int(row.get("leverage") or 0),
                margin_type=str(row.get("margin_type", "") or "").upper(),
                margin=None,
                tp_price=tp,
                sl_price=sl,
            )
        )
    return positions


@router.post("/sync", response_model=list[PositionOut])
def sync_positions(user: dict = Depends(get_current_user)):
    """从 Binance 拉取最新持仓，写入数据库，并返回更新后的持仓列表。"""
    username = str(user.get("username") or "")
    _log.info("[POSITION_SYNC] phase=request username=%s", username)
    api_key = cfg_module.get_api_key(username)
    api_secret = cfg_module.get_api_secret(username)
    if api_key and api_secret:
        testnet = cfg_module.is_testnet(username)
        _log.info("[POSITION_SYNC] phase=exchange_sync username=%s testnet=%s", username, testnet)
        sync_initial_positions_for_user(username, api_key, api_secret, testnet)
        # Invalidate position cache so the subsequent read sees fresh data
        user_id = int(user["sub"]) if user["role"] != "admin" else None
        _positions_cache.pop(user_id, None)
    else:
        _log.warning("[POSITION_SYNC] phase=missing_credentials username=%s", username)
    user_id = int(user["sub"]) if user["role"] != "admin" else None
    result = _db_positions(user_id=user_id)
    _log.info("[POSITION_SYNC] phase=response username=%s positions=%s", username, len(result))
    return result


@router.get("", response_model=list[PositionOut])
def get_positions(user: dict = Depends(get_current_user)):
    user_id = int(user["sub"]) if user["role"] != "admin" else None
    now = time.monotonic()
    cached = _positions_cache.get(user_id)
    if cached and now - cached[0] < _POSITIONS_CACHE_TTL:
        _log.info("[POSITION_SYNC] phase=cache_hit user_id=%s positions=%s", user_id, len(cached[1]))
        return cached[1]
    result = _db_positions(user_id=user_id)
    _positions_cache[user_id] = (now, result)
    _log.info("[POSITION_SYNC] phase=cache_miss user_id=%s positions=%s", user_id, len(result))
    return result


class TpSlIn(BaseModel):
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None


@router.post("/{position_id}/tpsl")
def set_position_tpsl(
    position_id: int,
    body: TpSlIn,
    user: dict = Depends(get_current_user),
):
    """Set TP/SL orders for a position. Places orders on Binance and records prices."""
    username = str(user.get("username") or "")
    api_key = cfg_module.get_api_key(username)
    api_secret = cfg_module.get_api_secret(username)
    if not api_key or not api_secret:
        raise HTTPException(status_code=400, detail="No API credentials configured")

    # Find the position row to get symbol, side, quantity
    user_id = int(user["sub"]) if user["role"] != "admin" else None
    rows = db_module.get_positions(user_id=user_id)
    position_row = None
    for idx, row in enumerate(rows, start=1):
        rid = int(row.get("id") or idx)
        if rid == position_id:
            position_row = row
            break

    if position_row is None:
        raise HTTPException(status_code=404, detail="Position not found")

    symbol = str(position_row.get("symbol", "") or "").upper()
    position_side = str(position_row.get("position_side", "") or "").upper()  # LONG or SHORT
    position_mode = str(position_row.get("position_mode", "") or "UNKNOWN").upper()
    quantity = float(position_row.get("quantity") or 0)
    entry_price = float(position_row["avg_entry_price"]) if position_row.get("avg_entry_price") is not None else None

    validation_errors = validate_tpsl_prices(
        position_side=position_side,
        entry_price=entry_price,
        tp_price=body.tp_price,
        sl_price=body.sl_price,
    )
    if validation_errors:
        raise HTTPException(status_code=400, detail="; ".join(validation_errors))
    _log.info(
        "[POSITION_SYNC] phase=tpsl_validated user=%s pos=%d symbol=%s side=%s entry_price=%s tp=%s sl=%s",
        username, position_id, symbol, position_side, entry_price, body.tp_price, body.sl_price,
    )

    db_user_id = int(user["sub"])
    errors = place_tp_sl_orders(
        username=username,
        user_id=db_user_id,
        symbol=symbol,
        position_side=position_side,
        quantity=quantity,
        entry_price=entry_price,
        tp_price=body.tp_price,
        sl_price=body.sl_price,
        position_id=position_id,
        position_mode=position_mode,
    )
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    # Store the set prices in memory
    with _tpsl_store_lock:
        _tpsl_store[position_id] = (
            body.tp_price if body.tp_price and body.tp_price > 0 else None,
            body.sl_price if body.sl_price and body.sl_price > 0 else None,
        )
    # Invalidate position cache
    _positions_cache.pop(user_id, None)

    _log.info("[POSITION_SYNC] phase=tpsl_set user=%s pos=%d symbol=%s tp=%s sl=%s", username, position_id, symbol, body.tp_price, body.sl_price)
    return {"ok": True, "tp_price": body.tp_price, "sl_price": body.sl_price}


@router.websocket("/ws")
async def positions_ws(websocket: WebSocket, token: Optional[str] = Query(default=None)):
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")
        return

    user = decode_token(token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or expired token")
        return

    username = str(user.get("username") or "")
    user_id = int(user["sub"]) if user.get("role") != "admin" else None
    if not username:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token payload")
        return

    await websocket.accept()

    api_key = cfg_module.get_api_key(username)
    api_secret = cfg_module.get_api_secret(username)
    if api_key and api_secret:
        testnet = cfg_module.is_testnet(username)
        ensure_user_order_status_stream(username, api_key, api_secret, testnet)
        # Defer the initial Binance REST sync so first-screen DB reads can return immediately.
        _schedule_initial_position_sync(username, user_id, api_key, api_secret, testnet)

    queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def listener(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    register_user_stream_listener(username, listener)

    try:
        await websocket.send_json({"type": "connected"})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=3.0)
            except asyncio.TimeoutError:
                # Heartbeat every 3s: detects dead connections quickly
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(event)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        unregister_user_stream_listener(username, listener)


@router.get("/history", response_model=list[PositionHistoryOut])
def get_position_history(user: dict = Depends(get_current_user)):
    user_id = int(user["sub"]) if user["role"] != "admin" else None
    rows = db_module.get_position_history(user_id=user_id)
    return [
        PositionHistoryOut(
            id=int(r["id"]),
            username=str(r["username"]),
            symbol=str(r["symbol"]),
            side=str(r["side"]),
            position_mode=str(r.get("position_mode") or "UNKNOWN").upper(),
            entry_price=float(r["entry_price"]),
            close_price=float(r["close_price"]),
            quantity=float(r["quantity"]),
            realized_pnl=float(r["realized_pnl"]),
            commission=float(r["commission"]),
            commission_asset=str(r["commission_asset"]) if r.get("commission_asset") is not None else None,
            created_at=serialize_utc_timestamp_required(r.get("created_at")),
            updated_at=serialize_utc_timestamp(r.get("updated_at")),
        )
        for r in rows
    ]


@router.post("/history", response_model=PositionHistoryOut)
def add_position_history(body: PositionHistoryOut, user: dict = Depends(get_current_user)):
    """手动新增一条持仓历史记录（供管理员或测试使用）。"""
    user_id = int(user["sub"])
    username = str(user["username"])
    new_id = db_module.add_position_history(
        user_id=user_id,
        username=username,
        symbol=body.symbol,
        side=body.side,
        entry_price=body.entry_price,
        close_price=body.close_price,
        quantity=body.quantity,
        realized_pnl=body.realized_pnl,
        commission=body.commission,
        commission_asset=body.commission_asset,
        position_mode=body.position_mode,
    )
    rows = db_module.get_position_history(user_id=user_id, limit=1)
    r = next((x for x in rows if x["id"] == new_id), rows[0])
    return PositionHistoryOut(
        id=int(r["id"]),
        username=str(r["username"]),
        symbol=str(r["symbol"]),
        side=str(r["side"]),
        position_mode=str(r.get("position_mode") or "UNKNOWN").upper(),
        entry_price=float(r["entry_price"]),
        close_price=float(r["close_price"]),
        quantity=float(r["quantity"]),
        realized_pnl=float(r["realized_pnl"]),
        commission=float(r["commission"]),
        commission_asset=str(r["commission_asset"]) if r.get("commission_asset") is not None else None,
        created_at=serialize_utc_timestamp_required(r.get("created_at")),
        updated_at=serialize_utc_timestamp(r.get("updated_at")),
    )
