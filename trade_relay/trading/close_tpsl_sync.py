from __future__ import annotations

import logging
from typing import Optional

from trade_relay import database as db
from trade_relay.trading.tpsl_service import place_tp_sl_orders

logger = logging.getLogger(__name__)


def _safe_float(value) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize_position_mode(value: str | None) -> str:
    normalized = str(value or "").strip().upper()
    if normalized in ("DUAL", "HEDGE"):
        return "DUAL"
    if normalized in ("SINGLE", "ONE_WAY", "ONEWAY"):
        return "SINGLE"
    return "UNKNOWN"


def derive_position_side_from_close_order(order_row: dict) -> str:
    side = str(order_row.get("side") or "").strip().upper()
    if side == "SELL":
        return "LONG"
    if side == "BUY":
        return "SHORT"
    return "UNKNOWN"


def sync_close_tpsl_quantity(
    *,
    username: str,
    user_id: int,
    symbol: str,
    position_side: str,
    quantity: float,
    entry_price: Optional[float],
) -> list[str]:
    if quantity <= 0 or position_side not in ("LONG", "SHORT"):
        return []

    close_side = "SELL" if position_side == "LONG" else "BUY"
    position = db.get_position(user_id, symbol, position_side)
    position_id = int(position["id"]) if position and position.get("id") else None
    position_mode = normalize_position_mode((position or {}).get("position_mode"))
    if position_mode == "SINGLE":
        logger.debug(
            "Skip CLOSE TP/SL quantity refresh for single-position mode: user=%s symbol=%s side=%s qty=%s",
            username,
            symbol,
            position_side,
            quantity,
        )
        return []

    active_rows = db.query_orders(user_id=user_id, status="NEW", limit=500)

    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    needs_refresh = False

    for row in active_rows:
        if str(row.get("trade_direction") or "").upper() != "CLOSE":
            continue
        if str(row.get("symbol") or "").upper() != symbol.upper():
            continue
        if str(row.get("side") or "").upper() != close_side:
            continue

        order_type = str(row.get("order_type") or "").upper()
        if order_type not in {"TAKE_PROFIT_MARKET", "STOP_MARKET"}:
            continue

        row_quantity = _safe_float(row.get("quantity") or 0) or 0.0
        row_position_id = row.get("position_id")
        position_id_mismatch = (
            position_id is not None
            and row_position_id is not None
            and int(row_position_id) != position_id
        )

        if abs(row_quantity - quantity) > 0.0005 or position_id_mismatch:
            needs_refresh = True

        if order_type == "TAKE_PROFIT_MARKET":
            price = _safe_float(row.get("price") or 0)
            if price and price > 0:
                tp_price = price
        elif order_type == "STOP_MARKET":
            stop_price = _safe_float(row.get("stop_price") or 0)
            if stop_price and stop_price > 0:
                sl_price = stop_price

    if not needs_refresh or (tp_price is None and sl_price is None):
        return []

    errors = place_tp_sl_orders(
        username=username,
        user_id=user_id,
        symbol=symbol,
        position_side=position_side,
        quantity=quantity,
        entry_price=entry_price,
        tp_price=tp_price,
        sl_price=sl_price,
        position_id=position_id,
        position_mode=position_mode,
    )
    if errors:
        logger.warning(
            "Auto-refresh CLOSE TP/SL quantity failed: user=%s symbol=%s side=%s qty=%s errors=%s",
            username,
            symbol,
            position_side,
            quantity,
            "; ".join(errors),
        )
        return list(errors)

    logger.info(
        "Auto-refreshed CLOSE TP/SL quantity: user=%s symbol=%s side=%s qty=%s tp=%s sl=%s",
        username,
        symbol,
        position_side,
        quantity,
        tp_price,
        sl_price,
    )
    return []