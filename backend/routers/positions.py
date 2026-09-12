"""
Positions router: current positions, open orders, order history, trade history.
"""
import asyncio
import math
import sys, os
import threading
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from typing import Any, Literal, Optional

from trade_relay import database as db_module
from trade_relay import config as cfg_module
from trade_relay.trading.order_status_stream import ensure_user_order_status_stream, register_user_stream_listener, unregister_user_stream_listener, sync_initial_positions_for_user
from trade_relay.trading.tpsl_service import place_tp_sl_orders, validate_tpsl_prices
from trade_relay.trading.excursion_retry_worker import _repair_missing_order_links
from task.recalculate_historical_excursion_metrics import recalculate_missing_metrics
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
    close_order_id: Optional[int] = None
    planned_stop_price: Optional[float] = None
    initial_risk_usdc: Optional[float] = None
    mfe_usdc: Optional[float] = None
    mae_usdc: Optional[float] = None
    mfe_at: Optional[str] = None
    mae_at: Optional[str] = None
    net_pnl: Optional[float] = None
    mfe_r: Optional[float] = None
    mae_r: Optional[float] = None
    net_pnl_r: Optional[float] = None
    profit_capture_rate: Optional[float] = None
    exit_efficiency: Optional[float] = None
    profit_giveback_usdc: Optional[float] = None
    profit_giveback_rate: Optional[float] = None
    excursion_status: Optional[str] = None
    excursion_source: Optional[str] = None
    excursion_calculated_at: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None


class PositionRecordOut(BaseModel):
    id: int
    position_id: Optional[int] = None
    username: str
    symbol: str
    side: str
    status: str
    position_mode: str
    quantity: float
    entry_price: Optional[float] = None
    close_price: Optional[float] = None
    realized_pnl: Optional[float] = None
    commission: float = 0.0
    commission_asset: Optional[str] = None
    open_time: Optional[str] = None
    close_time: Optional[str] = None
    open_orders_id: list[str] = Field(default_factory=list)
    close_orders_id: list[str] = Field(default_factory=list)
    planned_stop_price: Optional[float] = None
    initial_risk_usdc: Optional[float] = None
    mfe_usdc: Optional[float] = None
    mae_usdc: Optional[float] = None
    net_pnl: Optional[float] = None
    mfe_r: Optional[float] = None
    mae_r: Optional[float] = None
    net_pnl_r: Optional[float] = None
    profit_capture_rate: Optional[float] = None
    profit_giveback_usdc: Optional[float] = None
    excursion_status: Optional[str] = None


class PositionReviewIn(BaseModel):
    market_state: Optional[Literal["TREND", "RANGE", "CLIMAX_REVERSAL"]] = None
    setup_name: Optional[str] = Field(None, max_length=255)
    entry_rationale: Optional[str] = Field(None, max_length=5000)
    signal_candle_trigger: Optional[str] = Field(None, max_length=5000)
    opportunity_grade: Optional[Literal["A", "B", "C"]] = None
    is_planned_trade: Optional[bool] = None
    first_entry_pnl_state: Optional[Literal["PROFIT", "LOSS", "BREAKEVEN", "NOT_APPLICABLE"]] = None
    planned_stop_price: Optional[float] = Field(None, gt=0)
    actual_stop_fill_price: Optional[float] = Field(None, gt=0)
    first_target: Optional[str] = Field(None, max_length=255)
    structural_target: Optional[str] = Field(None, max_length=255)
    final_exit_reason: Optional[str] = Field(None, max_length=5000)
    discipline_trigger: Optional[Literal["NONE", "COOLDOWN", "STOP_TRADING", "BOTH"]] = None


class PositionReviewOut(PositionReviewIn):
    id: int
    position_id: int
    user_id: int
    created_at: str
    updated_at: str

# Per-user TTL cache: (user_id, status) (None = admin) → (timestamp, result)
_positions_cache: dict[tuple[int | None, str], tuple[float, list]] = {}
_POSITIONS_CACHE_TTL = 0.5  # seconds — short enough that an account_update fetch always sees fresh DB data
_startup_position_sync_inflight: set[str] = set()
_startup_position_sync_lock = threading.Lock()
_STARTUP_POSITION_SYNC_DELAY_SECONDS = 3.0
_maintenance_inflight: set[int | None] = set()
_maintenance_lock = threading.Lock()


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
    planned_stop_price: Optional[float] = None
    initial_risk_usdc: Optional[float] = None
    live_mfe_usdc: float = 0.0
    live_mae_usdc: float = 0.0
    live_mfe_at: Optional[str] = None
    live_mae_at: Optional[str] = None


