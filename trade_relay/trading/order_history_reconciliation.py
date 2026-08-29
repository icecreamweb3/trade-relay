"""Reconcile a user's local order history with Binance Futures REST data."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from trade_relay import database as db
from trade_relay.trading.close_trade_sync import sync_position_history_from_filled_close_order


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _utc_from_ms(value: Any) -> datetime | None:
    try:
        return datetime.utcfromtimestamp(int(value) / 1000)
    except (TypeError, ValueError, OSError):
        return None


def _explicit_trade_direction(raw: dict) -> str | None:
    side = str(raw.get("side") or "").upper()
    position_side = str(raw.get("positionSide") or "BOTH").upper()
    if bool(raw.get("reduceOnly")):
        return "CLOSE"
    if position_side == "LONG":
        return "CLOSE" if side == "SELL" else "OPEN"
    if position_side == "SHORT":
        return "CLOSE" if side == "BUY" else "OPEN"
    return None


def reconcile_order_history(*, username: str, client, start_time: datetime, end_time: datetime) -> dict:
    # The order screen sends UTC database-filter values without a timezone
    # suffix. Treat naive values as UTC instead of the backend host timezone.
    start_utc = start_time.replace(tzinfo=timezone.utc) if start_time.tzinfo is None else start_time.astimezone(timezone.utc)
    end_utc = end_time.replace(tzinfo=timezone.utc) if end_time.tzinfo is None else end_time.astimezone(timezone.utc)
    start_ms = int(start_utc.timestamp() * 1000)
    end_ms = int(end_utc.timestamp() * 1000)
    warnings: list[str] = []

    trades: list[dict] = []
    try:
        trades = client.get_account_trades_range(start_ms, end_ms)
    except Exception as exc:
        warnings.append(f"Binance trade query failed: {exc}")

    symbols = set(db.get_order_symbols_for_user_range(username, start_time, end_time))
    symbols.update(str(trade.get("symbol") or "").upper() for trade in trades if trade.get("symbol"))
    if not symbols:
        raise ValueError("No trading symbols were found in the selected time range")

    trade_details: dict[str, dict] = defaultdict(lambda: {
        "quantity": 0.0,
        "quote": 0.0,
        "commission": 0.0,
        "commission_asset": None,
        "realized_pnl": 0.0,
        "time": None,
    })
    for trade in trades:
        order_id = str(trade.get("orderId") or "")
        if not order_id:
            continue
        detail = trade_details[order_id]
        qty = _float(trade.get("qty"))
        price = _float(trade.get("price"))
        detail["quantity"] += qty
        detail["quote"] += qty * price
        detail["commission"] += _float(trade.get("commission"))
        detail["realized_pnl"] += _float(trade.get("realizedPnl"))
        detail["commission_asset"] = detail["commission_asset"] or trade.get("commissionAsset")
        trade_time = trade.get("time")
        if trade_time and (detail["time"] is None or int(trade_time) > int(detail["time"])):
            detail["time"] = int(trade_time)

    exchange_orders: list[dict] = []
    failed = 0
    for symbol in sorted(symbols):
        try:
            exchange_orders.extend(client.get_all_orders_range(symbol, start_ms, end_ms))
        except Exception as exc:
            failed += 1
            warnings.append(f"{symbol}: {exc}")

    unique_orders: dict[str, dict] = {}
    for raw in exchange_orders:
        exchange_id = str(raw.get("orderId") or "")
        if exchange_id:
            unique_orders[exchange_id] = raw

    inserted = 0
    updated = 0
    unchanged = 0
    for exchange_id, raw in sorted(
        unique_orders.items(),
        key=lambda item: int(item[1].get("time") or item[1].get("updateTime") or 0),
    ):
        try:
            before = db.get_order_by_exchange_id(username, exchange_id)
            detail = trade_details.get(exchange_id) or {}
            executed_qty = _float(raw.get("executedQty")) or _float(detail.get("quantity"))
            avg_price = _float(raw.get("avgPrice"))
            if avg_price <= 0 and _float(detail.get("quantity")) > 0:
                avg_price = _float(detail.get("quote")) / _float(detail.get("quantity"))
            status = str(raw.get("status") or "NEW").upper()
            update_time = detail.get("time") or raw.get("updateTime") or raw.get("time")
            ws_like = {
                "i": exchange_id,
                "s": raw.get("symbol"),
                "S": raw.get("side"),
                "o": raw.get("origType") or raw.get("type") or "MARKET",
                "q": raw.get("origQty"),
                "p": raw.get("price"),
                "sp": raw.get("stopPrice"),
                "X": status,
                "c": raw.get("clientOrderId"),
                "z": executed_qty,
                "ap": avg_price,
                "R": raw.get("reduceOnly", False),
                "ps": raw.get("positionSide", "BOTH"),
                "O": raw.get("time"),
                "T": update_time,
            }
            order_id = db.adopt_external_order(username, exchange_id, ws_like)
            if order_id is None:
                raise RuntimeError("local order insert failed")

            changed = db.update_order_status(
                int(order_id),
                status,
                filled_qty=executed_qty,
                avg_price=avg_price if avg_price > 0 else None,
                filled_at=_utc_from_ms(update_time) if executed_qty > 0 else None,
                realized_pnl=_float(detail.get("realized_pnl")) if detail else None,
                commission=_float(detail.get("commission")) if detail else None,
                commission_asset=str(detail.get("commission_asset")) if detail.get("commission_asset") else None,
            )
            explicit_direction = _explicit_trade_direction(raw)
            if explicit_direction:
                changed = db.update_order_metadata(int(order_id), trade_direction=explicit_direction) or changed

            after = db.get_order_by_id(int(order_id))
            if before is None:
                inserted += 1
            elif changed:
                updated += 1
            else:
                unchanged += 1

            if after and status == "FILLED" and str(after.get("trade_direction") or "").upper() == "CLOSE":
                sync_position_history_from_filled_close_order(after)
        except Exception as exc:
            failed += 1
            if len(warnings) < 20:
                warnings.append(f"order {exchange_id}: {exc}")

    return {
        "username": username,
        "start_time": start_time.isoformat(sep=" "),
        "end_time": end_time.isoformat(sep=" "),
        "symbols": sorted(symbols),
        "scanned_orders": len(unique_orders),
        "scanned_trades": len(trades),
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "failed": failed,
        "warnings": warnings[:20],
    }
