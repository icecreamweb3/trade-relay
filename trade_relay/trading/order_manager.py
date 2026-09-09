"""
Order management: places orders via Binance and persists them to the DB.
"""
import asyncio
import logging
import threading
import time
from typing import Optional

from trade_relay.auth.manager import Session
from trade_relay import database as db
from trade_relay import config as cfg
from trade_relay.trading.binance_client import get_order_by_client_id, place_order, place_order_mock
from trade_relay.trading.order_status_stream import ensure_user_order_status_stream, sync_order_status_once
from trade_relay.i18n import t


_log = logging.getLogger(__name__)


def _post_submit_sync(
    username: str,
    api_key: str,
    api_secret: str,
    testnet: bool,
    symbol: str,
    exchange_order_id: str,
) -> None:
    started_at = time.monotonic()
    try:
        ensure_user_order_status_stream(username, api_key, api_secret, testnet)
        sync_order_status_once(username, api_key, api_secret, testnet, symbol, exchange_order_id)
        _log.info(
            "[ORDER_TIMING] phase=post_submit_sync_complete username=%s symbol=%s exchange_order_id=%s duration_ms=%.1f",
            username,
            symbol,
            exchange_order_id,
            (time.monotonic() - started_at) * 1000,
        )
    except Exception:
        _log.exception(
            "[ORDER_FLOW] phase=post_submit_sync_error username=%s symbol=%s exchange_order_id=%s",
            username,
            symbol,
            exchange_order_id,
        )


def _schedule_post_submit_sync(
    username: str,
    api_key: str,
    api_secret: str,
    testnet: bool,
    symbol: str,
    exchange_order_id: str,
) -> None:
    threading.Thread(
        target=_post_submit_sync,
        args=(username, api_key, api_secret, testnet, symbol, exchange_order_id),
        name=f"post-submit-sync-{username}-{exchange_order_id}",
        daemon=True,
    ).start()


def _confirm_uncertain_order(
    order_db_id: int,
    username: str,
    api_key: str,
    api_secret: str,
    testnet: bool,
    symbol: str,
    client_order_id: str,
) -> None:
    """Resolve a timed-out submission without risking a duplicate order."""
    for delay_seconds in (0.0, 0.5, 1.0, 2.0, 4.0):
        if delay_seconds:
            time.sleep(delay_seconds)
        try:
            order = get_order_by_client_id(
                api_key,
                api_secret,
                testnet,
                symbol,
                client_order_id,
            )
        except Exception:
            _log.exception(
                "[ORDER_FLOW] phase=uncertain_lookup_error username=%s symbol=%s client_order_id=%s",
                username,
                symbol,
                client_order_id,
            )
            continue
        if not order:
            continue

        exchange_order_id = str(order.get("orderId") or "").strip()
        status = str(order.get("status") or "NEW").upper()
        if exchange_order_id:
            db.update_order_metadata(
                order_db_id,
                exchange_order_id=exchange_order_id,
                error_message="",
            )
        db.update_order_status(
            order_db_id,
            status,
            filled_qty=float(order.get("executedQty") or 0),
            avg_price=float(order.get("avgPrice") or 0) or None,
            error_message="",
        )
        _log.info(
            "[ORDER_FLOW] phase=uncertain_resolved username=%s symbol=%s client_order_id=%s exchange_order_id=%s status=%s",
            username,
            symbol,
            client_order_id,
            exchange_order_id,
            status,
        )
        if exchange_order_id:
            _schedule_post_submit_sync(
                username,
                api_key,
                api_secret,
                testnet,
                symbol,
                exchange_order_id,
            )
        return

    _log.error(
        "[ORDER_FLOW] phase=uncertain_unresolved username=%s symbol=%s client_order_id=%s order_db_id=%s",
        username,
        symbol,
        client_order_id,
        order_db_id,
    )


def _schedule_uncertain_confirmation(
    order_db_id: int,
    username: str,
    api_key: str,
    api_secret: str,
    testnet: bool,
    symbol: str,
    client_order_id: str,
) -> None:
    threading.Thread(
        target=_confirm_uncertain_order,
        args=(order_db_id, username, api_key, api_secret, testnet, symbol, client_order_id),
        name=f"confirm-order-{username}-{client_order_id}",
        daemon=True,
    ).start()


