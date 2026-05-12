__all__ = ["BaseExchange", "BinanceClient", "BinanceExchange", "OrdersMonitor"]


def __getattr__(name: str):
	if name == "BaseExchange":
		from .base import BaseExchange
		return BaseExchange
	if name == "BinanceClient":
		from .binance_client import BinanceClient
		return BinanceClient
	if name == "BinanceExchange":
		from .binance_exchange import BinanceExchange
		return BinanceExchange
	if name == "OrdersMonitor":
		from .orders_monitor import OrdersMonitor
		return OrdersMonitor
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
