"""
Order management: places orders via Binance and persists them to the DB.
"""
import asyncio
import logging
from typing import Optional

from trade_relay.auth.manager import Session
from trade_relay import database as db
from trade_relay import config as cfg
from trade_relay.trading.binance_client import place_order, place_order_mock
from trade_relay.trading.order_status_stream import ensure_user_order_status_stream, sync_order_status_once
from trade_relay.i18n import t


_log = logging.getLogger(__name__)


class OrderResult:
    def __init__(self, success: bool, message: str, order_id: Optional[int] = None):
        self.success = success
        self.message = message
        self.order_id = order_id


async def submit_order(
    session: Session,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float] = None,
    stop_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    sl_price: Optional[float] = None,
    leverage: int = 10,
    position_direction: str = 'OPEN',
) -> OrderResult:
    """
    Validate, place, and record an order for the given session user.
    """
    symbol = symbol.strip().upper()
    side = side.upper()
    order_type = order_type.upper()

    if not symbol:
        return OrderResult(False, t("field_required", t("symbol")))
    if side not in ("BUY", "SELL"):
        return OrderResult(False, t("field_required", t("side")))
    if order_type not in ("MARKET", "LIMIT", "STOP", "STOP_MARKET"):
        return OrderResult(False, t("field_required", t("order_type")))
    # Truncate to step size 0.001 (BTC contract minimum)
    import math
    quantity = math.floor(quantity * 1000) / 1000
    if quantity <= 0:
        return OrderResult(False, t("field_required", t("quantity")))
    if order_type == "LIMIT" and (price is None or price <= 0):
        return OrderResult(False, t("field_required", t("price")))
    if order_type == "STOP" and (price is None or price <= 0):
        return OrderResult(False, t("field_required", t("price")))
    if order_type in ("STOP", "STOP_MARKET") and (stop_price is None or stop_price <= 0):
        return OrderResult(False, t("field_required", "stop_price"))
    if leverage <= 0:
        return OrderResult(False, "Invalid leverage")

    username = session.username
    _log.info(
        "[ORDER_FLOW] phase=validate_success user_id=%s username=%s symbol=%s side=%s order_type=%s qty=%s leverage=%s pos_dir=%s",
        session.user_id,
        username,
        symbol,
        side,
        order_type,
        quantity,
        leverage,
        position_direction,
    )

    # Determine execution mode
    mock = cfg.is_mock_mode(username)
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    testnet = False

    if mock:
        _log.info("[ORDER_FLOW] phase=submit_mock username=%s symbol=%s side=%s type=%s", username, symbol, side, order_type)
        result = place_order_mock(symbol, side, order_type, quantity, price)
    else:
        api_key = cfg.get_api_key(username)
        api_secret = cfg.get_api_secret(username)

        if not api_key or not api_secret:
            _log.warning("[ORDER_FLOW] phase=missing_credentials username=%s symbol=%s", username, symbol)
            return OrderResult(False, t("no_api_key"))

        testnet = cfg.is_testnet(username)
        _log.info("[ORDER_FLOW] phase=submit_exchange username=%s symbol=%s side=%s type=%s testnet=%s", username, symbol, side, order_type, testnet)
        result = await place_order(
            api_key=api_key,
            api_secret=api_secret,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            leverage=leverage,
            testnet=testnet,
            position_direction=position_direction,
        )

    # When closing a position, look up the matching DB position to record position_id
    position_id: Optional[int] = None
    if position_direction and position_direction.upper() == "CLOSE":
        # CLOSE + SELL closes LONG; CLOSE + BUY closes SHORT
        closing_position_side = "LONG" if side.upper() == "SELL" else "SHORT"
        try:
            pos_row = db.get_position(session.user_id, symbol, closing_position_side)
            if pos_row:
                position_id = int(pos_row["id"])
        except Exception:
            pass  # non-critical; proceed without position_id

    order_category = "Conditional" if order_type in ("STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET") else "Basic"

    # Persist order record
    order_db_id = db.create_order(
        user_id=session.user_id,
        username=username,
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        stop_price=stop_price,
        tp_price=tp_price,
        sl_price=sl_price,
        status=result.status if result.success else "FAILED",
        binance_order_id=None if order_category == "Conditional" else result.order_id,
        algo_id=result.order_id if order_category == "Conditional" else None,
        algo_client_id=result.algo_client_id if order_category == "Conditional" else None,
        client_order_id=result.client_order_id,
        error_message=result.error,
        trade_direction=position_direction.upper() if position_direction else None,
        position_id=position_id,
        reduce_only=(position_direction or "").upper() == "CLOSE",
        post_only=False,
        order_category=order_category,
    )
    _log.info(
        "[ORDER_FLOW] phase=db_recorded username=%s order_db_id=%s exchange_order_id=%s algo_id=%s status=%s success=%s",
        username,
        order_db_id,
        None if order_category == "Conditional" else result.order_id,
        result.order_id if order_category == "Conditional" else None,
        result.status if hasattr(result, 'status') else None,
        result.success,
    )

    if result.success and not mock and result.order_id and api_key and api_secret and order_category == "Basic":
        # Start the per-user user-data stream and do one immediate REST sync to
        # close the race where an order fills before the websocket is fully up.
        ensure_user_order_status_stream(username, api_key, api_secret, testnet)
        try:
            sync_order_status_once(username, api_key, api_secret, testnet, symbol, str(result.order_id))
            _log.info("[ORDER_FLOW] phase=post_submit_sync username=%s symbol=%s exchange_order_id=%s", username, symbol, result.order_id)
        except Exception:
            _log.exception("[ORDER_FLOW] phase=post_submit_sync_error username=%s symbol=%s exchange_order_id=%s", username, symbol, result.order_id)

    # Log operation
    if result.success:
        db.log_operation(
            session.user_id,
            username,
            "PLACE_ORDER",
            f"{side} {quantity} {symbol} @ {'MARKET' if order_type == 'MARKET' else price} "
            f"→ status={result.status} id={result.order_id}",
        )
        if result.mock:
            msg = t("order_mock", side, quantity, symbol, quantity)
        else:
            msg = t("order_success", result.order_id)
        _log.info("[ORDER_FLOW] phase=return_success username=%s order_db_id=%s message=%s", username, order_db_id, msg)
        return OrderResult(True, msg, order_db_id)
    else:
        db.log_operation(
            session.user_id,
            username,
            "ORDER_FAILED",
            f"{side} {quantity} {symbol}: {result.error}",
        )
        _log.warning("[ORDER_FLOW] phase=return_failed username=%s order_db_id=%s error=%s", username, order_db_id, result.error)
        return OrderResult(False, t("order_failed", result.error), order_db_id)