class PositionMaintenanceIn(BaseModel):
    username: Optional[str] = None


class PositionOrderLinkBackfillOut(BaseModel):
    repaired: int
    skipped: int


class PositionExcursionRecalculationOut(BaseModel):
    scanned: int
    calculated: int
    queued: int
    failed: int
    duplicate_history_rows: int


def _resolve_maintenance_user_id(user: dict, requested_username: str | None) -> int | None:
    username = str(requested_username or "").strip()
    if user.get("role") != "admin":
        if username and username != str(user.get("username") or ""):
            raise HTTPException(status_code=403, detail="Cannot maintain another user's positions")
        return int(user["sub"])
    if not username:
        return None
    target = db_module.get_user_by_username(username)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    return int(target["id"])


async def _run_position_maintenance(task_name: str, user_id: int | None, operation):
    with _maintenance_lock:
        conflicts = (
            bool(_maintenance_inflight)
            if user_id is None
            else None in _maintenance_inflight or user_id in _maintenance_inflight
        )
        if conflicts:
            raise HTTPException(status_code=409, detail="This maintenance task is already running")
        _maintenance_inflight.add(user_id)
    try:
        return await asyncio.to_thread(operation)
    except HTTPException:
        raise
    except Exception as exc:
        _log.exception("Position maintenance failed task=%s user_id=%s", task_name, user_id)
        raise HTTPException(status_code=500, detail=f"Position maintenance failed: {exc}") from exc
    finally:
        with _maintenance_lock:
            _maintenance_inflight.discard(user_id)


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


