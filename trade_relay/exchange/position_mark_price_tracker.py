"""Track mark prices for every open position and persist live MFE/MAE extrema."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import threading

from trade_relay import database as db_module
from trade_relay.exchange.public_mark_price_stream import (
    register_public_mark_price_listener,
    unregister_public_mark_price_listener,
)


logger = logging.getLogger(__name__)


@dataclass
class _TrackedPosition:
    user_id: int
    symbol: str
    side: str
    quantity: float
    entry_price: float
    live_mfe_usdc: float
    live_mae_usdc: float


_lock = threading.RLock()
_positions_by_user: dict[int, dict[tuple[str, str], _TrackedPosition]] = {}
_listeners_by_symbol: dict[str, object] = {}


def _normalized_position(raw: dict, user_id: int, persisted: dict | None) -> _TrackedPosition | None:
    symbol = str(raw.get("symbol") or raw.get("s") or "").strip().upper()
    raw_amount = raw.get("positionAmt") if "positionAmt" in raw else raw.get("pa")
    try:
        amount = float(raw_amount or 0)
        entry_price = float(raw.get("entryPrice") or raw.get("ep") or 0)
    except (TypeError, ValueError):
        return None
    if not symbol or amount == 0 or not math.isfinite(entry_price) or entry_price <= 0:
        return None

    raw_side = str(raw.get("positionSide") or raw.get("ps") or "BOTH").upper()
    side = raw_side if raw_side in ("LONG", "SHORT") else ("LONG" if amount > 0 else "SHORT")
    persisted = persisted or {}
    return _TrackedPosition(
        user_id=user_id,
        symbol=symbol,
        side=side,
        quantity=abs(amount),
        entry_price=entry_price,
        live_mfe_usdc=max(float(persisted.get("live_mfe_usdc") or 0), 0),
        live_mae_usdc=max(float(persisted.get("live_mae_usdc") or 0), 0),
    )


def _make_listener(symbol: str):
    def on_mark_price(payload: dict) -> None:
        try:
            mark_price = float(payload.get("markPrice") or 0)
        except (TypeError, ValueError):
            return
        if not math.isfinite(mark_price) or mark_price <= 0:
            return

        with _lock:
            tracked = [
                position
                for positions in _positions_by_user.values()
                for position in positions.values()
                if position.symbol == symbol
            ]

        for position in tracked:
            if position.side == "LONG":
                unrealized_pnl = position.quantity * (mark_price - position.entry_price)
            else:
                unrealized_pnl = position.quantity * (position.entry_price - mark_price)

            next_mfe = max(position.live_mfe_usdc, unrealized_pnl, 0)
            next_mae = max(position.live_mae_usdc, -unrealized_pnl, 0)
            if next_mfe <= position.live_mfe_usdc and next_mae <= position.live_mae_usdc:
                continue
            try:
                db_module.update_open_position_live_excursion(
                    user_id=position.user_id,
                    symbol=position.symbol,
                    position_side=position.side,
                    unrealized_pnl=unrealized_pnl,
                )
            except Exception:
                logger.exception(
                    "[POSITION_MARK] phase=persist_error user_id=%s symbol=%s side=%s",
                    position.user_id,
                    position.symbol,
                    position.side,
                )
                continue

            with _lock:
                current = _positions_by_user.get(position.user_id, {}).get((position.symbol, position.side))
                if current is not None:
                    current.live_mfe_usdc = max(current.live_mfe_usdc, next_mfe)
                    current.live_mae_usdc = max(current.live_mae_usdc, next_mae)

    return on_mark_price


def replace_user_positions(user_id: int, positions_payload: list[dict]) -> None:
    """Replace one user's tracked positions and reconcile symbol subscriptions."""
    persisted_rows = db_module.get_positions(user_id=user_id, status="OPEN")
    persisted_by_key = {
        (str(row.get("symbol") or "").upper(), str(row.get("position_side") or "").upper()): row
        for row in persisted_rows
    }
    next_positions: dict[tuple[str, str], _TrackedPosition] = {}
    for raw in positions_payload:
        symbol = str(raw.get("symbol") or raw.get("s") or "").strip().upper()
        raw_side = str(raw.get("positionSide") or raw.get("ps") or "BOTH").upper()
        raw_amount = raw.get("positionAmt") if "positionAmt" in raw else raw.get("pa")
        try:
            amount = float(raw_amount or 0)
        except (TypeError, ValueError):
            continue
        side = raw_side if raw_side in ("LONG", "SHORT") else ("LONG" if amount > 0 else "SHORT")
        tracked = _normalized_position(raw, user_id, persisted_by_key.get((symbol, side)))
        if tracked is not None:
            next_positions[(tracked.symbol, tracked.side)] = tracked

    with _lock:
        if next_positions:
            _positions_by_user[user_id] = next_positions
        else:
            _positions_by_user.pop(user_id, None)
        desired_symbols = {
            position.symbol
            for positions in _positions_by_user.values()
            for position in positions.values()
        }
        current_symbols = set(_listeners_by_symbol)
        removed = [(symbol, _listeners_by_symbol.pop(symbol)) for symbol in current_symbols - desired_symbols]
        added = []
        for symbol in desired_symbols - current_symbols:
            listener = _make_listener(symbol)
            _listeners_by_symbol[symbol] = listener
            added.append((symbol, listener))

    for symbol, listener in removed:
        unregister_public_mark_price_listener(symbol, listener)
        logger.info("[POSITION_MARK] phase=unsubscribe symbol=%s", symbol)
    for symbol, listener in added:
        register_public_mark_price_listener(symbol, listener)
        logger.info("[POSITION_MARK] phase=subscribe symbol=%s", symbol)


def retain_users(active_user_ids: set[int]) -> None:
    """Drop subscriptions belonging to users no longer active/configured."""
    with _lock:
        stale_user_ids = set(_positions_by_user) - {int(value) for value in active_user_ids}
    for user_id in stale_user_ids:
        replace_user_positions(user_id, [])


def stop_position_mark_price_tracking() -> None:
    with _lock:
        listeners = list(_listeners_by_symbol.items())
        _listeners_by_symbol.clear()
        _positions_by_user.clear()
    for symbol, listener in listeners:
        unregister_public_mark_price_listener(symbol, listener)

