"""
Binance API wrapper supporting real, testnet, and mock modes.
"""
from typing import Optional
import asyncio


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
    testnet: bool = False,
) -> BinanceOrderResult:
    """
    Place a real order on Binance (or testnet).
    Returns BinanceOrderResult.
    """
    try:
        from binance import AsyncClient  # type: ignore
    except ImportError:
        return BinanceOrderResult(
            success=False,
            error="python-binance not installed. Run: pip install python-binance",
        )

    client = None
    try:
        client = await AsyncClient.create(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet,
        )

        params: dict = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": str(quantity),
        }

        if order_type == "LIMIT":
            if price is None:
                return BinanceOrderResult(success=False, error="Price required for LIMIT order")
            params["price"] = str(price)
            params["timeInForce"] = "GTC"

        response = await client.create_order(**params)

        order_id = str(response.get("orderId", ""))
        status = response.get("status", "NEW")

        return BinanceOrderResult(
            success=True,
            order_id=order_id,
            status=status,
        )

    except Exception as exc:
        return BinanceOrderResult(success=False, error=str(exc))
    finally:
        if client is not None:
            try:
                await client.close_connection()
            except Exception:
                pass


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
