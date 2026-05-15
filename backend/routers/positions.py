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
from trade_relay.trading.order_status_stream import ensure_user_order_status_stream, notify_user_stream_event, register_user_stream_listener, unregister_user_stream_listener, sync_initial_positions_for_user
from backend.routers.auth import decode_token, get_current_user
from backend.logger import get_logger

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
    entry_price: float
    close_price: float
    quantity: float
    realized_pnl: float
    commission: float
    created_at: str

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
            notify_user_stream_event(
                username,
                {"type": "order_update", "event": "REST_SYNC"},
                force=True,
            )
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
    quantity: float
    entry_price: Optional[float]
    liquidation_price: Optional[float]
    unrealized_pnl: Optional[float]
    leverage: int
    margin_type: str
    margin: Optional[float]
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None


def _db_positions(user_id: int | None) -> list[PositionOut]:
    rows = db_module.get_positions(user_id=user_id)
    positions: list[PositionOut] = []
    for index, row in enumerate(rows, start=1):
        pos_id = int(row.get("id") or index)
        with _tpsl_store_lock:
            tp, sl = _tpsl_store.get(pos_id, (None, None))
        positions.append(
            PositionOut(
                id=pos_id,
                symbol=str(row.get("symbol", "") or ""),
                side=str(row.get("position_side", "") or "").upper(),
                quantity=float(row["quantity"]),
                entry_price=float(row["avg_entry_price"]) if row.get("avg_entry_price") is not None else None,
                liquidation_price=None,
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
    api_key = cfg_module.get_api_key(username)
    api_secret = cfg_module.get_api_secret(username)
    if api_key and api_secret:
        testnet = cfg_module.is_testnet(username)
        sync_initial_positions_for_user(username, api_key, api_secret, testnet)
        # Invalidate position cache so the subsequent read sees fresh data
        user_id = int(user["sub"]) if user["role"] != "admin" else None
        _positions_cache.pop(user_id, None)
    user_id = int(user["sub"]) if user["role"] != "admin" else None
    return _db_positions(user_id=user_id)


@router.get("", response_model=list[PositionOut])
def get_positions(user: dict = Depends(get_current_user)):
    user_id = int(user["sub"]) if user["role"] != "admin" else None
    now = time.monotonic()
    cached = _positions_cache.get(user_id)
    if cached and now - cached[0] < _POSITIONS_CACHE_TTL:
        return cached[1]
    result = _db_positions(user_id=user_id)
    _positions_cache[user_id] = (now, result)
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
    quantity = float(position_row.get("quantity") or 0)
    entry_price = float(position_row["avg_entry_price"]) if position_row.get("avg_entry_price") is not None else None

    # Validate TP/SL direction against position side using entry price as reference
    if entry_price is not None:
        if position_side == "LONG":
            if body.tp_price is not None and body.tp_price > 0 and body.tp_price <= entry_price:
                raise HTTPException(
                    status_code=400,
                    detail=f"LONG 仓位的止盈价 ({body.tp_price}) 必须高于入场价 ({entry_price})",
                )
            if body.sl_price is not None and body.sl_price > 0 and body.sl_price >= entry_price:
                raise HTTPException(
                    status_code=400,
                    detail=f"LONG 仓位的止损价 ({body.sl_price}) 必须低于入场价 ({entry_price})",
                )
        elif position_side == "SHORT":
            if body.tp_price is not None and body.tp_price > 0 and body.tp_price >= entry_price:
                raise HTTPException(
                    status_code=400,
                    detail=f"SHORT 仓位的止盈价 ({body.tp_price}) 必须低于入场价 ({entry_price})",
                )
            if body.sl_price is not None and body.sl_price > 0 and body.sl_price <= entry_price:
                raise HTTPException(
                    status_code=400,
                    detail=f"SHORT 仓位的止损价 ({body.sl_price}) 必须高于入场价 ({entry_price})",
                )
    _log.info(
        "TP/SL validated: user=%s pos=%d symbol=%s side=%s entry_price=%s tp=%s sl=%s",
        username, position_id, symbol, position_side, entry_price, body.tp_price, body.sl_price,
    )

    # Determine order side to close the position
    close_side = "SELL" if position_side == "LONG" else "BUY"

    from trade_relay.exchange.binance_client import BinanceClient
    client = BinanceClient(
        api_key=api_key,
        secret_key=api_secret,
        testnet=cfg_module.is_testnet(username),
    )

    errors = []

    db_user_id = int(user["sub"])

    if body.tp_price is not None and body.tp_price > 0:
        _log.info(
            "TP order request: user=%s pos=%d symbol=%s side=%s positionSide=%s price=%s qty=%s",
            username, position_id, symbol, close_side, position_side, body.tp_price, quantity,
        )
        try:
            tp_resp = client.place_take_profit_order(
                symbol=symbol,
                side=close_side,
                price=body.tp_price,
                quantity=quantity,
                position_side=position_side,
            )
            _log.info(
                "TP order response: user=%s pos=%d symbol=%s result=%s",
                username, position_id, symbol, tp_resp,
            )
            tp_exchange_id = str(tp_resp.get("algoId", "") or tp_resp.get("orderId", "")) if isinstance(tp_resp, dict) else None
            tp_client_id = str(tp_resp.get("clientAlgoId", "") or tp_resp.get("clientOrderId", "")) if isinstance(tp_resp, dict) else None
            tp_status = tp_resp.get("status", "NEW") if isinstance(tp_resp, dict) else "NEW"
            db_module.create_order(
                user_id=db_user_id,
                username=username,
                symbol=symbol,
                side=close_side,
                order_type="TAKE_PROFIT_MARKET",
                quantity=quantity,
                price=body.tp_price,
                status=tp_status,
                binance_order_id=tp_exchange_id or None,
                client_order_id=tp_client_id or None,
                trade_direction="CLOSE",
                position_id=position_id,
                reduce_only=True,
                order_category="Conditional",
            )
        except Exception as exc:
            errors.append(f"TP: {exc}")
            _log.warning("TP order failed for %s pos=%d: %s", username, position_id, exc)
            db_module.create_order(
                user_id=db_user_id,
                username=username,
                symbol=symbol,
                side=close_side,
                order_type="TAKE_PROFIT_MARKET",
                quantity=quantity,
                price=body.tp_price,
                status="FAILED",
                error_message=str(exc),
                trade_direction="CLOSE",
                position_id=position_id,
                reduce_only=True,
                order_category="Conditional",
            )

    if body.sl_price is not None and body.sl_price > 0:
        _log.info(
            "SL order request: user=%s pos=%d symbol=%s side=%s positionSide=%s stop_price=%s qty=%s",
            username, position_id, symbol, close_side, position_side, body.sl_price, quantity,
        )
        try:
            sl_resp = client.place_stop_loss_order(
                symbol=symbol,
                side=close_side,
                stop_price=body.sl_price,
                quantity=quantity,
                position_side=position_side,
            )
            _log.info(
                "SL order response: user=%s pos=%d symbol=%s result=%s",
                username, position_id, symbol, sl_resp,
            )
            sl_exchange_id = str(sl_resp.get("algoId", "") or sl_resp.get("orderId", "")) if isinstance(sl_resp, dict) else None
            sl_client_id = str(sl_resp.get("clientAlgoId", "") or sl_resp.get("clientOrderId", "")) if isinstance(sl_resp, dict) else None
            sl_status = sl_resp.get("status", "NEW") if isinstance(sl_resp, dict) else "NEW"
            db_module.create_order(
                user_id=db_user_id,
                username=username,
                symbol=symbol,
                side=close_side,
                order_type="STOP_MARKET",
                quantity=quantity,
                price=None,
                stop_price=body.sl_price,
                status=sl_status,
                binance_order_id=sl_exchange_id or None,
                client_order_id=sl_client_id or None,
                trade_direction="CLOSE",
                position_id=position_id,
                reduce_only=True,
                order_category="Conditional",
            )
        except Exception as exc:
            errors.append(f"SL: {exc}")
            _log.warning("SL order failed for %s pos=%d: %s", username, position_id, exc)
            db_module.create_order(
                user_id=db_user_id,
                username=username,
                symbol=symbol,
                side=close_side,
                order_type="STOP_MARKET",
                quantity=quantity,
                price=None,
                stop_price=body.sl_price,
                status="FAILED",
                error_message=str(exc),
                trade_direction="CLOSE",
                position_id=position_id,
                reduce_only=True,
                order_category="Conditional",
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

    _log.info("TP/SL set for %s pos=%d symbol=%s tp=%s sl=%s", username, position_id, symbol, body.tp_price, body.sl_price)
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
            entry_price=float(r["entry_price"]),
            close_price=float(r["close_price"]),
            quantity=float(r["quantity"]),
            realized_pnl=float(r["realized_pnl"]),
            commission=float(r["commission"]),
            created_at=str(r["created_at"]),
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
    )
    rows = db_module.get_position_history(user_id=user_id, limit=1)
    r = next((x for x in rows if x["id"] == new_id), rows[0])
    return PositionHistoryOut(
        id=int(r["id"]),
        username=str(r["username"]),
        symbol=str(r["symbol"]),
        side=str(r["side"]),
        entry_price=float(r["entry_price"]),
        close_price=float(r["close_price"]),
        quantity=float(r["quantity"]),
        realized_pnl=float(r["realized_pnl"]),
        commission=float(r["commission"]),
        created_at=str(r["created_at"]),
    )
