"""TP/SL order placement helpers shared by routers and status streams."""

from __future__ import annotations

from typing import Optional

from trade_relay import config as cfg
from trade_relay import database as db
from trade_relay.exchange.binance_client import BinanceClient


def _normalize_position_mode(value: str | None) -> str:
    normalized = str(value or "").strip().upper()
    if normalized in {"DUAL", "HEDGE"}:
        return "DUAL"
    if normalized in {"SINGLE", "ONE_WAY", "ONEWAY"}:
        return "SINGLE"
    return "UNKNOWN"


def _raise_for_failed_conditional_response(response: object, label: str) -> dict:
    if not isinstance(response, dict):
        raise Exception(f"{label} placement returned invalid response")

    if response.get("error"):
        raise Exception(str(response.get("error_message") or f"{label} placement failed"))

    return response


def _replace_existing_conditional_orders(
    *,
    client: BinanceClient,
    user_id: int,
    symbol: str,
    close_side: str,
    position_id: Optional[int],
    order_type: str,
) -> list[str]:
    errors: list[str] = []
    active_rows = db.query_orders(user_id=user_id, status="NEW", limit=500)
    matching_rows: list[dict] = []

    for row in active_rows:
        if str(row.get("order_type") or "").upper() != order_type:
            continue
        if str(row.get("trade_direction") or "").upper() != "CLOSE":
            continue
        if str(row.get("symbol") or "").upper() != symbol.upper():
            continue
        if str(row.get("side") or "").upper() != close_side:
            continue

        row_position_id = row.get("position_id")
        if position_id is not None and row_position_id is not None and int(row_position_id) != int(position_id):
            continue
        matching_rows.append(row)

    for row in matching_rows:
        algo_id = str(row.get("algo_id") or row.get("exchange_order_id") or "").strip()
        try:
            if algo_id:
                client.cancel_algo_order(int(algo_id), None, symbol)
            db.update_order_status(int(row["id"]), "CANCELED")
        except Exception as exc:
            errors.append(f"{order_type} replace: {exc}")

    return errors


def cancel_close_tp_sl_orders(
    *,
    client: BinanceClient,
    user_id: int,
    symbol: str,
    position_side: str,
    position_id: Optional[int] = None,
) -> list[str]:
    close_side = "SELL" if str(position_side).upper() == "LONG" else "BUY"
    errors: list[str] = []
    errors.extend(_replace_existing_conditional_orders(
        client=client,
        user_id=user_id,
        symbol=symbol,
        close_side=close_side,
        position_id=position_id,
        order_type="TAKE_PROFIT_MARKET",
    ))
    errors.extend(_replace_existing_conditional_orders(
        client=client,
        user_id=user_id,
        symbol=symbol,
        close_side=close_side,
        position_id=position_id,
        order_type="STOP_MARKET",
    ))
    return errors


def validate_tpsl_prices(
    *,
    position_side: str,
    entry_price: Optional[float],
    tp_price: Optional[float],
    sl_price: Optional[float],
    current_price: Optional[float] = None,
) -> list[str]:
    errors: list[str] = []
    side = str(position_side or "").upper()
    if current_price is None or current_price <= 0:
        return errors

    if side == "LONG":
        if tp_price is not None and tp_price > 0 and tp_price <= current_price:
            errors.append(f"LONG 仓位的止盈价 ({tp_price}) 必须高于当前价 ({current_price})")
        if sl_price is not None and sl_price > 0 and sl_price >= current_price:
            errors.append(f"LONG 仓位的止损价 ({sl_price}) 必须低于当前价 ({current_price})")
    elif side == "SHORT":
        if tp_price is not None and tp_price > 0 and tp_price >= current_price:
            errors.append(f"SHORT 仓位的止盈价 ({tp_price}) 必须低于当前价 ({current_price})")
        if sl_price is not None and sl_price > 0 and sl_price <= current_price:
            errors.append(f"SHORT 仓位的止损价 ({sl_price}) 必须高于当前价 ({current_price})")
    return errors


