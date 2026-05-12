"""
Binance API wrapper supporting real, testnet, and mock modes.
"""
from typing import Optional
import asyncio

from trade_relay.exchange.binance_client import BinanceClient as FuturesBinanceClient


class BinanceOrderResult:
    def __init__(
        self,
        success: bool,
        order_id: Optional[str] = None,
        status: str = "FAILED",
        error: Optional[str] = None,
        mock: bool = False,
    ):
        self.success = success
        self.order_id = order_id
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
    leverage: int = 10,
    testnet: bool = False,
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

        await asyncio.to_thread(client.set_leverage, symbol, leverage)

        if order_type == "LIMIT":
            if price is None:
                return BinanceOrderResult(success=False, error="Price required for LIMIT order")
            response = await asyncio.to_thread(
                client.place_limit_order,
                symbol,
                side,
                quantity,
                price,
            )
        elif order_type == "MARKET":
            response = await asyncio.to_thread(
                client.place_market_order,
                symbol,
                side,
                quantity,
            )
        else:
            return BinanceOrderResult(success=False, error=f"Unsupported order type: {order_type}")

        if not response:
            return BinanceOrderResult(success=False, error="Empty response from Binance Futures")

        if response.get("error"):
            return BinanceOrderResult(
                success=False,
                error=response.get("error_message") or str(response),
            )

        order_id = str(response.get("orderId", ""))
        status = response.get("status", "NEW")

        return BinanceOrderResult(
            success=True,
            order_id=order_id,
            status=status,
        )

    except Exception as exc:
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
