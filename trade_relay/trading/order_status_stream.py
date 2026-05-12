"""Background Binance user-data stream for real-time order status sync."""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time
from typing import Optional
from urllib.parse import urlparse

import websocket

from trade_relay import config as cfg
from trade_relay import database as db
from trade_relay.exchange.binance_client import BinanceClient

logger = logging.getLogger(__name__)

MAINNET_WS_URL = "wss://fstream.binance.com/ws/"
TESTNET_WS_URL = "wss://stream.binancefuture.com/ws/"


class UserOrderStatusStream:
    def __init__(self, username: str, api_key: str, api_secret: str, testnet: bool):
        self.username = username
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.client = BinanceClient(
            api_key=api_key,
            secret_key=api_secret,
            testnet=testnet,
        )
        self.listen_key: Optional[str] = None
        self.ws: Optional[websocket.WebSocketApp] = None
        self.ws_thread: Optional[threading.Thread] = None
        self.keepalive_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.running = False
        self.proxy_url = None
        if self.client.proxy_config:
            self.proxy_url = self.client.proxy_config.get("https") or self.client.proxy_config.get("http")

    def matches(self, api_key: str, api_secret: str, testnet: bool) -> bool:
        return self.api_key == api_key and self.api_secret == api_secret and self.testnet == testnet

    @property
    def ws_url(self) -> str:
        base = TESTNET_WS_URL if self.testnet else MAINNET_WS_URL
        return f"{base}{self.listen_key}"

    def start(self) -> None:
        if self.running and self.ws_thread and self.ws_thread.is_alive():
            return
        self.stop_event.clear()
        self.listen_key = self.client.start_user_data_stream()
        if not self.listen_key:
            logger.warning("Failed to start order status stream for user=%s: missing listenKey", self.username)
            return
        self.running = True
        self.ws_thread = threading.Thread(
            target=self._run_ws_loop,
            daemon=True,
            name=f"order-status-ws-{self.username}",
        )
        self.ws_thread.start()
        self.keepalive_thread = threading.Thread(
            target=self._keepalive_loop,
            daemon=True,
            name=f"order-status-keepalive-{self.username}",
        )
        self.keepalive_thread.start()
        logger.info("Started order status stream for user=%s", self.username)

    def stop(self) -> None:
        self.stop_event.set()
        self.running = False
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                logger.debug("Failed to close ws for user=%s", self.username, exc_info=True)
        if self.listen_key:
            try:
                self.client.close_user_data_stream(self.listen_key)
            except Exception:
                logger.debug("Failed to close listenKey for user=%s", self.username, exc_info=True)
            self.listen_key = None

    def sync_order_status(self, symbol: str, exchange_order_id: str) -> None:
        order = self.client.get_order_status(symbol, exchange_order_id)
        if not order:
            return
        self._persist_status(order)

    def _run_ws_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                run_kwargs = {
                    "sslopt": {"cert_reqs": ssl.CERT_NONE},
                    "ping_interval": 20,
                    "ping_timeout": 10,
                }
                parsed_proxy = _parse_proxy_url(self.proxy_url)
                if parsed_proxy:
                    run_kwargs.update(parsed_proxy)
                self.ws.run_forever(**run_kwargs)
            except Exception:
                logger.exception("Order status websocket loop failed for user=%s", self.username)
            if not self.stop_event.is_set():
                time.sleep(3)

    def _keepalive_loop(self) -> None:
        while not self.stop_event.wait(30 * 60):
            if not self.running or not self.listen_key:
                continue
            try:
                if not self.client.keepalive_user_data_stream(self.listen_key):
                    logger.warning("listenKey keepalive failed for user=%s", self.username)
            except Exception:
                logger.exception("listenKey keepalive exception for user=%s", self.username)

    def _on_message(self, _ws, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.debug("Invalid order status payload for user=%s: %s", self.username, message[:200])
            return
        if data.get("e") != "ORDER_TRADE_UPDATE":
            return
        order = data.get("o") or {}
        self._persist_status(order)

    def _persist_status(self, order: dict) -> None:
        exchange_order_id = str(order.get("i") or order.get("orderId") or "")
        if not exchange_order_id:
            return
        status = str(order.get("X") or order.get("status") or "").upper()
        filled_qty = _safe_float(order.get("z") or order.get("executedQty"))
        avg_price = _safe_float(order.get("ap") or order.get("avgPrice"))
        commission = _safe_float(order.get("n") or order.get("commission"))
        commission_asset = order.get("N") or order.get("commissionAsset")
        reject_reason = str(order.get("r") or "").strip()
        error_message = reject_reason if reject_reason and reject_reason != "NONE" else None

        updated = db.update_order_status_by_exchange_id(
            username=self.username,
            exchange_order_id=exchange_order_id,
            status=status or "NEW",
            filled_qty=filled_qty,
            avg_price=avg_price,
            commission=commission,
            commission_asset=commission_asset,
            error_message=error_message,
        )
        if updated:
            logger.info(
                "Synced order status from websocket: user=%s exchange_order_id=%s status=%s filled_qty=%s avg_price=%s",
                self.username,
                exchange_order_id,
                status,
                filled_qty,
                avg_price,
            )

    def _on_error(self, _ws, error) -> None:
        logger.warning("Order status websocket error for user=%s: %s", self.username, error)

    def _on_close(self, _ws, status_code, message) -> None:
        if not self.stop_event.is_set():
            logger.warning(
                "Order status websocket closed for user=%s: status=%s message=%s",
                self.username,
                status_code,
                message,
            )


_streams: dict[str, UserOrderStatusStream] = {}
_streams_lock = threading.Lock()


def ensure_user_order_status_stream(username: str, api_key: str, api_secret: str, testnet: bool) -> None:
    with _streams_lock:
        current = _streams.get(username)
        if current and current.matches(api_key, api_secret, testnet):
            stream = current
        else:
            if current:
                current.stop()
            stream = UserOrderStatusStream(username, api_key, api_secret, testnet)
            _streams[username] = stream
    stream.start()


def sync_order_status_once(username: str, api_key: str, api_secret: str, testnet: bool, symbol: str, exchange_order_id: str) -> None:
    ensure_user_order_status_stream(username, api_key, api_secret, testnet)
    with _streams_lock:
        stream = _streams.get(username)
    if stream:
        stream.sync_order_status(symbol, exchange_order_id)


def restore_active_order_status_streams() -> None:
    for username in db.get_active_order_usernames():
        api_key = cfg.get_api_key(username)
        api_secret = cfg.get_api_secret(username)
        if not api_key or not api_secret:
            continue
        ensure_user_order_status_stream(username, api_key, api_secret, cfg.is_testnet(username))


def stop_all_order_status_streams() -> None:
    with _streams_lock:
        streams = list(_streams.values())
        _streams.clear()
    for stream in streams:
        stream.stop()


def _safe_float(value) -> Optional[float]:
    if value in (None, "", 0, "0"):
        return 0.0 if value in (0, "0") else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_proxy_url(proxy_url: Optional[str]) -> Optional[dict]:
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    if not parsed.hostname or not parsed.port:
        return None
    proxy_type = parsed.scheme.lower() if parsed.scheme else "http"
    if proxy_type == "https":
        proxy_type = "http"
    return {
        "http_proxy_host": parsed.hostname,
        "http_proxy_port": parsed.port,
        "proxy_type": proxy_type,
    }