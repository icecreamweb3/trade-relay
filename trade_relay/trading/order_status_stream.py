"""Background Binance user-data stream for real-time order status sync."""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time
from typing import Callable, Optional
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
        event_type = str(data.get("e") or "")
        if event_type == "ORDER_TRADE_UPDATE":
            order = data.get("o") or {}
            self._persist_status(order)
            self._notify_listeners({
                "type": "order_update",
                "event": event_type,
                "symbol": order.get("s") or order.get("symbol"),
            })
            return
        if event_type == "ACCOUNT_UPDATE":
            account = data.get("a") or {}
            positions = account.get("P") or []
            self._sync_positions(positions)
            self._notify_listeners({
                "type": "account_update",
                "event": event_type,
                "reason": account.get("m"),
                "symbols": [str(position.get("s") or "") for position in positions if position.get("s")],
            })

    def _sync_positions(self, positions_payload: list[dict]) -> None:
        user = db.get_user_by_username(self.username)
        if not user:
            return

        user_id = int(user["id"])
        existing_rows = db.get_positions(user_id=user_id)
        existing_by_key = {
            (str(row.get("symbol") or ""), str(row.get("position_side") or "BOTH").upper()): row
            for row in existing_rows
        }

        for position in positions_payload:
            symbol = str(position.get("s") or position.get("symbol") or "")
            if not symbol:
                continue

            raw_side = str(position.get("ps") or position.get("positionSide") or "BOTH").upper()
            amount = _safe_float(position.get("pa") or position.get("positionAmt"))
            entry_price = _safe_float(position.get("ep") or position.get("entryPrice"))
            unrealized_pnl = _safe_float(position.get("up") or position.get("unrealizedProfit"))
            realized_pnl = _safe_float(position.get("cr") or position.get("realizedPnl")) or 0.0
            margin_type = str(position.get("mt") or position.get("marginType") or "CROSS").upper()

            normalized_side = raw_side
            if normalized_side not in ("LONG", "SHORT"):
                if amount is not None and amount > 0:
                    normalized_side = "LONG"
                elif amount is not None and amount < 0:
                    normalized_side = "SHORT"
                else:
                    normalized_side = "BOTH"

            if amount is None or amount == 0:
                delete_sides = {raw_side, normalized_side}
                if raw_side == "BOTH":
                    delete_sides.update({"LONG", "SHORT"})
                for side in delete_sides:
                    if side in ("LONG", "SHORT", "BOTH"):
                        db.delete_position(user_id, symbol, side)
                continue

            existing = existing_by_key.get((symbol, normalized_side)) or existing_by_key.get((symbol, raw_side)) or {}
            leverage = int(existing.get("leverage") or 1)

            db.upsert_position(
                user_id=user_id,
                username=self.username,
                symbol=symbol,
                quantity=abs(amount),
                avg_entry_price=entry_price,
                unrealized_pnl=unrealized_pnl,
                realized_pnl=realized_pnl,
                leverage=leverage,
                margin_type=margin_type if margin_type in ("ISOLATED", "CROSS") else "CROSS",
                position_side=normalized_side,
            )

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

    def _notify_listeners(self, event: dict) -> None:
        now = time.time()
        with _listeners_lock:
            last = _notify_last.get(self.username, 0.0)
            if now - last < _notify_cooldown:
                return
            _notify_last[self.username] = now
            listeners = list(_listeners.get(self.username, ()))
        logger.debug("Notifying %d listeners for user=%s event=%s", len(listeners), self.username, event.get("type"))
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                logger.debug("Failed to notify user stream listener for user=%s", self.username, exc_info=True)


_streams: dict[str, UserOrderStatusStream] = {}
_streams_lock = threading.Lock()
_listeners: dict[str, set[Callable[[dict], None]]] = {}
_listeners_lock = threading.Lock()
_notify_last: dict[str, float] = {}   # username → last notify timestamp
_notify_cooldown = 2.0                # minimum seconds between notifications


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


def sync_initial_positions_for_user(username: str, api_key: str, api_secret: str, testnet: bool) -> None:
    """Pull current positions from Binance for a single user and write to DB.
    Called when the user's WebSocket connection is established (they are authenticated).
    """
    try:
        client = BinanceClient(api_key=api_key, secret_key=api_secret, testnet=testnet)
        rows = client.get_position_information()
        open_rows = [r for r in rows if float(r.get("positionAmt", 0) or 0) != 0]
        logger.info("Initial position sync for user=%s: %d open positions", username, len(open_rows))
        stream = UserOrderStatusStream(username, api_key, api_secret, testnet)
        # Only sync rows with a non-zero position; REST API uses "unRealizedProfit" (capital R)
        payload = [
            {
                "s": r.get("symbol"),
                "ps": r.get("positionSide", "BOTH"),
                "pa": r.get("positionAmt"),
                "ep": r.get("entryPrice"),
                "up": r.get("unRealizedProfit") or r.get("unrealizedProfit") or "0",
                "cr": "0",
                "mt": r.get("marginType", "cross").upper(),
            }
            for r in open_rows
        ]
        stream._sync_positions(payload)
    except Exception:
        logger.exception("Failed initial position sync for user=%s", username)


def sync_all_initial_positions() -> None:
    """Pull current open positions from Binance for every configured user and write to DB.
    Called once at backend startup so the DB is populated before any WS events arrive.
    """
    from trade_relay.config import get_api_key, get_api_secret, is_testnet
    for username in db.get_all_active_usernames():
        api_key = get_api_key(username)
        api_secret = get_api_secret(username)
        if not api_key or not api_secret:
            continue
        try:
            client = BinanceClient(api_key=api_key, secret_key=api_secret, testnet=is_testnet(username))
            rows = client.get_position_information()
            open_rows = [r for r in rows if float(r.get("positionAmt", 0) or 0) != 0]
            logger.info("Initial position sync for user=%s: %d open positions", username, len(open_rows))
            # Re-use _sync_positions via a temporary stream object
            stream = UserOrderStatusStream(username, api_key, api_secret, is_testnet(username))
            # Convert REST format → ACCOUNT_UPDATE "P" array format
            payload = [
                {
                    "s": r.get("symbol"),
                    "ps": r.get("positionSide", "BOTH"),
                    "pa": r.get("positionAmt"),
                    "ep": r.get("entryPrice"),
                    "up": r.get("unrealizedProfit"),
                    "cr": "0",
                    "mt": r.get("marginType", "cross").upper(),
                }
                for r in rows
            ]
            stream._sync_positions(payload)
        except Exception:
            logger.exception("Failed initial position sync for user=%s", username)


def stop_all_order_status_streams() -> None:
    with _streams_lock:
        streams = list(_streams.values())
        _streams.clear()
    for stream in streams:
        stream.stop()


def register_user_stream_listener(username: str, listener: Callable[[dict], None]) -> None:
    with _listeners_lock:
        listeners = _listeners.setdefault(username, set())
        listeners.add(listener)


def unregister_user_stream_listener(username: str, listener: Callable[[dict], None]) -> None:
    with _listeners_lock:
        listeners = _listeners.get(username)
        if not listeners:
            return
        listeners.discard(listener)
        if not listeners:
            _listeners.pop(username, None)


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