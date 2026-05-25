"""
Binance API wrapper supporting real, testnet, and mock modes.
"""
import logging
from typing import Optional
import asyncio

from trade_relay.exchange.binance_client import BinanceClient as FuturesBinanceClient

logger = logging.getLogger(__name__)


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
    ):
        self.success = success
        self.order_id = order_id
        self.client_order_id = client_order_id
        self.algo_client_id = algo_client_id
        self.status = status  # 'FILLED', 'NEW', 'FAILED', 'MOCK'
        self.error = error
        self.mock = mock


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
) -> BinanceOrderResult:
    """
    Place a real order on Binance (or testnet).
    Returns BinanceOrderResult.
    """
    try:
        client = FuturesBinanceClient(
            api_key=api_key,
            secret_key=api_secret,
            testnet=testnet,
        )

        requested_hedge_mode: Optional[bool] = None
        normalized_position_mode = str(position_mode or '').strip().upper()
        if normalized_position_mode in ('DUAL', 'HEDGE'):
            requested_hedge_mode = True
        elif normalized_position_mode in ('SINGLE', 'ONE_WAY', 'ONEWAY'):
            requested_hedge_mode = False

        if requested_hedge_mode is not None:
            current_position_mode = await asyncio.to_thread(client.get_position_mode)
            if current_position_mode is None or current_position_mode != requested_hedge_mode:
                updated = await asyncio.to_thread(client.set_position_mode, requested_hedge_mode)
                if not updated:
                    return BinanceOrderResult(success=False, error=f"Failed to switch position mode to {normalized_position_mode}")

        await asyncio.to_thread(client.set_leverage, symbol, leverage)

        is_close = position_direction.upper() == 'CLOSE'
        current_position_mode = requested_hedge_mode
        if current_position_mode is None and hasattr(client, 'get_position_mode'):
            current_position_mode = await asyncio.to_thread(client.get_position_mode)

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

        if order_type == "LIMIT":
            if price is None:
                return BinanceOrderResult(success=False, error="Price required for LIMIT order")
            logger.info(
                'binance request | LIMIT order | symbol=%s side=%s positionSide=%s qty=%s price=%s testnet=%s post_only=%s',
                symbol, side, position_side, quantity, price, testnet, post_only,
            )
            response = await asyncio.to_thread(
                client.place_limit_order,
                symbol,
                side,
                quantity,
                price,
                position_side,
                post_only,
                None,
                reduce_only,
            )
        elif order_type == "MARKET":
            logger.info(
                'binance request | MARKET order | symbol=%s side=%s positionSide=%s reduceOnly=%s qty=%s testnet=%s',
                symbol, side, position_side, reduce_only, quantity, testnet,
            )
            response = await asyncio.to_thread(
                client.place_market_order,
                symbol,
                side,
                quantity,
                position_side,
                reduce_only,
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
            response = await asyncio.to_thread(
                client.place_stop_limit_order,
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
            response = await asyncio.to_thread(
                client.place_stop_loss_order,
                symbol, side, stop_price, quantity, position_side, reduce_only,
            )
        else:
            return BinanceOrderResult(success=False, error=f"Unsupported order type: {order_type}")

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
            )

        order_id = str(response.get("orderId") or response.get("algoId") or response.get("clientAlgoId") or "")
        client_order_id = str(response.get("clientOrderId") or "") or None
        algo_client_id = str(response.get("clientAlgoId") or "") or None
        status = response.get("status") or response.get("algoStatus") or "NEW"
        logger.info(
            'binance response | success | orderId=%s status=%s symbol=%s side=%s positionSide=%s',
            order_id, status, symbol, side, position_side,
        )

        return BinanceOrderResult(
            success=True,
            order_id=order_id,
            client_order_id=client_order_id,
            algo_client_id=algo_client_id,
            status=status,
        )

    except Exception as exc:
        logger.exception('binance request | exception | symbol=%s side=%s type=%s qty=%s price=%s: %s',
                         symbol, side, order_type, quantity, price, exc)
        return BinanceOrderResult(success=False, error=str(exc))


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
