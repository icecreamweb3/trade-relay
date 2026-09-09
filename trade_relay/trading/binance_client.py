"""
Binance API wrapper supporting real, testnet, and mock modes.
"""
import logging
from typing import Optional
import asyncio
import inspect
import threading
import time
import uuid

from trade_relay.exchange.binance_client import BinanceClient as FuturesBinanceClient

logger = logging.getLogger(__name__)


_client_cache: dict[tuple[type, str, str, bool], FuturesBinanceClient] = {}
_client_cache_lock = threading.Lock()


def _get_cached_client(api_key: str, api_secret: str, testnet: bool) -> FuturesBinanceClient:
    """Reuse the authenticated HTTP session and timestamp offset across orders."""
    cache_key = (FuturesBinanceClient, api_key, api_secret, bool(testnet))
    with _client_cache_lock:
        cached = _client_cache.get(cache_key)
    if cached is not None:
        return cached

    created = FuturesBinanceClient(api_key=api_key, secret_key=api_secret, testnet=testnet)
    with _client_cache_lock:
        return _client_cache.setdefault(cache_key, created)


def get_order_by_client_id(
    api_key: str,
    api_secret: str,
    testnet: bool,
    symbol: str,
    client_order_id: str,
) -> Optional[dict]:
    """Query an order after an ambiguous submit response without creating a new session."""
    client = _get_cached_client(api_key, api_secret, testnet)
    return client.get_order_status_by_client_order_id(symbol, client_order_id)


class BinanceOrderResult:
    def __init__(
        self,
        success: bool,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        algo_client_id: Optional[str] = None,
        status: str = "FAILED",
        error: Optional[str] = None,
        mock: bool = False,
        uncertain: bool = False,
    ):
        self.success = success
        self.order_id = order_id
        self.client_order_id = client_order_id
        self.algo_client_id = algo_client_id
        self.status = status  # 'FILLED', 'NEW', 'FAILED', 'MOCK'
        self.error = error
        self.mock = mock
        self.uncertain = uncertain


def _call_with_client_order_id(method, args: tuple, client_order_id: Optional[str]):
    """Pass the idempotency key when supported, retaining lightweight test clients."""
    if client_order_id and "client_order_id" in inspect.signature(method).parameters:
        return method(*args, client_order_id=client_order_id)
    return method(*args)


