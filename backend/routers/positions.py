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
from typing import Any, Optional

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

# Per-user TTL cache: (user_id, status) (None = admin) → (timestamp, result)
_positions_cache: dict[tuple[int | None, str], tuple[float, list]] = {}
_POSITIONS_CACHE_TTL = 0.5  # seconds — short enough that an account_update fetch always sees fresh DB data
_startup_position_sync_inflight: set[str] = set()
_startup_position_sync_lock = threading.Lock()
_STARTUP_POSITION_SYNC_DELAY_SECONDS = 3.0


def _normalize_positions_status(status: str | None) -> str:
    normalized = str(status or "OPEN").strip().upper()
    return normalized or "OPEN"


def _clear_positions_cache(user_id: int | None) -> None:
    stale_keys = [key for key in _positions_cache if key[0] == user_id]
    for key in stale_keys:
        _positions_cache.pop(key, None)


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
                _clear_positions_cache(user_id)
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
    status: str
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


def _db_positions(user_id: int | None, status: str | None = "OPEN") -> list[PositionOut]:
    rows = db_module.get_positions(user_id=user_id, status=_normalize_positions_status(status))
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
                status=str(row.get("status") or "OPEN").upper(),
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


def _position_out_to_dict(position: PositionOut) -> dict[str, Any]:
    if hasattr(position, "model_dump"):
        return position.model_dump()
    return position.dict()


