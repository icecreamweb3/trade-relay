"""TP/SL order placement helpers shared by routers and status streams."""

from __future__ import annotations

from typing import Optional

from trade_relay import config as cfg
from trade_relay import database as db
from trade_relay.exchange.binance_client import BinanceClient


def validate_tpsl_prices(
    *,
    position_side: str,
    entry_price: Optional[float],
    tp_price: Optional[float],
    sl_price: Optional[float],
) -> list[str]:
    errors: list[str] = []
    if entry_price is None or entry_price <= 0:
        return errors

    side = str(position_side or "").upper()
    if side == "LONG":
        if tp_price is not None and tp_price > 0 and tp_price <= entry_price:
            errors.append(f"LONG 仓位的止盈价 ({tp_price}) 必须高于入场价 ({entry_price})")
        if sl_price is not None and sl_price > 0 and sl_price >= entry_price:
            errors.append(f"LONG 仓位的止损价 ({sl_price}) 必须低于入场价 ({entry_price})")
    elif side == "SHORT":
        if tp_price is not None and tp_price > 0 and tp_price >= entry_price:
            errors.append(f"SHORT 仓位的止盈价 ({tp_price}) 必须低于入场价 ({entry_price})")
        if sl_price is not None and sl_price > 0 and sl_price <= entry_price:
            errors.append(f"SHORT 仓位的止损价 ({sl_price}) 必须高于入场价 ({entry_price})")
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
) -> list[str]:
    errors = validate_tpsl_prices(
        position_side=position_side,
        entry_price=entry_price,
        tp_price=tp_price,
        sl_price=sl_price,
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

    if tp_price is not None and tp_price > 0:
        try:
            tp_resp = client.place_take_profit_order(
                symbol=symbol,
                side=close_side,
                price=tp_price,
                quantity=quantity,
                position_side=position_side,
            )
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
                binance_order_id=tp_exchange_id or None,
                client_order_id=tp_client_id or None,
                trade_direction="CLOSE",
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
                position_id=position_id,
                reduce_only=True,
                order_category="Conditional",
            )

    if sl_price is not None and sl_price > 0:
        try:
            sl_resp = client.place_stop_loss_order(
                symbol=symbol,
                side=close_side,
                stop_price=sl_price,
                quantity=quantity,
                position_side=position_side,
            )
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
                binance_order_id=sl_exchange_id or None,
                client_order_id=sl_client_id or None,
                trade_direction="CLOSE",
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
                position_id=position_id,
                reduce_only=True,
                order_category="Conditional",
            )

    return errors
