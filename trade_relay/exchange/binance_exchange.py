"""
exchange/binance_exchange.py — Binance implementation of BaseExchange.

Kline data and trading operations both use BinanceClient (official python-binance SDK).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from .base import BaseExchange
from .binance_client import BinanceClient


INTERVAL_TO_MS: dict[str, int] = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000,
    "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000,
    "1d": 86_400_000,
}


class BinanceExchange(BaseExchange):
    """
    Concrete Binance exchange adapter using BinanceClient (official python-binance SDK).

    Both kline fetching and order operations go through BinanceClient.
    Credentials are optional for kline-only usage.

    Parameters
    ----------
    api_key : str, optional
        Binance API key.  Required for trading operations.
    secret_key : str, optional
        Binance secret key.  Required for trading operations.
    use_futures : bool
        Currently unused (kept for signature compatibility); always uses futures fapi.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        use_futures: bool = True,
    ) -> None:
        self._client: Optional[BinanceClient] = None
        self._api_key = api_key
        self._secret_key = secret_key

    # ------------------------------------------------------------------
    # Lazy-initialise BinanceClient only when trading ops are needed.
    # This avoids mandatory credentials for read-only kline usage.
    # ------------------------------------------------------------------
    @property
    def _trading_client(self) -> BinanceClient:
        if self._client is None:
            self._client = BinanceClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
            )
        return self._client

    # ------------------------------------------------------------------
    # BaseExchange implementation
    # ------------------------------------------------------------------

    def get_klines(
        self,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: Optional[datetime] = None,
        limit: int = 1000,
    ) -> list[KlineBar]:
        """
        Fetch historical klines via BinanceClient (pagination handled internally).

        Parameters
        ----------
        symbol   : e.g. 'BTCUSDT'
        interval : e.g. '1m', '5m', '1h', '1d'
        start_time, end_time : UTC datetime bounds (end_time defaults to now)
        limit    : max bars per HTTP request (Binance max 1500)
        """
        client = self._trading_client

        start_ms = int(start_time.timestamp() * 1000)
        end_ms = (
            int(end_time.timestamp() * 1000)
            if end_time
            else int(time.time() * 1000)
        )
        interval_ms = INTERVAL_TO_MS.get(interval, 60_000)

        bars: list[KlineBar] = []
        cursor = start_ms

        while cursor < end_ms:
            raw = client.get_kline_data(symbol=symbol, interval=interval, limit=limit)
            if not raw:
                break

            for row in raw:
                # Binance kline: [open_time, open, high, low, close, volume, ...]
                open_time_ms = int(row[0])
                if open_time_ms < start_ms or open_time_ms >= end_ms:
                    continue
                bars.append(KlineBar(
                    open_time=datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc).replace(tzinfo=None),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                ))

            # Advance cursor past the last returned bar
            last_open_ms = int(raw[-1][0])
            next_cursor = last_open_ms + interval_ms
            if next_cursor <= cursor:
                break  # no progress, avoid infinite loop
            cursor = next_cursor

            if len(raw) < limit:
                break  # last page

        return bars

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
    ) -> dict:
        """
        Place a market or limit order via BinanceClient.

        Parameters
        ----------
        symbol     : e.g. 'BTCUSDT'
        side       : 'BUY' | 'SELL'
        order_type : 'MARKET' | 'LIMIT'
        quantity   : order quantity
        price      : required for LIMIT orders
        """
        client = self._trading_client
        order_type_upper = order_type.upper()

        if order_type_upper == "MARKET":
            result = client.place_market_order(
                symbol=symbol,
                side=side.upper(),
                quantity=quantity,
            )
        elif order_type_upper == "LIMIT":
            if price is None:
                raise ValueError("price is required for LIMIT orders")
            result = client.place_limit_order(
                symbol=symbol,
                side=side.upper(),
                quantity=quantity,
                price=price,
            )
        else:
            raise ValueError(f"Unsupported order_type: {order_type!r}. Use 'MARKET' or 'LIMIT'.")

        return result or {}

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        """Cancel an open order by order_id."""
        result = self._trading_client.cancel_order(symbol=symbol, order_id=order_id)
        return result or {}

    def get_open_orders(self, symbol: str) -> list[dict]:
        """Return all open orders for *symbol*."""
        return self._trading_client.get_open_orders(symbol=symbol)

    def get_account_balance(self, asset: str = "USDT") -> float:
        """
        Return available balance for *asset* in the futures wallet.

        Fetches full account info and finds the matching asset balance.
        Returns 0.0 if the asset is not found.
        """
        info = self._trading_client.get_account_info()
        assets = info.get("assets", [])
        for entry in assets:
            if entry.get("asset", "").upper() == asset.upper():
                return float(entry.get("availableBalance", 0.0))
        return 0.0