def _fetch_current_trigger_price(user_id: int | None, username: str, symbol: str) -> float | None:
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        return None

    if user_id is not None:
        summary_row = db_module.get_account_summary_from_db(user_id, normalized_symbol) or {}
        rest_mark_price = summary_row.get("rest_mark_price")
        if rest_mark_price is not None:
            try:
                price = float(rest_mark_price)
                if price > 0:
                    return price
            except (TypeError, ValueError):
                pass

    testnet = cfg_module.is_testnet(username)
    base_url = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
    try:
        import requests as _requests

        proxy_cfg = None
        proxy_url = os.environ.get("ALL_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        if proxy_url:
            proxy_cfg = {"http": proxy_url, "https": proxy_url}
        resp = _requests.get(
            f"{base_url}/fapi/v1/premiumIndex",
            params={"symbol": normalized_symbol},
            proxies=proxy_cfg,
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        price = float(data.get("markPrice") or data.get("price") or 0)
        return price if price > 0 else None
    except Exception as exc:
        _log.warning("[POSITION_SYNC] phase=mark_price_lookup_failed username=%s symbol=%s error=%s", username, normalized_symbol, exc)
        return None


def _active_order_rows_for_user(user_id: int | None, username: str) -> list[dict]:
    if user_id is not None:
        return db_module.get_active_orders(user_id=user_id)

    rows = db_module.query_orders(username=username, limit=500)
    return [
        row for row in rows
        if str(row.get("order_category") or "Basic") == "Basic"
        and str(row.get("status") or "").upper() in {"NEW", "PARTIALLY_FILLED", "PENDING", "PENDING_CANCEL"}
    ]


def _conditional_order_rows_for_user(user_id: int | None, username: str) -> list[dict]:
    rows = db_module.query_orders(user_id=user_id, username=username, limit=500)
    active_statuses = {"NEW", "PARTIALLY_FILLED", "PENDING", "PENDING_CANCEL"}
    return [
        row for row in rows
        if str(row.get("order_category") or "Basic") == "Conditional"
        and str(row.get("status") or "").upper() in active_statuses
    ]


def _serialize_open_orders_snapshot(user_id: int | None, username: str) -> list[dict[str, Any]]:
    rows = _active_order_rows_for_user(user_id, username)
    return [
        {
            "id": int(row["id"]),
            "username": str(row.get("username") or username),
            "symbol": str(row.get("symbol") or "").upper(),
            "side": str(row.get("side") or "").upper(),
            "order_type": str(row.get("order_type") or "").upper(),
            "trade_direction": str(row.get("trade_direction") or "").upper() if row.get("trade_direction") else None,
            "quantity": float(row.get("quantity") or 0),
            "filled_qty": float(row.get("filled_qty") or 0),
            "price": float(row["price"]) if row.get("price") is not None else None,
            "avg_price": float(row["avg_price"]) if row.get("avg_price") is not None else None,
            "stop_price": float(row["stop_price"]) if row.get("stop_price") is not None else None,
            "reduce_only": bool(row.get("reduce_only") or False),
            "post_only": bool(row.get("post_only") or False),
            "commission": float(row["commission"]) if row.get("commission") is not None else None,
            "commission_asset": str(row["commission_asset"]) if row.get("commission_asset") is not None else None,
            "status": str(row.get("status") or "NEW").upper(),
            "exchange_order_id": str(row.get("exchange_order_id") or "") or None,
            "created_at": serialize_utc_timestamp_required(row.get("created_at")),
            "updated_at": serialize_utc_timestamp(row.get("updated_at")),
        }
        for row in rows
    ]


def _serialize_conditional_orders_snapshot(user_id: int | None, username: str) -> list[dict[str, Any]]:
    rows = _conditional_order_rows_for_user(user_id, username)
    payload: list[dict[str, Any]] = []
    for row in rows:
        algo_id_raw = str(row.get("algo_id") or "").strip()
        if not algo_id_raw:
            continue
        try:
            algo_id = int(algo_id_raw)
        except ValueError:
            continue

        order_type = str(row.get("order_type") or "").upper()
        if order_type == "TAKE_PROFIT_MARKET":
            trigger_price = float(row["price"]) if row.get("price") is not None else 0.0
        else:
            trigger_price = float(row["stop_price"]) if row.get("stop_price") is not None else 0.0

        payload.append({
            "algo_id": algo_id,
            "algo_client_id": str(row.get("algo_client_id") or "") or None,
            "symbol": str(row.get("symbol") or "").upper(),
            "side": str(row.get("side") or "").upper(),
            "position_side": _derive_conditional_position_side(
                str(row.get("side") or ""),
                str(row.get("trade_direction") or ""),
            ),
            "order_type": order_type,
            "quantity": float(row.get("quantity") or 0),
            "trigger_price": trigger_price,
            "status": str(row.get("status") or "NEW").upper(),
            "created_at": serialize_utc_timestamp_required(row.get("created_at")),
            "trade_direction": str(row.get("trade_direction") or "").upper() if row.get("trade_direction") else None,
            "exchange_order_id": str(row.get("exchange_order_id") or "") or None,
            "client_order_id": str(row.get("client_order_id") or "") or None,
        })
    return payload


def _build_positions_ws_payload(user_id: int | None, username: str, event: dict[str, Any]) -> dict[str, Any]:
    payload = dict(event)
    payload["positions"] = [_position_out_to_dict(position) for position in _db_positions(user_id, status="OPEN")]
    payload["open_orders"] = _serialize_open_orders_snapshot(user_id, username)
    payload["conditional_orders"] = _serialize_conditional_orders_snapshot(user_id, username)
    return payload


def _load_account_summary_snapshot(
    user_id: int | None,
    username: str,
    symbol: str | None,
    *,
    refresh: bool,
) -> dict[str, Any] | None:
    normalized_symbol = str(symbol or "").strip().upper() or None
    if user_id is None or not normalized_symbol:
        return None

    if refresh:
        try:
            from backend.routers.account import _refresh_account_summary_from_exchange

            row = _refresh_account_summary_from_exchange(user_id, username, normalized_symbol) or {}
        except Exception:
            _log.exception(
                "[POSITION_SYNC] phase=account_summary_refresh_error username=%s symbol=%s",
                username,
                normalized_symbol,
            )
            row = db_module.get_account_summary_from_db(user_id, normalized_symbol) or {}
    else:
        row = db_module.get_account_summary_from_db(user_id, normalized_symbol) or {}

    if not row:
        return None

    return {
        "symbol": row.get("symbol"),
        "base_asset": row.get("base_asset"),
        "quote_asset": row.get("quote_asset"),
        "position_mode": row.get("position_mode"),
        "leverage": int(row["leverage"]) if row.get("leverage") is not None else None,
        "configured_leverage": int(row["configured_leverage"]) if row.get("configured_leverage") is not None else None,
        "long_position_qty": float(row["long_position_qty"]) if row.get("long_position_qty") is not None else None,
        "short_position_qty": float(row["short_position_qty"]) if row.get("short_position_qty") is not None else None,
        "long_position_value": float(row["long_position_value"]) if row.get("long_position_value") is not None else None,
        "short_position_value": float(row["short_position_value"]) if row.get("short_position_value") is not None else None,
        "rest_mark_price": float(row["rest_mark_price"]) if row.get("rest_mark_price") is not None else None,
        "available_balance": float(row["available_balance"]) if row.get("available_balance") is not None else None,
        "margin_ratio": float(row["margin_ratio"]) if row.get("margin_ratio") is not None else None,
        "risk_rate": float(row["risk_rate"]) if row.get("risk_rate") is not None else None,
        "maint_margin": float(row["maint_margin"]) if row.get("maint_margin") is not None else None,
        "total_equity": float(row["total_equity"]) if row.get("total_equity") is not None else None,
        "position_value": float(row["position_value"]) if row.get("position_value") is not None else None,
        "actual_leverage": float(row["actual_leverage"]) if row.get("actual_leverage") is not None else None,
        "unrealized_pnl": float(row["unrealized_pnl"]) if row.get("unrealized_pnl") is not None else None,
        "wallet_balance": float(row["wallet_balance"]) if row.get("wallet_balance") is not None else None,
        "has_api_credentials": bool(row.get("has_api_credentials") or False),
        "message": row.get("message"),
    }


def _build_positions_ws_payload_for_symbol(
    user_id: int | None,
    username: str,
    event: dict[str, Any],
    summary_symbol: str | None,
    *,
    refresh_account_summary: bool,
) -> dict[str, Any]:
    payload = _build_positions_ws_payload(user_id, username, event)
    account_summary = _load_account_summary_snapshot(
        user_id,
        username,
        summary_symbol,
        refresh=refresh_account_summary,
    )
    if account_summary is not None:
        payload["account_summary"] = account_summary
    return payload


@router.post("/sync", response_model=list[PositionOut])
def sync_positions(
    status: str = Query("OPEN", description="持仓状态过滤：OPEN/CLOSE/ALL"),
    user: dict = Depends(get_current_user),
):
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
        _clear_positions_cache(user_id)
    else:
        _log.warning("[POSITION_SYNC] phase=missing_credentials username=%s", username)
    user_id = int(user["sub"]) if user["role"] != "admin" else None
    normalized_status = _normalize_positions_status(status)
    result = _db_positions(user_id=user_id, status=normalized_status)
    _log.info("[POSITION_SYNC] phase=response username=%s status=%s positions=%s", username, normalized_status, len(result))
    return result


@router.get("", response_model=list[PositionOut])
def get_positions(
    status: str = Query("OPEN", description="持仓状态过滤：OPEN/CLOSE/ALL"),
    user: dict = Depends(get_current_user),
):
    user_id = int(user["sub"]) if user["role"] != "admin" else None
    normalized_status = _normalize_positions_status(status)
    cache_key = (user_id, normalized_status)
    now = time.monotonic()
    cached = _positions_cache.get(cache_key)
    if cached and now - cached[0] < _POSITIONS_CACHE_TTL:
        _log.info("[POSITION_SYNC] phase=cache_hit user_id=%s status=%s positions=%s", user_id, normalized_status, len(cached[1]))
        return cached[1]
    result = _db_positions(user_id=user_id, status=normalized_status)
    _positions_cache[cache_key] = (now, result)
    _log.info("[POSITION_SYNC] phase=cache_miss user_id=%s status=%s positions=%s", user_id, normalized_status, len(result))
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
    rows = db_module.get_positions(user_id=user_id, status="OPEN")
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
    current_price = _fetch_current_trigger_price(user_id, username, symbol)

    validation_errors = validate_tpsl_prices(
        position_side=position_side,
        entry_price=entry_price,
        tp_price=body.tp_price,
        sl_price=body.sl_price,
        current_price=current_price,
    )
    if validation_errors:
        raise HTTPException(status_code=400, detail="; ".join(validation_errors))
    _log.info(
        "[POSITION_SYNC] phase=tpsl_validated user=%s pos=%d symbol=%s side=%s entry_price=%s current_price=%s tp=%s sl=%s",
        username, position_id, symbol, position_side, entry_price, current_price, body.tp_price, body.sl_price,
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
        current_price=current_price,
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
    _clear_positions_cache(user_id)

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
    summary_symbol = None
    try:
        summary_symbol = websocket.query_params.get("symbol")
    except Exception:
        summary_symbol = None
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
        await websocket.send_json(
            _build_positions_ws_payload_for_symbol(
                user_id,
                username,
                {"type": "connected"},
                summary_symbol,
                refresh_account_summary=False,
            )
        )
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=3.0)
            except asyncio.TimeoutError:
                # Heartbeat every 3s: detects dead connections quickly
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(
                _build_positions_ws_payload_for_symbol(
                    user_id,
                    username,
                    event,
                    summary_symbol,
                    refresh_account_summary=bool(summary_symbol),
                )
            )
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