def place_tp_sl_orders(
    *,
    username: str,
    user_id: int,
    symbol: str,
    position_side: str,
    quantity: float,
    entry_price: Optional[float],
    tp_price: Optional[float],
    sl_price: Optional[float],
    position_id: Optional[int] = None,
    position_mode: str = "UNKNOWN",
    current_price: Optional[float] = None,
) -> list[str]:
    errors = validate_tpsl_prices(
        position_side=position_side,
        entry_price=entry_price,
        tp_price=tp_price,
        sl_price=sl_price,
        current_price=current_price,
    )
    if errors:
        return errors

    api_key = cfg.get_api_key(username)
    api_secret = cfg.get_api_secret(username)
    if not api_key or not api_secret:
        return ["No API credentials configured"]

    client = BinanceClient(
        api_key=api_key,
        secret_key=api_secret,
        testnet=cfg.is_testnet(username),
    )
    close_side = "SELL" if str(position_side).upper() == "LONG" else "BUY"
    normalized_position_mode = _normalize_position_mode(position_mode)
    use_close_all_conditional_orders = normalized_position_mode == "SINGLE"

    if tp_price is not None and tp_price > 0:
        errors.extend(_replace_existing_conditional_orders(
            client=client,
            user_id=user_id,
            symbol=symbol,
            close_side=close_side,
            position_id=position_id,
            order_type="TAKE_PROFIT_MARKET",
        ))
        if errors:
            return errors
        try:
            if use_close_all_conditional_orders:
                tp_resp = client.place_close_all_take_profit_order(
                    symbol=symbol,
                    side=close_side,
                    trigger_price=tp_price,
                )
            else:
                tp_resp = client.place_take_profit_order(
                    symbol=symbol,
                    side=close_side,
                    price=tp_price,
                    quantity=quantity,
                    position_side=position_side,
                )
            tp_resp = _raise_for_failed_conditional_response(tp_resp, "TP")
            tp_exchange_id = str(tp_resp.get("algoId", "") or tp_resp.get("orderId", "")) if isinstance(tp_resp, dict) else None
            tp_client_id = str(tp_resp.get("clientAlgoId", "") or tp_resp.get("clientOrderId", "")) if isinstance(tp_resp, dict) else None
            tp_status = tp_resp.get("status", "NEW") if isinstance(tp_resp, dict) else "NEW"
            db.create_order(
                user_id=user_id,
                username=username,
                symbol=symbol,
                side=close_side,
                order_type="TAKE_PROFIT_MARKET",
                quantity=quantity,
                price=tp_price,
                status=tp_status,
                binance_order_id=None,
                algo_id=tp_exchange_id or None,
                algo_client_id=tp_client_id or None,
                client_order_id=None,
                trade_direction="CLOSE",
                position_mode=normalized_position_mode,
                position_id=position_id,
                reduce_only=True,
                order_category="Conditional",
            )
        except Exception as exc:
            errors.append(f"TP: {exc}")
            db.create_order(
                user_id=user_id,
                username=username,
                symbol=symbol,
                side=close_side,
                order_type="TAKE_PROFIT_MARKET",
                quantity=quantity,
                price=tp_price,
                status="FAILED",
                error_message=str(exc),
                trade_direction="CLOSE",
                position_mode=normalized_position_mode,
                position_id=position_id,
                reduce_only=True,
                order_category="Conditional",
            )

    if sl_price is not None and sl_price > 0:
        errors.extend(_replace_existing_conditional_orders(
            client=client,
            user_id=user_id,
            symbol=symbol,
            close_side=close_side,
            position_id=position_id,
            order_type="STOP_MARKET",
        ))
        if errors:
            return errors
        try:
            if use_close_all_conditional_orders:
                sl_resp = client.place_close_all_stop_loss_order(
                    symbol=symbol,
                    side=close_side,
                    stop_price=sl_price,
                )
            else:
                sl_resp = client.place_stop_loss_order(
                    symbol=symbol,
                    side=close_side,
                    stop_price=sl_price,
                    quantity=quantity,
                    position_side=position_side,
                )
            sl_resp = _raise_for_failed_conditional_response(sl_resp, "SL")
            sl_exchange_id = str(sl_resp.get("algoId", "") or sl_resp.get("orderId", "")) if isinstance(sl_resp, dict) else None
            sl_client_id = str(sl_resp.get("clientAlgoId", "") or sl_resp.get("clientOrderId", "")) if isinstance(sl_resp, dict) else None
            sl_status = sl_resp.get("status", "NEW") if isinstance(sl_resp, dict) else "NEW"
            db.create_order(
                user_id=user_id,
                username=username,
                symbol=symbol,
                side=close_side,
                order_type="STOP_MARKET",
                quantity=quantity,
                price=None,
                stop_price=sl_price,
                status=sl_status,
                binance_order_id=None,
                algo_id=sl_exchange_id or None,
                algo_client_id=sl_client_id or None,
                client_order_id=None,
                trade_direction="CLOSE",
                position_mode=normalized_position_mode,
                position_id=position_id,
                reduce_only=True,
                order_category="Conditional",
            )
        except Exception as exc:
            errors.append(f"SL: {exc}")
            db.create_order(
                user_id=user_id,
                username=username,
                symbol=symbol,
                side=close_side,
                order_type="STOP_MARKET",
                quantity=quantity,
                price=None,
                stop_price=sl_price,
                status="FAILED",
                error_message=str(exc),
                trade_direction="CLOSE",
                position_mode=normalized_position_mode,
                position_id=position_id,
                reduce_only=True,
                order_category="Conditional",
            )

    return errors
