"""
Order management: places orders via Binance and persists them to the DB.
"""
import asyncio
from typing import Optional

from trade_relay.auth.manager import Session
from trade_relay import database as db
from trade_relay import config as cfg
from trade_relay.trading.binance_client import place_order, place_order_mock
from trade_relay.trading.order_status_stream import ensure_user_order_status_stream, sync_order_status_once
from trade_relay.i18n import t


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
    if order_type not in ("MARKET", "LIMIT"):
        return OrderResult(False, t("field_required", t("order_type")))
    # Truncate to step size 0.001 (BTC contract minimum)
    import math
    quantity = math.floor(quantity * 1000) / 1000
    if quantity <= 0:
        return OrderResult(False, t("field_required", t("quantity")))
    if order_type == "LIMIT" and (price is None or price <= 0):
        return OrderResult(False, t("field_required", t("price")))
    if leverage <= 0:
        return OrderResult(False, "Invalid leverage")

    username = session.username

    # Determine execution mode
    mock = cfg.is_mock_mode(username)
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    testnet = False

    if mock:
        result = place_order_mock(symbol, side, order_type, quantity, price)
    else:
        api_key = cfg.get_api_key(username)
        api_secret = cfg.get_api_secret(username)

        if not api_key or not api_secret:
            return OrderResult(False, t("no_api_key"))

        testnet = cfg.is_testnet(username)
        result = await place_order(
            api_key=api_key,
            api_secret=api_secret,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
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

    # Persist order record
    order_db_id = db.create_order(
        user_id=session.user_id,
        username=username,
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        status=result.status if result.success else "FAILED",
        binance_order_id=result.order_id,
        error_message=result.error,
        trade_direction=position_direction.upper() if position_direction else None,
        position_id=position_id,
        reduce_only=(position_direction or "").upper() == "CLOSE",
        post_only=False,
    )

    if result.success and not mock and result.order_id and api_key and api_secret:
        # Start the per-user user-data stream and do one immediate REST sync to
        # close the race where an order fills before the websocket is fully up.
        ensure_user_order_status_stream(username, api_key, api_secret, testnet)
        try:
            sync_order_status_once(username, api_key, api_secret, testnet, symbol, str(result.order_id))
        except Exception:
            pass

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
        return OrderResult(True, msg, order_db_id)
    else:
        db.log_operation(
            session.user_id,
            username,
            "ORDER_FAILED",
            f"{side} {quantity} {symbol}: {result.error}",
        )
        return OrderResult(False, t("order_failed", result.error), order_db_id)