def _place_order_sync(
    api_key: str,
    api_secret: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float] = None,
    stop_price: Optional[float] = None,
    post_only: bool = False,
    leverage: int = 10,
    testnet: bool = False,
    position_direction: str = 'OPEN',
    position_mode: Optional[str] = None,
    client_order_id: Optional[str] = None,
) -> BinanceOrderResult:
    """
    Place a real order on Binance (or testnet).
    Returns BinanceOrderResult.
    """
    try:
        started_at = time.monotonic()
        requested_client_order_id = client_order_id
        if order_type.upper() in ("MARKET", "LIMIT") and not requested_client_order_id:
            requested_client_order_id = f"tr_{uuid.uuid4().hex}"
        client = _get_cached_client(api_key, api_secret, testnet)
        client_ready_at = time.monotonic()

        requested_hedge_mode: Optional[bool] = None
        normalized_position_mode = str(position_mode or '').strip().upper()
        if normalized_position_mode in ('DUAL', 'HEDGE'):
            requested_hedge_mode = True
        elif normalized_position_mode in ('SINGLE', 'ONE_WAY', 'ONEWAY'):
            requested_hedge_mode = False

        if requested_hedge_mode is not None:
            current_position_mode = requested_hedge_mode
            remember_position_mode = getattr(client, "remember_position_mode", None)
            if callable(remember_position_mode):
                remember_position_mode(requested_hedge_mode)

        client.set_leverage(symbol, leverage)
        settings_ready_at = time.monotonic()

        is_close = position_direction.upper() == 'CLOSE'
        current_position_mode = requested_hedge_mode
        if current_position_mode is None and hasattr(client, 'get_position_mode'):
            current_position_mode = client.get_position_mode()

        # Derive positionSide only for hedge mode:
        # OPEN  + BUY  -> LONG  (open long)
        # OPEN  + SELL -> SHORT (open short)
        # CLOSE + SELL -> LONG  (sell to close long)
        # CLOSE + BUY  -> SHORT (buy to close short)
        position_side: Optional[str] = None
        if current_position_mode is True:
            if is_close:
                position_side = 'LONG' if side.upper() == 'SELL' else 'SHORT'
            else:
                position_side = 'LONG' if side.upper() == 'BUY' else 'SHORT'

        reduce_only = current_position_mode is False and is_close
        logger.info(
            'place_order: symbol=%s side=%s pos_dir=%s position_mode=%s -> positionSide=%s reduceOnly=%s type=%s qty=%s price=%s',
            symbol, side, position_direction, current_position_mode, position_side, reduce_only, order_type, quantity, price,
        )
        exchange_started_at = time.monotonic()

        if order_type == "LIMIT":
            if price is None:
                return BinanceOrderResult(success=False, error="Price required for LIMIT order")
            logger.info(
                'binance request | LIMIT order | symbol=%s side=%s positionSide=%s qty=%s price=%s testnet=%s post_only=%s',
                symbol, side, position_side, quantity, price, testnet, post_only,
            )
            response = _call_with_client_order_id(
                client.place_limit_order,
                (symbol, side, quantity, price, position_side, post_only, None, reduce_only),
                requested_client_order_id,
            )
        elif order_type == "MARKET":
            logger.info(
                'binance request | MARKET order | symbol=%s side=%s positionSide=%s reduceOnly=%s qty=%s testnet=%s',
                symbol, side, position_side, reduce_only, quantity, testnet,
            )
            response = _call_with_client_order_id(
                client.place_market_order,
                (symbol, side, quantity, position_side, reduce_only),
                requested_client_order_id,
            )
        elif order_type == "STOP":
            # Trigger-limit conditional order: needs both stop_price (trigger) and price (limit)
            if stop_price is None or stop_price <= 0:
                return BinanceOrderResult(success=False, error="stop_price required for STOP (trigger-limit) order")
            if price is None or price <= 0:
                return BinanceOrderResult(success=False, error="price required for STOP (trigger-limit) order")
            logger.info(
                'binance request | STOP(trigger-limit) | symbol=%s side=%s positionSide=%s qty=%s stopPrice=%s price=%s testnet=%s',
                symbol, side, position_side, quantity, stop_price, price, testnet,
            )
            response = client.place_stop_limit_order(
                symbol, side, quantity, stop_price, price, position_side, reduce_only,
            )
        elif order_type == "STOP_MARKET":
            # Trigger-market conditional order: only stop_price (trigger), no limit price
            if stop_price is None or stop_price <= 0:
                return BinanceOrderResult(success=False, error="stop_price required for STOP_MARKET (trigger-market) order")
            logger.info(
                'binance request | STOP_MARKET(trigger-market) | symbol=%s side=%s positionSide=%s qty=%s stopPrice=%s testnet=%s',
                symbol, side, position_side, quantity, stop_price, testnet,
            )
            response = client.place_stop_loss_order(
                symbol, side, stop_price, quantity, position_side, reduce_only,
            )
        else:
            return BinanceOrderResult(success=False, error=f"Unsupported order type: {order_type}")

        exchange_finished_at = time.monotonic()
        logger.info('binance response | raw=%s', response)

        if response is None:
            logger.warning('binance response | empty response for symbol=%s side=%s', symbol, side)
            return BinanceOrderResult(success=False, error="Empty response from Binance Futures")

        if not isinstance(response, dict):
            logger.warning('binance response | unexpected type=%s symbol=%s side=%s raw=%s', type(response).__name__, symbol, side, response)
            return BinanceOrderResult(success=False, error=f"Unexpected response type from Binance Futures: {type(response).__name__}")

        if not response:
            logger.warning('binance response | empty json object for symbol=%s side=%s', symbol, side)
            return BinanceOrderResult(success=False, error="Empty JSON response from Binance Futures")

        if response.get("error"):
            error_msg = response.get("error_message") or str(response)
            logger.warning('binance response | error | symbol=%s side=%s error=%s', symbol, side, error_msg)
            return BinanceOrderResult(
                success=False,
                error=error_msg,
                client_order_id=requested_client_order_id,
                uncertain=bool(response.get("uncertain")),
            )

        order_id = str(response.get("orderId") or response.get("algoId") or response.get("clientAlgoId") or "")
        client_order_id = str(response.get("clientOrderId") or requested_client_order_id or "") or None
        algo_client_id = str(response.get("clientAlgoId") or "") or None
        status = response.get("status") or response.get("algoStatus") or "NEW"
        logger.info(
            'binance response | success | orderId=%s status=%s symbol=%s side=%s positionSide=%s',
            order_id, status, symbol, side, position_side,
        )
        logger.info(
            '[ORDER_TIMING] phase=exchange_complete symbol=%s type=%s client_ms=%.1f settings_ms=%.1f submit_ms=%.1f total_ms=%.1f',
            symbol,
            order_type,
            (client_ready_at - started_at) * 1000,
            (settings_ready_at - client_ready_at) * 1000,
            (exchange_finished_at - exchange_started_at) * 1000,
            (exchange_finished_at - started_at) * 1000,
        )

        return BinanceOrderResult(
            success=True,
            order_id=order_id,
            client_order_id=client_order_id,
            algo_client_id=algo_client_id,
            status=status,
        )

    except Exception as exc:
        elapsed_ms = (time.monotonic() - started_at) * 1000 if 'started_at' in locals() else 0.0
        logger.warning(
            '[ORDER_TIMING] phase=exchange_failed symbol=%s type=%s total_ms=%.1f error_type=%s',
            symbol,
            order_type,
            elapsed_ms,
            type(exc).__name__,
        )
        logger.exception('binance request | exception | symbol=%s side=%s type=%s qty=%s price=%s: %s',
                         symbol, side, order_type, quantity, price, exc)
        return BinanceOrderResult(success=False, error=str(exc))


async def place_order(
    api_key: str,
    api_secret: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float] = None,
    stop_price: Optional[float] = None,
    post_only: bool = False,
    leverage: int = 10,
    testnet: bool = False,
    position_direction: str = 'OPEN',
    position_mode: Optional[str] = None,
    client_order_id: Optional[str] = None,
) -> BinanceOrderResult:
    """Run the complete blocking Binance operation in one dedicated worker."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[BinanceOrderResult] = loop.create_future()

    def run() -> None:
        try:
            result = _place_order_sync(
                api_key,
                api_secret,
                symbol,
                side,
                order_type,
                quantity,
                price,
                stop_price,
                post_only,
                leverage,
                testnet,
                position_direction,
                position_mode,
                client_order_id,
            )
        except BaseException as exc:
            loop.call_soon_threadsafe(future.set_exception, exc)
        else:
            loop.call_soon_threadsafe(future.set_result, result)

    threading.Thread(target=run, name="binance-place-order", daemon=True).start()
    return await future


def place_order_mock(
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float] = None,
) -> BinanceOrderResult:
    """
    Simulate an order without touching Binance API.
    """
    import random
    import time

    fake_id = f"MOCK-{int(time.time())}-{random.randint(1000, 9999)}"
    return BinanceOrderResult(
        success=True,
        order_id=fake_id,
        status="MOCK",
        mock=True,
    )
