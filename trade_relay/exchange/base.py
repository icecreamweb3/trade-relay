"""
exchange/base.py — Abstract interface for exchange adapters.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from core.models import KlineBar


class BaseExchange(ABC):
    """
    All exchange implementations must satisfy this interface.
    Methods are intentionally synchronous for REST calls;
    WebSocket streaming is handled by dedicated ws modules in live/.
    """

    @abstractmethod
    def get_klines(self, symbol: str, interval: str,
                   start_time: datetime, end_time: datetime | None = None,
                   limit: int = 1000) -> list[KlineBar]:
        """Fetch historical klines."""

    @abstractmethod
    def place_order(self, symbol: str, side: str, order_type: str,
                    quantity: float, price: float | None = None) -> dict:
        """
        Place an order.
        side: 'BUY' | 'SELL'
        order_type: 'MARKET' | 'LIMIT'
        Returns raw exchange response dict.
        """

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> dict:
        """Cancel an open order by order_id."""

    @abstractmethod
    def get_open_orders(self, symbol: str) -> list[dict]:
        """Return list of open orders for *symbol*."""

    @abstractmethod
    def get_account_balance(self, asset: str = "USDT") -> float:
        """Return available balance for *asset*."""