def _restore_missing_position_risk(row: dict, position_id: int, active_sl_price: float | None) -> tuple[float | None, float | None]:
    """Recover or reconcile a legacy open position's 1R."""
    planned_stop = float(row["planned_stop_price"]) if row.get("planned_stop_price") is not None else None
    initial_risk = float(row["initial_risk_usdc"]) if row.get("initial_risk_usdc") is not None else None
    # A risk amount without the stop that defines it is not a usable 1R.  Older
    # rows can contain only the opening commission here; do not expose that as
    # risk or let it prevent the first real stop from establishing the baseline.
    has_valid_baseline = (
        planned_stop is not None
        and math.isfinite(planned_stop)
        and planned_stop > 0
        and initial_risk is not None
        and math.isfinite(initial_risk)
        and initial_risk > 0
    )
    if has_valid_baseline:
        entry_price = float(row["avg_entry_price"]) if row.get("avg_entry_price") is not None else None
        quantity = abs(float(row.get("quantity") or 0))
        if (
            planned_stop is not None
            and entry_price is not None
            and math.isfinite(planned_stop)
            and math.isfinite(entry_price)
            and math.isfinite(quantity)
            and planned_stop > 0
            and entry_price > 0
            and quantity > 0
        ):
            reconciled_risk = abs(entry_price - planned_stop) * quantity
            if math.isfinite(reconciled_risk) and reconciled_risk > initial_risk + 1e-12:
                try:
                    db_module.update_position_initial_risk(position_id, reconciled_risk)
                except Exception:
                    _log.exception(
                        "Failed to reconcile stale position risk: position_id=%s risk=%s",
                        position_id,
                        reconciled_risk,
                    )
                else:
                    _log.info(
                        "[POSITION_SYNC] phase=risk_reconciled pos=%s old_risk=%s initial_risk_usdc=%s",
                        position_id,
                        initial_risk,
                        reconciled_risk,
                    )
                return planned_stop, reconciled_risk
        return planned_stop, initial_risk

    entry_price = float(row["avg_entry_price"]) if row.get("avg_entry_price") is not None else None
    quantity = abs(float(row.get("quantity") or 0))
    has_valid_planned_stop = (
        planned_stop is not None and math.isfinite(planned_stop) and planned_stop > 0
    )
    stop_price = planned_stop if has_valid_planned_stop else active_sl_price
    side = str(row.get("position_side") or "").upper()
    if (
        entry_price is None
        or stop_price is None
        or not math.isfinite(entry_price)
        or not math.isfinite(stop_price)
        or not math.isfinite(quantity)
        or entry_price <= 0
        or stop_price <= 0
        or quantity <= 0
    ):
        return planned_stop if has_valid_planned_stop else None, None

    # A moved stop already beyond breakeven cannot reveal the original downside risk.
    # Leave R unavailable instead of manufacturing a misleading baseline.
    is_loss_side_stop = (side == "LONG" and stop_price < entry_price) or (side == "SHORT" and stop_price > entry_price)
    if not is_loss_side_stop:
        return planned_stop if has_valid_planned_stop else None, None

    recovered_risk = abs(entry_price - stop_price) * quantity
    if not math.isfinite(recovered_risk) or recovered_risk <= 0:
        return planned_stop if has_valid_planned_stop else None, None

    try:
        db_module.initialize_position_risk(position_id, stop_price, recovered_risk)
    except Exception:
        _log.exception(
            "Failed to restore position risk from active stop: position_id=%s stop=%s",
            position_id,
            stop_price,
        )
        return planned_stop if has_valid_planned_stop else None, None

    _log.info(
        "[POSITION_SYNC] phase=risk_restored pos=%s stop=%s initial_risk_usdc=%s",
        position_id,
        stop_price,
        recovered_risk,
    )
    return stop_price, recovered_risk


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

        planned_stop_price, initial_risk_usdc = _restore_missing_position_risk(row, pos_id, sl)

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
                planned_stop_price=planned_stop_price,
                initial_risk_usdc=initial_risk_usdc,
                live_mfe_usdc=float(row.get("live_mfe_usdc") or 0),
                live_mae_usdc=float(row.get("live_mae_usdc") or 0),
                live_mfe_at=serialize_utc_timestamp(row.get("live_mfe_at")),
                live_mae_at=serialize_utc_timestamp(row.get("live_mae_at")),
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

    # TP/SL validation must use the same recent mark-price source as the modal.
    # The persisted account summary is only a fallback because it can be stale.
    try:
        from backend.routers.account import _fetch_public_mark_price

        return _fetch_public_mark_price(normalized_symbol, username)
    except Exception as exc:
        _log.warning(
            "[POSITION_SYNC] phase=live_mark_price_lookup_failed username=%s symbol=%s error=%s fallback=db_snapshot",
            username,
            normalized_symbol,
            exc,
        )

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
    return None


def _active_order_rows_for_user(user_id: int | None, username: str) -> list[dict]:
    active_statuses = {"NEW", "PARTIALLY_FILLED", "PENDING", "PENDING_CANCEL"}

    def _should_project_triggered_conditional_as_basic(row: dict) -> bool:
        return (
            str(row.get("order_category") or "").upper() == "CONDITIONAL"
            and str(row.get("order_type") or "").upper() == "STOP"
            and str(row.get("status") or "").upper() in active_statuses
            and bool(str(row.get("exchange_order_id") or "").strip())
        )

    def _project_triggered_conditional_as_basic(row: dict) -> dict:
        projected = dict(row)
        projected["order_category"] = "Basic"
        projected["order_type"] = "LIMIT"
        projected["stop_price"] = None
        return projected

    if user_id is not None:
        rows = list(db_module.get_active_orders(user_id=user_id))
        recent_rows = db_module.query_orders(user_id=user_id, username=username, status="NEW", limit=500)
    else:
        recent_rows = db_module.query_orders(username=username, limit=500)
        rows = [
            row for row in recent_rows
            if str(row.get("order_category") or "Basic") == "Basic"
            and str(row.get("status") or "").upper() in active_statuses
        ]

    triggered_rows = [
        _project_triggered_conditional_as_basic(row)
        for row in recent_rows
        if _should_project_triggered_conditional_as_basic(row)
    ]
    return rows + triggered_rows