def _normalize_position_mode(value: Optional[str]) -> Optional[str]:
    normalized = str(value or '').strip().upper()
    if not normalized:
        return None
    if normalized in ('DUAL', 'HEDGE'):
        return 'DUAL'
    if normalized in ('SINGLE', 'ONE_WAY', 'ONEWAY'):
        return 'SINGLE'
    return None


class OrderResult:
    def __init__(
        self,
        success: bool,
        message: str,
        order_id: Optional[int] = None,
        pending_confirmation: bool = False,
    ):
        self.success = success
        self.message = message
        self.order_id = order_id
        self.pending_confirmation = pending_confirmation


def _coerce_legacy_submit_order_args(
    post_only: object,
    leverage: object,
    position_direction: object,
    position_mode: Optional[str],
) -> tuple[bool, int, str, Optional[str]]:
    """Support older positional callers that passed leverage before position_direction."""
    if (
        isinstance(post_only, (int, float))
        and not isinstance(post_only, bool)
        and isinstance(leverage, str)
        and isinstance(position_direction, str)
        and position_direction == 'OPEN'
        and position_mode is None
    ):
        return False, int(post_only), leverage, position_mode

    return bool(post_only), int(leverage), str(position_direction or 'OPEN').upper(), position_mode


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
    post_only: bool = False,
    leverage: int = 10,
    position_direction: str = 'OPEN',
    position_mode: Optional[str] = None,
) -> OrderResult:
    """
    Validate, place, and record an order for the given session user.
    """
    request_started_at = time.monotonic()
    post_only, leverage, position_direction, position_mode = _coerce_legacy_submit_order_args(
        post_only,
        leverage,
        position_direction,
        position_mode,
    )
    symbol = symbol.strip().upper()
    side = side.upper()
    order_type = order_type.upper()

    if not symbol:
        return OrderResult(False, t("field_required", t("symbol")))
    if side not in ("BUY", "SELL"):
        return OrderResult(False, t("field_required", t("side")))
    if order_type not in ("MARKET", "LIMIT", "STOP", "STOP_MARKET"):
        return OrderResult(False, t("field_required", t("order_type")))
    if post_only and order_type != "LIMIT":
        return OrderResult(False, "Post Only is only supported for LIMIT orders")
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

    normalized_position_mode = _normalize_position_mode(position_mode)

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
        exchange_started_at = time.monotonic()
        result = await place_order(
            api_key=api_key,
            api_secret=api_secret,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            post_only=post_only,
            leverage=leverage,
            testnet=testnet,
            position_direction=position_direction,
            position_mode=normalized_position_mode,
        )
        exchange_finished_at = time.monotonic()

    accepted = result.success or bool(getattr(result, "uncertain", False))

    # When closing a position, look up the matching DB position to record position_id
    position_id: Optional[int] = None
    if position_direction and position_direction.upper() == "CLOSE":
        # CLOSE + SELL closes LONG; CLOSE + BUY closes SHORT
        closing_position_side = "LONG" if side.upper() == "SELL" else "SHORT"
        try:
            pos_row = db.get_position(session.user_id, symbol, closing_position_side)
            if pos_row:
                position_id = int(pos_row["id"])
                if normalized_position_mode is None:
                    normalized_position_mode = _normalize_position_mode(pos_row.get("position_mode"))
        except Exception:
            pass  # non-critical; proceed without position_id

    order_category = "Conditional" if order_type in ("STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET") else "Basic"

    # Guard against the race where a fast-fill WS event arrives before this DB write
    # and adopt_external_order() has already created a row for this exchange_order_id.
    # In that case, correct the adopted row's metadata instead of inserting a duplicate.
    adopted_exchange_id = None if order_category == "Conditional" else (result.order_id and str(result.order_id))
    if adopted_exchange_id and result.success:
        existing = db.get_order_by_exchange_id(username, adopted_exchange_id)
        if existing and existing.get("source") == "external":
            # Row was pre-created by adopt_external_order; take ownership of it.
            order_db_id = int(existing["id"])
            db.update_order_metadata(
                order_db_id,
                trade_direction=position_direction.upper() if position_direction else None,
            )
            db.update_order_source(order_db_id, source="trade_relay")
            _log.info(
                "[ORDER_FLOW] phase=reclaim_adopted_row username=%s order_db_id=%s exchange_order_id=%s",
                username, order_db_id, adopted_exchange_id,
            )
        else:
            existing = None  # proceed to normal create_order below

    if not (adopted_exchange_id and result.success and existing):
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
            status="PENDING" if getattr(result, "uncertain", False) else result.status if result.success else "FAILED",
            binance_order_id=None if order_category == "Conditional" else result.order_id,
            algo_id=result.order_id if order_category == "Conditional" else None,
            algo_client_id=result.algo_client_id if order_category == "Conditional" else None,
            client_order_id=result.client_order_id,
            error_message=result.error,
            trade_direction=position_direction.upper() if position_direction else None,
            position_mode=normalized_position_mode or "UNKNOWN",
            position_id=position_id,
            reduce_only=(position_direction or "").upper() == "CLOSE",
            post_only=post_only,
            order_category=order_category,
        )
    _log.info(
        "[ORDER_FLOW] phase=db_recorded username=%s order_db_id=%s exchange_order_id=%s algo_id=%s status=%s success=%s",
        username,
        order_db_id,
        None if order_category == "Conditional" else result.order_id,
        result.order_id if order_category == "Conditional" else None,
        "PENDING" if getattr(result, "uncertain", False) else result.status if hasattr(result, 'status') else None,
        accepted,
    )

    # Post-create cleanup: the WS thread may have raced our INSERT and created an 'external'
    # duplicate row.  Delete any extra external rows for this exchange_order_id now that we
    # own the authoritative trade_relay row.
    if adopted_exchange_id and result.success and order_db_id:
        all_rows = db.get_all_orders_by_exchange_id(username, adopted_exchange_id)
        for dup in (all_rows or []):
            if int(dup["id"]) != order_db_id and str(dup.get("source")) == "external":
                db.delete_order_by_id(int(dup["id"]))
                _log.info(
                    "[ORDER_FLOW] phase=remove_external_duplicate username=%s dup_id=%s exchange_order_id=%s",
                    username, dup["id"], adopted_exchange_id,
                )

    if result.success and not mock and result.order_id and api_key and api_secret and order_category == "Basic":
        # Binance already accepted the order and the local record is durable.
        # Reconciliation must not delay the response shown to the trader.
        _schedule_post_submit_sync(
            username,
            api_key,
            api_secret,
            testnet,
            symbol,
            str(result.order_id),
        )

    if (
        getattr(result, "uncertain", False)
        and order_db_id
        and result.client_order_id
        and api_key
        and api_secret
        and order_category == "Basic"
    ):
        _schedule_uncertain_confirmation(
            order_db_id,
            username,
            api_key,
            api_secret,
            testnet,
            symbol,
            result.client_order_id,
        )

    # Log operation
    if accepted:
        db.log_operation(
            session.user_id,
            username,
            "PLACE_ORDER",
            f"{side} {quantity} {symbol} @ {'MARKET' if order_type == 'MARKET' else price} "
            f"→ status={'PENDING' if getattr(result, 'uncertain', False) else result.status} "
            f"id={result.order_id or result.client_order_id}",
        )
        if getattr(result, "uncertain", False):
            msg = t("order_pending_confirmation", result.client_order_id)
        elif result.mock:
            msg = t("order_mock", side, quantity, symbol, quantity)
        else:
            msg = t("order_success", result.order_id)
        finished_at = time.monotonic()
        if not mock:
            _log.info(
                "[ORDER_TIMING] phase=response_ready username=%s symbol=%s type=%s exchange_ms=%.1f persistence_ms=%.1f total_ms=%.1f",
                username,
                symbol,
                order_type,
                (exchange_finished_at - exchange_started_at) * 1000,
                (finished_at - exchange_finished_at) * 1000,
                (finished_at - request_started_at) * 1000,
            )
        _log.info("[ORDER_FLOW] phase=return_success username=%s order_db_id=%s message=%s", username, order_db_id, msg)
        return OrderResult(
            True,
            msg,
            order_db_id,
            pending_confirmation=bool(getattr(result, "uncertain", False)),
        )
    else:
        db.log_operation(
            session.user_id,
            username,
            "ORDER_FAILED",
            f"{side} {quantity} {symbol}: {result.error}",
        )
        _log.warning("[ORDER_FLOW] phase=return_failed username=%s order_db_id=%s error=%s", username, order_db_id, result.error)
        return OrderResult(False, t("order_failed", result.error), order_db_id)
