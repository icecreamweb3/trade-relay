from __future__ import annotations

from datetime import datetime
import logging
from typing import Optional

from trade_relay import database as db


logger = logging.getLogger(__name__)

_UNASSIGNED_HISTORY_ORDER_WINDOW_SECONDS = 30 * 60


def _safe_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _derive_close_position_side(order_row: dict) -> str | None:
    if str(order_row.get("trade_direction") or "").upper() != "CLOSE":
        return None
    return "LONG" if str(order_row.get("side") or "").upper() == "SELL" else "SHORT"


def _safe_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _order_close_price(order_row: dict) -> float:
    close_price = _safe_float(order_row.get("avg_price"))
    if close_price > 0:
        return close_price
    return _safe_float(order_row.get("price"))


def _is_unassigned_history_row_for_order(row: dict, order_row: dict) -> bool:
    row_created_at = _safe_datetime(row.get("created_at"))
    order_created_at = _safe_datetime(order_row.get("created_at"))
    order_updated_at = _safe_datetime(order_row.get("updated_at"))
    order_close_price = _order_close_price(order_row)
    row_close_price = _safe_float(row.get("close_price"))

    if row_created_at and order_created_at:
        if abs((row_created_at - order_created_at).total_seconds()) <= _UNASSIGNED_HISTORY_ORDER_WINDOW_SECONDS:
            return True
    if row_created_at and order_updated_at:
        if abs((row_created_at - order_updated_at).total_seconds()) <= _UNASSIGNED_HISTORY_ORDER_WINDOW_SECONDS:
            return True
    if order_close_price > 0 and row_close_price > 0:
        tolerance = max(0.01, abs(order_close_price) * 1e-4)
        if abs(row_close_price - order_close_price) <= tolerance:
            return True
    return False


def _create_missing_position_history_from_close_order(
    order_row: dict,
    total_qty: float,
    total_realized_pnl: float,
    total_commission: float,
    commission_asset: str | None,
) -> int:
    user_id = int(order_row.get("user_id") or 0)
    username = str(order_row.get("username") or "").strip()
    symbol = str(order_row.get("symbol") or "").upper().strip()
    position_side = _derive_close_position_side(order_row)
    close_price = _order_close_price(order_row)

    if user_id <= 0 or not username or not symbol or position_side not in {"LONG", "SHORT"}:
        return 0
    if total_qty <= 0 or close_price <= 0:
        return 0

    if position_side == "LONG":
        entry_price = close_price - (total_realized_pnl / total_qty)
    else:
        entry_price = close_price + (total_realized_pnl / total_qty)

    db.add_position_history(
        user_id=user_id,
        username=username,
        symbol=symbol,
        side=position_side,
        entry_price=entry_price,
        close_price=close_price,
        quantity=total_qty,
        realized_pnl=total_realized_pnl,
        commission=total_commission,
        commission_asset=commission_asset,
        position_id=int(order_row["position_id"]) if order_row.get("position_id") else None,
        position_mode=str(order_row.get("position_mode") or "UNKNOWN").upper(),
    )
    logger.info(
        "[ORDER_FLOW] phase=position_history_backfilled_from_close_order order_id=%s exchange_order_id=%s user_id=%s symbol=%s side=%s qty=%s entry=%s close=%s",
        order_row.get("id"),
        order_row.get("exchange_order_id"),
        user_id,
        symbol,
        position_side,
        total_qty,
        entry_price,
        close_price,
    )
    return 1


def _select_related_position_history_rows(order_row: dict, total_qty: float) -> list[dict]:
    user_id = int(order_row.get("user_id") or 0)
    if user_id <= 0:
        return []

    symbol = str(order_row.get("symbol") or "").upper()
    position_side = _derive_close_position_side(order_row)
    if not symbol or position_side not in {"LONG", "SHORT"}:
        return []

    requested_position_id = int(order_row["position_id"]) if order_row.get("position_id") else None
    history_rows = db.get_position_history(user_id=user_id, limit=200)
    exact_rows = []
    fallback_rows = []
    for row in history_rows:
        if str(row.get("symbol") or "").upper() != symbol:
            continue
        if str(row.get("side") or "").upper() != position_side:
            continue
        row_position_id = int(row["position_id"]) if row.get("position_id") else None
        if requested_position_id is not None:
            if row_position_id == requested_position_id:
                exact_rows.append(row)
            elif row_position_id is None and _is_unassigned_history_row_for_order(row, order_row):
                fallback_rows.append(row)
            continue
        fallback_rows.append(row)

    matching_rows = exact_rows or fallback_rows

    if not matching_rows:
        return []

    if total_qty <= 0:
        return [matching_rows[0]]

    selected_rows: list[dict] = []
    cumulative_qty = 0.0
    for row in matching_rows:
        selected_rows.append(row)
        cumulative_qty += abs(_safe_float(row.get("quantity")))
        if cumulative_qty + 1e-9 >= total_qty:
            break

    return selected_rows or [matching_rows[0]]


