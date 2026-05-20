"""Backend-managed Binance public 24hr ticker websocket streams."""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time
from typing import Callable, Optional

import websocket

from trade_relay.exchange.ws_proxy import get_proxy_config

logger = logging.getLogger(__name__)

WS_MARKET_URL = "wss://fstream.binance.com/market/ws/"

TickerListener = Callable[[dict], None]


class PublicTicker24hStream:
    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        self.ws: Optional[websocket.WebSocketApp] = None
        self.ws_thread: Optional[threading.Thread] = None
        self.health_check_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.listeners: set[TickerListener] = set()
        self.last_payload: Optional[dict] = None
        self.use_proxy, self.proxy_host, self.proxy_port = get_proxy_config()
        self.running = False
        self.connected = False
        self.reconnecting = False
        self.reconnect_count = 0
        self.max_reconnect_attempts = 10
        self.reconnect_interval = 5
        self.connection_timeout = 5 * 60
        self.last_message_monotonic: float | None = None
        self.ping_interval = 20
        self.ping_timeout = 10

    @property
    def ws_url(self) -> str:
        return f"{WS_MARKET_URL}{self.symbol.lower()}@ticker"

    def start(self) -> None:
        if self.ws_thread and self.ws_thread.is_alive():
            return
        self.stop_event.clear()
        self.running = True
        self.reconnecting = False
        self.reconnect_count = 0
        self.ws_thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"ticker24h-{self.symbol.lower()}",
        )
        self.ws_thread.start()
        self.health_check_thread = threading.Thread(
            target=self._health_check_loop,
            daemon=True,
            name=f"ticker24h-health-{self.symbol.lower()}",
        )
        self.health_check_thread.start()
        if self.use_proxy:
            logger.info("Ticker24h websocket will use proxy %s:%s for symbol=%s", self.proxy_host, self.proxy_port, self.symbol)
        else:
            logger.info("Ticker24h websocket will connect directly for symbol=%s", self.symbol)

    def stop(self) -> None:
        self.running = False
        self.stop_event.set()
        self.connected = False
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None
        if self.ws_thread:
            self.ws_thread.join(timeout=2)
            self.ws_thread = None
        if self.health_check_thread:
            self.health_check_thread.join(timeout=2)
            self.health_check_thread = None

    def add_listener(self, listener: TickerListener) -> None:
        snapshot: Optional[dict]
        with self.lock:
            self.listeners.add(listener)
            snapshot = self.last_payload.copy() if self.last_payload else None
        if snapshot is not None:
            listener(snapshot)

    def remove_listener(self, listener: TickerListener) -> None:
        should_stop = False
        with self.lock:
            self.listeners.discard(listener)
            should_stop = not self.listeners
        if should_stop:
            self.stop()

    def listener_count(self) -> int:
        with self.lock:
            return len(self.listeners)

    def _emit(self, payload: dict) -> None:
        with self.lock:
            self.last_payload = payload
            listeners = list(self.listeners)
        for listener in listeners:
            try:
                listener(payload)
            except Exception:
                logger.exception("Ticker listener failed for symbol=%s", self.symbol)

    def _on_open(self, _ws) -> None:
        self.connected = True
        self.last_message_monotonic = time.monotonic()
        if self.reconnect_count > 0:
            logger.info("Ticker24h websocket reconnected for symbol=%s after %s attempt(s)", self.symbol, self.reconnect_count)
        self.reconnect_count = 0
        logger.info("Ticker24h websocket connected for symbol=%s url=%s", self.symbol, self.ws_url)

    def _on_error(self, _ws, error) -> None:
        logger.warning("Ticker24h websocket error for symbol=%s: %s", self.symbol, error)
        if self.running:
            self.reconnecting = True

    def _on_close(self, _ws, code, msg) -> None:
        self.connected = False
        self.ws = None
        logger.info("Ticker24h websocket closed for symbol=%s code=%s msg=%s", self.symbol, code, msg)
        if self.running and not self.stop_event.is_set():
            self.reconnecting = True

    def _on_message(self, _ws, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning("Failed to decode 24hrTicker websocket payload for symbol=%s", self.symbol)
            return

        if not isinstance(data, dict) or data.get("e") != "24hrTicker":
            return

        self.last_message_monotonic = time.monotonic()

        try:
            payload = {
                "type": "ticker24h",
                "symbol": str(data["s"]),
                "lastPrice": float(data["c"]),
                "priceChange": float(data["p"]),
                "priceChangePercent": float(data["P"]),
                "openPrice": float(data["o"]),
                "highPrice": float(data["h"]),
                "lowPrice": float(data["l"]),
                "volume": float(data["v"]),
                "quoteVolume": float(data["q"]),
                "openTime": int(data["O"]),
                "closeTime": int(data["C"]),
                "eventTime": int(data["E"]),
            }
        except (KeyError, TypeError, ValueError):
            logger.warning("Invalid 24hrTicker payload for symbol=%s", self.symbol, exc_info=True)
            return

        self._emit(payload)

    def _reconnect(self) -> None:
        if not self.running:
            return
        self.reconnecting = True
        self.last_message_monotonic = time.monotonic()
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass

    def _health_check_loop(self) -> None:
        while self.running and not self.stop_event.is_set():
            time.sleep(60)
            if not self.running or self.stop_event.is_set():
                break
            if self.last_message_monotonic is None:
                continue
            elapsed = time.monotonic() - self.last_message_monotonic
            if elapsed > self.connection_timeout:
                logger.warning(
                    "Ticker24h websocket idle timeout for symbol=%s after %.1fs; reconnecting",
                    self.symbol,
                    elapsed,
                )
                self._reconnect()

    def _run_loop(self) -> None:
        try:
            while self.running and not self.stop_event.is_set():
                try:
                    if self.reconnecting:
                        if self.reconnect_count >= self.max_reconnect_attempts:
                            logger.error(
                                "Ticker24h websocket reached max reconnect attempts for symbol=%s",
                                self.symbol,
                            )
                            self.running = False
                            break
                        self.reconnect_count += 1
                        logger.info(
                            "Reconnecting ticker24h websocket for symbol=%s (%s/%s)",
                            self.symbol,
                            self.reconnect_count,
                            self.max_reconnect_attempts,
                        )
                        time.sleep(self.reconnect_interval)

                    self.ws = websocket.WebSocketApp(
                        self.ws_url,
                        on_open=self._on_open,
                        on_message=self._on_message,
                        on_error=self._on_error,
                        on_close=self._on_close,
                    )
                    self.reconnecting = False
                    run_kwargs: dict[str, object] = {
                        "sslopt": {"cert_reqs": ssl.CERT_NONE},
                        "ping_interval": self.ping_interval,
                        "ping_timeout": self.ping_timeout,
                    }
                    if self.use_proxy and self.proxy_host and self.proxy_port:
                        run_kwargs["http_proxy_host"] = self.proxy_host
                        run_kwargs["http_proxy_port"] = self.proxy_port
                        run_kwargs["proxy_type"] = "http"
                    self.ws.run_forever(**run_kwargs)
                    if self.running and not self.stop_event.is_set():
                        logger.warning("Ticker24h websocket disconnected for symbol=%s; reconnecting", self.symbol)
                        self.reconnecting = True
                except websocket.WebSocketException as exc:
                    logger.warning("Ticker24h websocket exception for symbol=%s: %s", self.symbol, exc)
                    if self.running:
                        self.reconnecting = True
                except Exception:
                    logger.exception("Ticker24h websocket crashed for symbol=%s", self.symbol)
                    if self.running:
                        self.reconnecting = True
                time.sleep(1)
        finally:
            self.running = False
            self.connected = False
            self.reconnecting = False


_streams: dict[str, PublicTicker24hStream] = {}
_streams_lock = threading.Lock()


def _get_or_create_stream(symbol: str) -> PublicTicker24hStream:
    normalized_symbol = symbol.upper()
    with _streams_lock:
        stream = _streams.get(normalized_symbol)
        if stream is None:
            stream = PublicTicker24hStream(normalized_symbol)
            _streams[normalized_symbol] = stream
        return stream


def register_public_ticker_listener(symbol: str, listener: TickerListener) -> None:
    stream = _get_or_create_stream(symbol)
    stream.add_listener(listener)
    stream.start()


def unregister_public_ticker_listener(symbol: str, listener: TickerListener) -> None:
    normalized_symbol = symbol.upper()
    with _streams_lock:
        stream = _streams.get(normalized_symbol)
    if stream is None:
        return

    stream.remove_listener(listener)

    with _streams_lock:
        if stream.listener_count() == 0 and _streams.get(normalized_symbol) is stream:
            _streams.pop(normalized_symbol, None)