def _conditional_order_rows_for_user(user_id: int | None, username: str) -> list[dict]:
    rows = db_module.query_orders(user_id=user_id, username=username, limit=500)
    active_statuses = {"NEW", "PARTIALLY_FILLED", "PENDING", "PENDING_CANCEL"}
    return [
        row for row in rows
        if str(row.get("order_category") or "Basic") == "Conditional"
        and str(row.get("status") or "").upper() in active_statuses
        and not (
            str(row.get("order_type") or "").upper() == "STOP"
            and str(row.get("exchange_order_id") or "").strip()
        )
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


@router.post("/maintenance/backfill-open-orders", response_model=PositionOrderLinkBackfillOut)
async def backfill_position_open_orders(
    body: PositionMaintenanceIn,
    user: dict = Depends(get_current_user),
):
    """Repair missing OPEN order IDs and opening times from local filled orders."""
    user_id = _resolve_maintenance_user_id(user, body.username)

    def operation() -> dict[str, int]:
        repaired, skipped = _repair_missing_order_links(10000, user_id=user_id)
        return {"repaired": repaired, "skipped": skipped}

    return await _run_position_maintenance("backfill-open-orders", user_id, operation)


@router.post("/maintenance/recalculate-mfe", response_model=PositionExcursionRecalculationOut)
async def recalculate_position_mfe(
    body: PositionMaintenanceIn,
    user: dict = Depends(get_current_user),
):
    """Recalculate closed positions whose excursion metrics are not current."""
    user_id = _resolve_maintenance_user_id(user, body.username)
    return await _run_position_maintenance(
        "recalculate-mfe",
        user_id,
        lambda: recalculate_missing_metrics(user_id=user_id, dry_run=False),
    )


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

    stored_planned_stop = (
        float(position_row["planned_stop_price"])
        if position_row.get("planned_stop_price") is not None
        else None
    )
    # Treat the two columns as one baseline.  A standalone amount (commonly an
    # old opening-commission value) is invalid and must not block initialization
    # from the first actual stop-loss.
    effective_initial_risk = (
        float(position_row["initial_risk_usdc"])
        if stored_planned_stop is not None
        and math.isfinite(stored_planned_stop)
        and stored_planned_stop > 0
        and position_row.get("initial_risk_usdc") is not None
        else None
    )
    if effective_initial_risk is None and body.sl_price and body.sl_price > 0 and entry_price and quantity > 0:
        # A stop added to an existing position has no reliable historical opening-fee
        # context, so initialise 1R from price risk. The first planned stop remains fixed.
        effective_initial_risk = abs(entry_price - float(body.sl_price)) * quantity
        if effective_initial_risk > 0:
            db_module.initialize_position_risk(
                position_id,
                float(body.sl_price),
                effective_initial_risk,
            )

    # Store the set prices in memory
    with _tpsl_store_lock:
        _tpsl_store[position_id] = (
            body.tp_price if body.tp_price and body.tp_price > 0 else None,
            body.sl_price if body.sl_price and body.sl_price > 0 else None,
        )
    # Invalidate position cache
    _clear_positions_cache(user_id)

    _log.info("[POSITION_SYNC] phase=tpsl_set user=%s pos=%d symbol=%s tp=%s sl=%s", username, position_id, symbol, body.tp_price, body.sl_price)
    return {
        "ok": True,
        "tp_price": body.tp_price,
        "sl_price": body.sl_price,
        "initial_risk_usdc": effective_initial_risk,
    }


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
def get_position_history(
    limit: int = Query(200, ge=1, le=5000),
    username: Optional[str] = None,
    symbol: Optional[str] = None,
    side: Optional[str] = Query(None, pattern="^(LONG|SHORT)$"),
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    user_id = int(user["sub"]) if user["role"] != "admin" else None
    rows = db_module.get_position_history(
        user_id=user_id,
        limit=limit,
        username=username if user["role"] == "admin" else None,
        symbol=symbol,
        side=side,
        start_time=start_time,
        end_time=end_time,
    )
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
            close_order_id=int(r["close_order_id"]) if r.get("close_order_id") is not None else None,
            planned_stop_price=float(r["planned_stop_price"]) if r.get("planned_stop_price") is not None else None,
            initial_risk_usdc=float(r["initial_risk_usdc"]) if r.get("initial_risk_usdc") is not None else None,
            mfe_usdc=float(r["mfe_usdc"]) if r.get("mfe_usdc") is not None else None,
            mae_usdc=float(r["mae_usdc"]) if r.get("mae_usdc") is not None else None,
            mfe_at=serialize_utc_timestamp(r.get("mfe_at")),
            mae_at=serialize_utc_timestamp(r.get("mae_at")),
            net_pnl=float(r["net_pnl"]) if r.get("net_pnl") is not None else None,
            mfe_r=float(r["mfe_r"]) if r.get("mfe_r") is not None else None,
            mae_r=float(r["mae_r"]) if r.get("mae_r") is not None else None,
            net_pnl_r=float(r["net_pnl_r"]) if r.get("net_pnl_r") is not None else None,
            profit_capture_rate=float(r["profit_capture_rate"]) if r.get("profit_capture_rate") is not None else None,
            exit_efficiency=float(r["exit_efficiency"]) if r.get("exit_efficiency") is not None else None,
            profit_giveback_usdc=float(r["profit_giveback_usdc"]) if r.get("profit_giveback_usdc") is not None else None,
            profit_giveback_rate=float(r["profit_giveback_rate"]) if r.get("profit_giveback_rate") is not None else None,
            excursion_status=str(r["excursion_status"]) if r.get("excursion_status") is not None else None,
            excursion_source=str(r["excursion_source"]) if r.get("excursion_source") is not None else None,
            excursion_calculated_at=serialize_utc_timestamp(r.get("excursion_calculated_at")),
            created_at=serialize_utc_timestamp_required(r.get("created_at")),
            updated_at=serialize_utc_timestamp(r.get("updated_at")),
        )
        for r in rows
    ]