def _normalize_commission_asset(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _resolve_filled_order_metrics(order_row: dict) -> tuple[float, float, float, str | None]:
    total_qty = abs(_safe_float(order_row.get("filled_qty")))
    if total_qty <= 0:
        total_qty = abs(_safe_float(order_row.get("quantity")))

    total_realized_pnl = _safe_float(order_row.get("realized_pnl"))
    total_commission = _safe_float(order_row.get("commission"))
    commission_asset = _normalize_commission_asset(order_row.get("commission_asset"))
    return total_qty, total_realized_pnl, total_commission, commission_asset


def _sync_position_history_metrics(
    order_row: dict,
    total_qty: float,
    total_realized_pnl: float,
    total_commission: float,
    commission_asset: str | None,
) -> int:
    selected_rows = _select_related_position_history_rows(order_row, total_qty)
    if not selected_rows:
        return _create_missing_position_history_from_close_order(
            order_row,
            total_qty,
            total_realized_pnl,
            total_commission,
            commission_asset,
        )

    current_realized = sum(_safe_float(row.get("realized_pnl")) for row in selected_rows)
    current_commission = sum(_safe_float(row.get("commission")) for row in selected_rows)
    delta_realized = total_realized_pnl - current_realized
    delta_commission = total_commission - current_commission
    asset_needs_update = commission_asset is not None and any(
        _normalize_commission_asset(row.get("commission_asset")) != commission_asset
        for row in selected_rows
    )

    if abs(delta_realized) < 1e-12 and abs(delta_commission) < 1e-12 and not asset_needs_update:
        return 0

    total_selected_qty = sum(abs(_safe_float(row.get("quantity"))) for row in selected_rows)
    remaining_realized = delta_realized
    remaining_commission = delta_commission
    updated_rows = 0

    for index, row in enumerate(selected_rows):
        current_row_realized = _safe_float(row.get("realized_pnl"))
        current_row_commission = _safe_float(row.get("commission"))
        if index == len(selected_rows) - 1:
            next_realized = current_row_realized + remaining_realized
            next_commission = current_row_commission + remaining_commission
        else:
            row_qty = abs(_safe_float(row.get("quantity")))
            share = (row_qty / total_selected_qty) if total_selected_qty > 0 else (1.0 / len(selected_rows))
            realized_piece = delta_realized * share
            commission_piece = delta_commission * share
            remaining_realized -= realized_piece
            remaining_commission -= commission_piece
            next_realized = current_row_realized + realized_piece
            next_commission = current_row_commission + commission_piece

        if db.update_position_history_values(
            int(row["id"]),
            realized_pnl=next_realized,
            commission=next_commission,
            commission_asset=commission_asset,
        ):
            updated_rows += 1

    return updated_rows


def sync_position_history_from_filled_close_order(order_row: Optional[dict]) -> int:
    if not order_row:
        return 0

    latest_order_row = dict(order_row)
    if order_row.get("id"):
        refreshed = db.get_order_by_id(int(order_row["id"]))
        if refreshed:
            latest_order_row = {
                **refreshed,
                **{key: value for key, value in order_row.items() if value is not None},
            }

    if str(latest_order_row.get("trade_direction") or "").upper() != "CLOSE":
        return 0

    total_qty, total_realized_pnl, total_commission, commission_asset = _resolve_filled_order_metrics(latest_order_row)
    return _sync_position_history_metrics(
        latest_order_row,
        total_qty,
        total_realized_pnl,
        total_commission,
        commission_asset,
    )


def sync_filled_order_trade_details(*, username: str, client, order_row: Optional[dict]) -> None:
    if not order_row:
        return

    latest_order_row = order_row
    if order_row.get("id"):
        refreshed = db.get_order_by_id(int(order_row["id"]))
        if refreshed:
            latest_order_row = refreshed

    trade_direction = str(latest_order_row.get("trade_direction") or "").upper()
    if trade_direction not in {"OPEN", "CLOSE"}:
        return

    exchange_order_id = str(latest_order_row.get("exchange_order_id") or "").strip()
    symbol = str(latest_order_row.get("symbol") or "").upper()
    if not exchange_order_id or not symbol:
        return
    if not hasattr(client, "get_trade_fills"):
        return

    trades = client.get_trade_fills(symbol, exchange_order_id)
    if not trades:
        return

    total_qty = sum(abs(_safe_float(trade.get("qty"))) for trade in trades)
    total_commission = sum(abs(_safe_float(trade.get("commission"))) for trade in trades)
    total_realized_pnl = sum(_safe_float(trade.get("realizedPnl")) for trade in trades)
    fill_times = [int(trade.get("time")) for trade in trades if trade.get("time") not in (None, "")]
    commission_assets = sorted({
        str(trade.get("commissionAsset") or "").strip()
        for trade in trades
        if str(trade.get("commissionAsset") or "").strip()
    })
    commission_asset = None
    if len(commission_assets) == 1:
        commission_asset = commission_assets[0]
    elif commission_assets:
        commission_asset = ",".join(commission_assets)

    update_kwargs = {
        "filled_qty": total_qty if total_qty > 0 else None,
        "commission": total_commission,
        "commission_asset": commission_asset,
        "filled_at": max(fill_times) if fill_times else None,
    }
    if trade_direction == "CLOSE":
        update_kwargs["realized_pnl"] = total_realized_pnl

    db.update_order_status(
        int(latest_order_row["id"]),
        str(latest_order_row.get("status") or "FILLED"),
        **update_kwargs,
    )
    if trade_direction == "CLOSE":
        sync_position_history_from_filled_close_order(
            {
                **latest_order_row,
                "filled_qty": total_qty if total_qty > 0 else latest_order_row.get("filled_qty"),
                "realized_pnl": total_realized_pnl,
                "commission": total_commission,
                "commission_asset": commission_asset,
            }
        )
    logger.info(
        "[ORDER_FLOW] phase=filled_order_trade_details_synced username=%s order_id=%s exchange_order_id=%s direction=%s qty=%s rpnl=%s commission=%s asset=%s",
        username,
        latest_order_row.get("id"),
        exchange_order_id,
        trade_direction,
        total_qty,
        total_realized_pnl if trade_direction == "CLOSE" else None,
        total_commission,
        commission_asset,
    )


def sync_close_order_trade_details(*, username: str, client, order_row: Optional[dict]) -> None:
    if not order_row:
        return
    if str(order_row.get("trade_direction") or "").upper() != "CLOSE":
        return
    sync_filled_order_trade_details(username=username, client=client, order_row=order_row)