"""
Positions router: current positions, open orders, order history, trade history.
"""
import asyncio
import sys, os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from typing import Optional

from trade_relay import database as db_module
from trade_relay import config as cfg_module
from trade_relay.trading.order_status_stream import ensure_user_order_status_stream, register_user_stream_listener, unregister_user_stream_listener, sync_initial_positions_for_user
from backend.routers.auth import decode_token, get_current_user
from backend.logger import get_logger

router = APIRouter(prefix="/api/positions", tags=["positions"])
_log = get_logger(__name__)


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


def _db_positions(user_id: int | None) -> list[PositionOut]:
    rows = db_module.get_positions(user_id=user_id)
    positions: list[PositionOut] = []
    for index, row in enumerate(rows, start=1):
        positions.append(
            PositionOut(
                id=int(row.get("id") or index),
                symbol=str(row.get("symbol", "") or ""),
                side=str(row.get("position_side", "") or "").upper(),
                quantity=float(row["quantity"]),
                entry_price=float(row["avg_entry_price"]) if row.get("avg_entry_price") is not None else None,
                liquidation_price=None,
                unrealized_pnl=float(row["unrealized_pnl"]) if row.get("unrealized_pnl") is not None else None,
                leverage=int(row.get("leverage") or 0),
                margin_type=str(row.get("margin_type", "") or "").upper(),
                margin=None,
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
    if not username:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token payload")
        return

    await websocket.accept()

    api_key = cfg_module.get_api_key(username)
    api_secret = cfg_module.get_api_secret(username)
    if api_key and api_secret:
        ensure_user_order_status_stream(username, api_key, api_secret, cfg_module.is_testnet(username))
        # Sync current positions from Binance once on first WS connect (user is now authenticated)
        sync_initial_positions_for_user(username, api_key, api_secret, cfg_module.is_testnet(username))

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