@router.get("/records", response_model=list[PositionRecordOut])
def get_position_records(
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    username: Optional[str] = None,
    symbol: Optional[str] = None,
    side: Optional[str] = Query(None, pattern="^(LONG|SHORT)$"),
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    user_id = int(user["sub"]) if user["role"] != "admin" else None
    rows = db_module.query_position_records(
        user_id=user_id,
        limit=limit,
        offset=offset,
        username=username if user["role"] == "admin" else None,
        symbol=symbol,
        side=side,
        start_time=start_time,
        end_time=end_time,
    )
    return [
        PositionRecordOut(
            id=int(row["id"]),
            position_id=int(row["position_id"]) if row.get("position_id") is not None else None,
            username=str(row.get("username") or ""),
            symbol=str(row.get("symbol") or ""),
            side=str(row.get("side") or ""),
            status=str(row.get("status") or "CLOSE").upper(),
            position_mode=str(row.get("position_mode") or "UNKNOWN").upper(),
            quantity=float(row.get("quantity") or 0),
            entry_price=float(row["entry_price"]) if row.get("entry_price") is not None else None,
            close_price=float(row["close_price"]) if row.get("close_price") is not None else None,
            realized_pnl=float(row["realized_pnl"]) if row.get("realized_pnl") is not None else None,
            commission=float(row.get("commission") or 0),
            commission_asset=str(row["commission_asset"]) if row.get("commission_asset") is not None else None,
            open_time=serialize_utc_timestamp(row.get("open_time")),
            close_time=serialize_utc_timestamp(row.get("close_time")),
            open_orders_id=[value for value in str(row.get("open_orders_id") or "").split(",") if value],
            close_orders_id=[value for value in str(row.get("close_orders_id") or "").split(",") if value],
            planned_stop_price=float(row["planned_stop_price"]) if row.get("planned_stop_price") is not None else None,
            initial_risk_usdc=float(row["initial_risk_usdc"]) if row.get("initial_risk_usdc") is not None else None,
            mfe_usdc=float(row["mfe_usdc"]) if row.get("mfe_usdc") is not None else None,
            mae_usdc=float(row["mae_usdc"]) if row.get("mae_usdc") is not None else None,
            net_pnl=float(row["net_pnl"]) if row.get("net_pnl") is not None else None,
            mfe_r=float(row["mfe_r"]) if row.get("mfe_r") is not None else None,
            mae_r=float(row["mae_r"]) if row.get("mae_r") is not None else None,
            net_pnl_r=float(row["net_pnl_r"]) if row.get("net_pnl_r") is not None else None,
            profit_capture_rate=float(row["profit_capture_rate"]) if row.get("profit_capture_rate") is not None else None,
            profit_giveback_usdc=float(row["profit_giveback_usdc"]) if row.get("profit_giveback_usdc") is not None else None,
            excursion_status=str(row["excursion_status"]) if row.get("excursion_status") is not None else None,
        )
        for row in rows
    ]


def _review_owner(position_id: int, user: dict) -> int:
    position = db_module.get_position_by_id(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    owner_id = int(position["user_id"])
    if user.get("role") != "admin" and owner_id != int(user["sub"]):
        raise HTTPException(status_code=403, detail="Cannot access another user's position review")
    return owner_id


def _position_review_out(row: dict) -> PositionReviewOut:
    return PositionReviewOut(
        id=int(row["id"]),
        position_id=int(row["position_id"]),
        user_id=int(row["user_id"]),
        market_state=row.get("market_state"),
        setup_name=row.get("setup_name"),
        entry_rationale=row.get("entry_rationale"),
        signal_candle_trigger=row.get("signal_candle_trigger"),
        opportunity_grade=row.get("opportunity_grade"),
        is_planned_trade=bool(row["is_planned_trade"]) if row.get("is_planned_trade") is not None else None,
        first_entry_pnl_state=row.get("first_entry_pnl_state"),
        planned_stop_price=float(row["planned_stop_price"]) if row.get("planned_stop_price") is not None else None,
        actual_stop_fill_price=float(row["actual_stop_fill_price"]) if row.get("actual_stop_fill_price") is not None else None,
        first_target=row.get("first_target"),
        structural_target=row.get("structural_target"),
        final_exit_reason=row.get("final_exit_reason"),
        discipline_trigger=row.get("discipline_trigger"),
        created_at=serialize_utc_timestamp_required(row.get("created_at")),
        updated_at=serialize_utc_timestamp_required(row.get("updated_at")),
    )


@router.get("/{position_id}/review", response_model=Optional[PositionReviewOut])
def get_position_review(position_id: int, user: dict = Depends(get_current_user)):
    owner_id = _review_owner(position_id, user)
    row = db_module.get_position_review(position_id, owner_id)
    return _position_review_out(row) if row is not None else None


@router.put("/{position_id}/review", response_model=PositionReviewOut)
def save_position_review(
    position_id: int,
    body: PositionReviewIn,
    user: dict = Depends(get_current_user),
):
    owner_id = _review_owner(position_id, user)
    values = body.model_dump()
    for field, value in values.items():
        if isinstance(value, str):
            values[field] = value.strip() or None
    row = db_module.upsert_position_review(position_id, owner_id, values)
    return _position_review_out(row)


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
        close_order_id=body.close_order_id,
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
        close_order_id=int(r["close_order_id"]) if r.get("close_order_id") is not None else None,
        planned_stop_price=float(r["planned_stop_price"]) if r.get("planned_stop_price") is not None else None,
        initial_risk_usdc=float(r["initial_risk_usdc"]) if r.get("initial_risk_usdc") is not None else None,
        mfe_usdc=float(r["mfe_usdc"]) if r.get("mfe_usdc") is not None else None,
        mae_usdc=float(r["mae_usdc"]) if r.get("mae_usdc") is not None else None,
        mfe_at=serialize_utc_timestamp(r.get("mfe_at")),
        mae_at=serialize_utc_timestamp(r.get("mae_at")),
        net_pnl=float(r["net_pnl"]) if r.get("net_pnl") is not None else None,
        mfe_r=float(r["mfe_r"]) if r.get("mfe_r") is not None else None,
        mae_r=float(r["mae_r"]) if r.get("mae_r") is not None else None,
        net_pnl_r=float(r["net_pnl_r"]) if r.get("net_pnl_r") is not None else None,
        profit_capture_rate=float(r["profit_capture_rate"]) if r.get("profit_capture_rate") is not None else None,
        exit_efficiency=float(r["exit_efficiency"]) if r.get("exit_efficiency") is not None else None,
        profit_giveback_usdc=float(r["profit_giveback_usdc"]) if r.get("profit_giveback_usdc") is not None else None,
        profit_giveback_rate=float(r["profit_giveback_rate"]) if r.get("profit_giveback_rate") is not None else None,
        excursion_status=str(r["excursion_status"]) if r.get("excursion_status") is not None else None,
        excursion_source=str(r["excursion_source"]) if r.get("excursion_source") is not None else None,
        excursion_calculated_at=serialize_utc_timestamp(r.get("excursion_calculated_at")),
        created_at=serialize_utc_timestamp_required(r.get("created_at")),
        updated_at=serialize_utc_timestamp(r.get("updated_at")),
    )
