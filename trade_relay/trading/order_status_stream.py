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
from trade_relay.trading.close_trade_sync import sync_close_order_trade_details, sync_filled_order_trade_details
from trade_relay.trading.tpsl_service import place_tp_sl_orders

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
        self.poll_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.running = False
        self.proxy_url = None
        if self.client.proxy_config:
            self.proxy_url = self.client.proxy_config.get("https") or self.client.proxy_config.get("http")
        # Reconnect state (mirrors orders_monitor.py pattern)
        self.reconnecting = False
        self.reconnect_count = 0
        self.max_reconnect_attempts = 10
        self.reconnect_interval = 5       # seconds to wait between reconnect attempts
        self.connection_timeout = 5 * 60  # seconds without any WS message → trigger reconnect
        self.last_message_time: Optional[float] = None
        self.health_check_thread: Optional[threading.Thread] = None
        # Track exchange_order_ids for which position_history has already been created,
        # to prevent duplicate records when both WS and REST poll detect the same fill.
        self._handled_close_fills: set[str] = set()
        self._handled_close_fills_lock = threading.Lock()
        # Cache the last known (position_id, entry_price) per (symbol, position_side).
        # Needed so that the second of two sequential partial-close fills can still read
        # entry_price even after _sync_position_from_rest has zeroed/deleted the DB row.
        self._entry_price_cache: dict[tuple[str, str], tuple[Optional[int], float]] = {}
        self._entry_price_cache_lock = threading.Lock()
        self._handled_open_tpsl: set[str] = set()
        self._handled_open_tpsl_lock = threading.Lock()

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
        self.reconnecting = False
        self.reconnect_count = 0
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
        self.poll_thread = threading.Thread(
            target=self._poll_open_orders_loop,
            daemon=True,
            name=f"order-status-poll-{self.username}",
        )
        self.poll_thread.start()
        self.health_check_thread = threading.Thread(
            target=self._health_check_loop,
            daemon=True,
            name=f"order-status-health-{self.username}",
        )
        self.health_check_thread.start()
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
        status = str(order.get("status") or "").upper()

        # When the order is fully filled, handle position_history and position sync —
        # same as the poll path, because the WebSocket ORDER_TRADE_UPDATE may arrive
        # before this sync runs (race) or may never arrive (reconnect gap).
        if status == "FILLED":
            executed_qty = float(order.get("executedQty") or 0)
            avg_price_raw = order.get("avgPrice")
            avg_price = float(avg_price_raw) if avg_price_raw not in (None, "", "0", "0.00000000") else None

            if executed_qty > 0 and avg_price:
                db_order = db.get_order_by_exchange_id(self.username, exchange_order_id)
                if db_order and str(db_order.get("trade_direction") or "").upper() == "CLOSE":
                    # Guard against duplicates when WS path already handled this fill.
                    already_handled = False
                    with self._handled_close_fills_lock:
                        if exchange_order_id in self._handled_close_fills:
                            logger.debug(
                                "position_history already created for order=%s (REST path), skipping",
                                exchange_order_id,
                            )
                            already_handled = True
                        else:
                            self._handled_close_fills.add(exchange_order_id)
                    if not already_handled:
                        # Derive position_side from positionSide field (REST format) or order side
                        position_side = str(order.get("positionSide") or "BOTH").upper()
                        if position_side not in ("LONG", "SHORT"):
                            order_side = str(order.get("side") or "").upper()
                            position_side = "SHORT" if order_side == "BUY" else "LONG"
                        self._create_position_history_from_poll(
                            symbol=symbol,
                            position_side=position_side,
                            fill_qty=executed_qty,
                            fill_price=avg_price,
                        )
            # Always sync position from REST for filled orders (clears closed positions)
            threading.Thread(
                target=self._sync_position_from_rest,
                args=(symbol,),
                daemon=True,
            ).start()

        # Notify frontend so Open Orders / Positions tabs refresh immediately.
        force = status in ("FILLED", "CANCELED", "CANCELLED", "EXPIRED", "REJECTED")
        self._notify_listeners({
            "type": "order_update",
            "event": "SYNC",
            "symbol": symbol,
            "status": status,
        }, force=force)

    def _run_ws_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                # ── Reconnect branch: get a fresh listenKey before reconnecting ──
                if self.reconnecting:
                    if self.reconnect_count >= self.max_reconnect_attempts:
                        logger.error(
                            "Order status stream user=%s: reached max reconnect attempts (%d), giving up",
                            self.username, self.max_reconnect_attempts,
                        )
                        self.running = False
                        break

                    self.reconnect_count += 1
                    logger.info(
                        "Order status stream user=%s: reconnect attempt %d/%d …",
                        self.username, self.reconnect_count, self.max_reconnect_attempts,
                    )
                    if self.stop_event.wait(self.reconnect_interval):
                        break

                    # Close stale listenKey and get a new one
                    old_key = self.listen_key
                    try:
                        new_key = self.client.start_user_data_stream()
                    except Exception:
                        logger.exception("Order status stream user=%s: failed to refresh listenKey", self.username)
                        continue
                    if not new_key:
                        logger.warning("Order status stream user=%s: got empty listenKey on reconnect", self.username)
                        continue
                    if old_key and old_key != new_key:
                        try:
                            self.client.close_user_data_stream(old_key)
                        except Exception:
                            pass
                    self.listen_key = new_key
                    self.reconnecting = False
                    logger.info("Order status stream user=%s: new listenKey obtained, reconnecting …", self.username)

                # ── Normal / post-reconnect: open WebSocket ──
                self.ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open,
                )
                run_kwargs: dict = {
                    "sslopt": {"cert_reqs": ssl.CERT_NONE},
                    "ping_interval": 20,
                    "ping_timeout": 10,
                }
                parsed_proxy = _parse_proxy_url(self.proxy_url)
                if parsed_proxy:
                    run_kwargs.update(parsed_proxy)
                self.ws.run_forever(**run_kwargs)

                # run_forever() returned — connection was closed
                if not self.stop_event.is_set():
                    logger.warning(
                        "Order status websocket closed unexpectedly for user=%s, will reconnect …",
                        self.username,
                    )
                    self.reconnecting = True

            except Exception:
                logger.exception("Order status websocket loop failed for user=%s", self.username)
                if not self.stop_event.is_set():
                    self.reconnecting = True
                    time.sleep(1)

    def _reconnect(self) -> None:
        """Signal the WS loop to reconnect with a fresh listenKey.
        Safe to call from any thread (health-check or keepalive).
        """
        logger.info("Order status stream user=%s: _reconnect() called", self.username)
        self.reconnecting = True
        # Reset the health timestamp so we don't re-trigger immediately during reconnect
        self.last_message_time = time.time()
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass

    def _health_check_loop(self) -> None:
        """Every 60 s, verify that the WS has received at least one message in the
        last ``connection_timeout`` seconds.  If not, trigger a reconnect.
        Mirrors the health_check_loop in orders_monitor.py.
        """
        check_interval = 60
        while not self.stop_event.wait(check_interval):
            if not self.running:
                break
            t = self.last_message_time
            if t is None:
                continue
            elapsed = time.time() - t
            if elapsed > self.connection_timeout:
                logger.warning(
                    "Order status stream user=%s: no WS message for %.0f s (timeout %d s) — reconnecting",
                    self.username, elapsed, self.connection_timeout,
                )
                self._reconnect()
            else:
                logger.debug(
                    "Order status stream user=%s: health OK, last message %.0f s ago",
                    self.username, elapsed,
                )

    def _keepalive_loop(self) -> None:
        while not self.stop_event.wait(30 * 60):
            if not self.running or not self.listen_key:
                continue
            try:
                if not self.client.keepalive_user_data_stream(self.listen_key):
                    logger.warning("listenKey keepalive failed for user=%s", self.username)
            except Exception:
                logger.exception("listenKey keepalive exception for user=%s", self.username)

    _POLL_INTERVAL = 15  # seconds between REST polls for open order status

    def _poll_open_orders_loop(self) -> None:
        """Periodically query Binance REST API for every DB-active order and sync status.
        This is a safety net for missed WebSocket ORDER_TRADE_UPDATE events.
        """
        # Stagger initial poll so it doesn't collide with startup sync
        self.stop_event.wait(self._POLL_INTERVAL)
        while not self.stop_event.is_set():
            try:
                self._poll_open_orders_once()
            except Exception:
                logger.exception("Order status poll error for user=%s", self.username)
            self.stop_event.wait(self._POLL_INTERVAL)

    def _poll_open_orders_once(self) -> None:
        active_rows = db.get_active_orders_for_user(self.username)
        if not active_rows:
            return
        notify_needed = False
        # Collect (db_row, new_status, executed_qty, avg_price) for fills detected this round
        fills: list[tuple[dict, str, float, float | None]] = []
        for row in active_rows:
            exchange_order_id = self._resolve_monitored_exchange_order_id(row) or ""
            symbol = str(row.get("symbol") or "")
            db_status = str(row.get("status") or "")
            if not exchange_order_id or not symbol:
                continue
            try:
                result = self.client.get_order_status(symbol, exchange_order_id)
                if result is None:
                    continue
                new_status = str(result.get("status") or "").upper()
                if not new_status or new_status == db_status:
                    continue
                executed_qty = float(result.get("executedQty") or 0)
                avg_price_raw = result.get("avgPrice")
                avg_price = float(avg_price_raw) if avg_price_raw not in (None, "", "0", "0.00000000") else None
                logger.info(
                    "Poll order sync: exchange_order_id=%s user=%s symbol=%s %s → %s executedQty=%s avgPrice=%s",
                    exchange_order_id, self.username, symbol, db_status, new_status, executed_qty, avg_price,
                )
                db.update_order_status_by_exchange_id(
                    username=self.username,
                    exchange_order_id=exchange_order_id,
                    status=new_status,
                    filled_qty=executed_qty if executed_qty else None,
                    avg_price=avg_price,
                )
                notify_needed = True
                if new_status in ("FILLED", "PARTIALLY_FILLED") and executed_qty > 0:
                    fills.append(({**row, "exchange_order_id": exchange_order_id}, new_status, executed_qty, avg_price))
            except Exception:
                logger.exception("Poll: error querying exchange_order_id=%s user=%s", exchange_order_id, self.username)

        # Handle fills: create position_history for CLOSE orders, then sync positions from Binance
        symbols_to_sync: set[str] = set()
        for db_row, new_status, executed_qty, avg_price in fills:
            symbol = str(db_row.get("symbol") or "")
            trade_direction = str(db_row.get("trade_direction") or "").upper()
            order_side = str(db_row.get("side") or "").upper()
            # Derive position_side:
            # OPEN+BUY→LONG, OPEN+SELL→SHORT, CLOSE+SELL→LONG, CLOSE+BUY→SHORT
            if trade_direction == "CLOSE":
                position_side = "LONG" if order_side == "SELL" else "SHORT"
            else:
                position_side = "LONG" if order_side == "BUY" else "SHORT"

            if trade_direction == "CLOSE" and avg_price and avg_price > 0:
                self._create_position_history_from_poll(
                    symbol=symbol,
                    position_side=position_side,
                    fill_qty=executed_qty,
                    fill_price=avg_price,
                )
                sync_filled_order_trade_details(username=self.username, client=self.client, order_row=db_row)
            elif trade_direction == "OPEN" and new_status == "FILLED":
                sync_filled_order_trade_details(username=self.username, client=self.client, order_row=db_row)
                self._place_open_fill_tpsl(db_row, executed_qty, avg_price)

            symbols_to_sync.add(symbol)

        for symbol in symbols_to_sync:
            self._sync_position_from_rest(symbol)

        if notify_needed:
            self._notify_listeners({"type": "order_update", "event": "POLL"}, force=True)

    def _resolve_monitored_exchange_order_id(self, row: dict) -> str | None:
        exchange_order_id = str(row.get("exchange_order_id") or "").strip()
        if exchange_order_id:
            return exchange_order_id

        if str(row.get("order_category") or "").upper() != "CONDITIONAL":
            return None

        algo_id = str(row.get("algo_id") or "").strip()
        algo_client_id = str(row.get("algo_client_id") or row.get("client_order_id") or "").strip()
        if not algo_id and not algo_client_id:
            return None

        try:
            algo_detail = self.client.get_algo_order(
                algo_id=int(algo_id) if algo_id else None,
                client_algo_id=None if algo_id else algo_client_id,
            )
        except Exception:
            logger.exception(
                "Failed to resolve actual order id for conditional order user=%s algo_id=%s algo_client_id=%s",
                self.username,
                algo_id,
                algo_client_id,
            )
            return None

        if not isinstance(algo_detail, dict):
            return None

        backfill_fields: dict[str, str] = {}
        actual_order_id = str(algo_detail.get("actualOrderId") or algo_detail.get("orderId") or "").strip()
        algo_id_from_detail = str(algo_detail.get("algoId") or "").strip()
        client_algo_id = str(algo_detail.get("clientAlgoId") or "").strip()

        if algo_id_from_detail and not algo_id:
            backfill_fields["algo_id"] = algo_id_from_detail
        if client_algo_id and not algo_client_id:
            backfill_fields["algo_client_id"] = client_algo_id
        if actual_order_id:
            backfill_fields["exchange_order_id"] = actual_order_id

        if backfill_fields and row.get("id"):
            db.update_order_metadata(int(row["id"]), **backfill_fields)

        if actual_order_id:
            logger.info(
                "Resolved actual order id for conditional order user=%s db_order_id=%s algo_id=%s actual_order_id=%s",
                self.username,
                row.get("id"),
                algo_id or algo_id_from_detail,
                actual_order_id,
            )
            return actual_order_id

        return None

    def _sync_position_from_rest(self, symbol: str) -> None:
        """Fetch live position data from Binance REST for a given symbol and upsert into DB."""
        try:
            rows = self.client.get_position_information(symbol=symbol)
            payload = [
                {
                    "s":  r.get("symbol"),
                    "ps": r.get("positionSide", "BOTH"),
                    "pa": r.get("positionAmt"),
                    "ep": r.get("entryPrice"),
                    "up": r.get("unRealizedProfit") or r.get("unrealizedProfit") or "0",
                    "cr": str(r.get("realizedPnl", 0)),
                    "mt": r.get("marginType", "cross").upper(),
                    "l": r.get("leverage"),
                }
                for r in rows
            ]
            # Always sync (even empty payload handles zero-amount entries from Binance)
            self._sync_positions(payload)
            logger.info(
                "Synced %d position row(s) from REST for user=%s symbol=%s",
                len(payload), self.username, symbol,
            )
            # If Binance reports no open positions for this symbol, purge any stale DB rows.
            # This handles the case where Binance omits zero-amount entries entirely.
            has_open = any(float(r.get("positionAmt", 0) or 0) != 0 for r in rows)
            if not has_open:
                user = db.get_user_by_username(self.username)
                if user:
                    user_id = int(user["id"])
                    for side in ("LONG", "SHORT", "BOTH"):
                        db.delete_position(user_id, symbol, side)
                    logger.info(
                        "Cleared stale DB positions for user=%s symbol=%s (no open positions on Binance)",
                        self.username, symbol,
                    )
            # Notify frontend that positions have been updated.  This covers the edge case
            # where ACCOUNT_UPDATE was delayed or missed (e.g. WS reconnect gap).
            self._notify_listeners({
                "type": "account_update",
                "event": "REST_SYNC",
                "symbol": symbol,
            }, force=True)
        except Exception:
            logger.exception("Failed REST position sync for user=%s symbol=%s", self.username, symbol)

    def _create_position_history_from_poll(
        self,
        symbol: str,
        position_side: str,
        fill_qty: float,
        fill_price: float,
    ) -> None:
        """Create a position_history record for a CLOSE fill detected via REST poll."""
        try:
            user = db.get_user_by_username(self.username)
            if not user:
                return
            user_id = int(user["id"])
            cache_key = (symbol, position_side)
            # Read entry_price and id from current DB position BEFORE REST sync overwrites it
            position = db.get_position(user_id, symbol, position_side)
            position_id: Optional[int] = int(position["id"]) if position and position.get("id") else None
            entry_price = float((position or {}).get("avg_entry_price") or 0)
            # Fallback 1: fetch entry price from Binance REST if DB has none
            if not entry_price:
                try:
                    binance_positions = self.client.get_position_information(symbol)
                    for bp in (binance_positions or []):
                        ps = str(bp.get("positionSide") or "").upper()
                        amt = float(bp.get("positionAmt") or 0)
                        if ps == position_side and abs(amt) > 0:
                            entry_price = float(bp.get("entryPrice") or 0)
                            break
                except Exception:
                    pass
            # Fallback 2: use cached value from a previous fill on the same position
            # (handles the case where the DB row is already gone by the time a second
            #  partial-close fill arrives, e.g. two sequential 0.001 BTC closes)
            if entry_price:
                with self._entry_price_cache_lock:
                    # Prefer the cached position_id when position is already gone from DB
                    if position_id is None:
                        cached_pid, _ = self._entry_price_cache.get(cache_key, (None, 0.0))
                        position_id = cached_pid
                    self._entry_price_cache[cache_key] = (position_id, entry_price)
            else:
                with self._entry_price_cache_lock:
                    cached_pid, cached_ep = self._entry_price_cache.get(cache_key, (None, 0.0))
                if cached_ep:
                    entry_price = cached_ep
                    if position_id is None:
                        position_id = cached_pid
                    logger.debug(
                        "entry_price fallback from cache: user=%s symbol=%s side=%s entry=%.4f",
                        self.username, symbol, position_side, entry_price,
                    )
            if position_side == "LONG":
                realized_pnl = (fill_price - entry_price) * fill_qty
            else:
                realized_pnl = (entry_price - fill_price) * fill_qty
            history_id = db.add_position_history(
                user_id=user_id,
                username=self.username,
                symbol=symbol,
                side=position_side,
                entry_price=entry_price,
                close_price=fill_price,
                quantity=fill_qty,
                realized_pnl=realized_pnl,
                commission=0.0,  # commission not available from poll; WS path has it
                commission_asset=None,
                position_id=position_id,
            )
            logger.info(
                "position_history (poll) created: id=%s position_id=%s user=%s symbol=%s side=%s qty=%s "
                "entry=%.4f close=%.4f rpnl=%.4f",
                history_id, position_id, self.username, symbol, position_side,
                fill_qty, entry_price, fill_price, realized_pnl,
            )
        except Exception:
            logger.exception(
                "Failed to create position_history (poll) for user=%s symbol=%s",
                self.username, symbol,
            )

    def _on_message(self, _ws, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.debug("Invalid order status payload for user=%s: %s", self.username, message[:200])
            return
        event_type = str(data.get("e") or "")
        # Track liveness for health-check (only real business events, not keep-alive frames)
        if event_type:
            self.last_message_time = time.time()
        if event_type == "ORDER_TRADE_UPDATE":
            order = data.get("o") or {}
            self._persist_status(order)
            self._handle_open_fill_tpsl(order)
            # Generate position_history entry when a CLOSE order fills
            self._handle_close_fill(order)
            order_status = str(order.get("X") or order.get("status") or "").upper()
            force_notify = order_status in ("FILLED", "CANCELED", "CANCELLED", "EXPIRED", "REJECTED")
            self._notify_listeners({
                "type": "order_update",
                "event": event_type,
                "symbol": order.get("s") or order.get("symbol"),
            }, force=force_notify)
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
            }, force=True)  # Position data just written to DB; never suppress this notification

    def _handle_close_fill(self, order: dict) -> None:
        """When a CLOSE-direction order fills (fully or partially), write a position_history
        record for the filled portion.  Called once per ORDER_TRADE_UPDATE event."""
        # Only act on actual trade executions
        execution_type = str(order.get("x") or "").upper()
        if execution_type != "TRADE":
            return
        order_status = str(order.get("X") or "").upper()
        if order_status not in ("FILLED", "PARTIALLY_FILLED"):
            return

        exchange_order_id = str(order.get("i") or "")
        if not exchange_order_id:
            return

        # For a FULLY FILLED order, check dedup set to avoid double-creating position_history
        # if both WS and the REST sync_order_status detect the fill.
        is_full_fill = order_status == "FILLED"
        if is_full_fill:
            with self._handled_close_fills_lock:
                if exchange_order_id in self._handled_close_fills:
                    logger.debug(
                        "position_history already created for order=%s (WS path), skipping",
                        exchange_order_id,
                    )
                    return
                self._handled_close_fills.add(exchange_order_id)

        # Look up the DB order to check trade_direction
        db_order = db.get_order_by_exchange_id(self.username, exchange_order_id)
        if not db_order:
            return
        trade_direction = str(db_order.get("trade_direction") or "").upper()
        if trade_direction != "CLOSE":
            return

        # Fields from the WS event for THIS fill (not cumulative):
        # l = last filled qty, L = last fill price, n = commission for this fill
        symbol = str(order.get("s") or "")
        position_side = str(order.get("ps") or "BOTH").upper()
        last_fill_qty = _safe_float(order.get("l") or order.get("z") or 0)
        last_fill_price = _safe_float(order.get("L") or order.get("ap") or 0)
        commission = abs(_safe_float(order.get("n") or 0))

        if last_fill_qty is None or last_fill_qty <= 0 or last_fill_price is None or last_fill_price <= 0:
            return

        user = db.get_user_by_username(self.username)
        if not user:
            return
        user_id = int(user["id"])

        cache_key = (symbol, position_side)
        # Get the current position to read avg_entry_price and id
        position = db.get_position(user_id, symbol, position_side)
        position_id: Optional[int] = int(position["id"]) if position and position.get("id") else None
        entry_price = _safe_float((position or {}).get("avg_entry_price") or 0) or 0.0
        # Fallback 1: when DB has no position yet (e.g. first run after restart), read from Binance
        if not entry_price:
            try:
                binance_positions = self.client.get_position_information(symbol)
                for bp in (binance_positions or []):
                    ps = str(bp.get("positionSide") or "").upper()
                    amt = float(bp.get("positionAmt") or 0)
                    if ps == position_side and abs(amt) > 0:
                        entry_price = float(bp.get("entryPrice") or 0)
                        break
            except Exception:
                pass
        # Fallback 2: use cached value from a previous fill on the same position
        if entry_price:
            with self._entry_price_cache_lock:
                if position_id is None:
                    cached_pid, _ = self._entry_price_cache.get(cache_key, (None, 0.0))
                    position_id = cached_pid
                self._entry_price_cache[cache_key] = (position_id, entry_price)
        else:
            with self._entry_price_cache_lock:
                cached_pid, cached_ep = self._entry_price_cache.get(cache_key, (None, 0.0))
            if cached_ep:
                entry_price = cached_ep
                if position_id is None:
                    position_id = cached_pid
                logger.debug(
                    "entry_price fallback from cache (WS): user=%s symbol=%s side=%s entry=%.4f",
                    self.username, symbol, position_side, entry_price,
                )

        # Realized PnL for this fill
        if position_side == "LONG":
            realized_pnl = (last_fill_price - entry_price) * last_fill_qty
        else:
            realized_pnl = (entry_price - last_fill_price) * last_fill_qty

        try:
            history_id = db.add_position_history(
                user_id=user_id,
                username=self.username,
                symbol=symbol,
                side=position_side,
                entry_price=entry_price,
                close_price=last_fill_price,
                quantity=last_fill_qty,
                realized_pnl=realized_pnl,
                commission=commission,
                commission_asset=str(commission_asset) if commission_asset else None,
                position_id=position_id,
            )
            logger.info(
                "position_history created: id=%s position_id=%s user=%s symbol=%s side=%s qty=%s "
                "entry=%.4f close=%.4f rpnl=%.4f commission=%.6f",
                history_id, position_id, self.username, symbol, position_side,
                last_fill_qty, entry_price, last_fill_price, realized_pnl, commission,
            )
            sync_filled_order_trade_details(username=self.username, client=self.client, order_row=db_order)
        except Exception:
            logger.exception(
                "Failed to create position_history for user=%s exchange_order_id=%s",
                self.username, exchange_order_id,
            )

        # For fully-filled closes, trigger a REST position sync as a fallback in case
        # the ACCOUNT_UPDATE WebSocket event is delayed or missed.
        if order_status == "FILLED":
            threading.Thread(
                target=self._sync_position_from_rest,
                args=(symbol,),
                daemon=True,
            ).start()

    def _handle_open_fill_tpsl(self, order: dict) -> None:
        execution_type = str(order.get("x") or "").upper()
        if execution_type != "TRADE":
            return
        order_status = str(order.get("X") or "").upper()
        if order_status != "FILLED":
            return

        exchange_order_id = str(order.get("i") or "")
        if not exchange_order_id:
            return

        db_order = db.get_order_by_exchange_id(self.username, exchange_order_id)
        if not db_order:
            return

        sync_filled_order_trade_details(username=self.username, client=self.client, order_row=db_order)

        executed_qty = _safe_float(order.get("z") or order.get("executedQty") or db_order.get("filled_qty") or 0)
        avg_price = _safe_float(order.get("ap") or order.get("avgPrice") or db_order.get("avg_price") or 0)
        self._place_open_fill_tpsl(db_order, executed_qty, avg_price)

    def _place_open_fill_tpsl(self, db_order: dict, executed_qty: Optional[float], avg_price: Optional[float]) -> None:
        exchange_order_id = str(db_order.get("exchange_order_id") or "")
        if not exchange_order_id:
            return
        with self._handled_open_tpsl_lock:
            if exchange_order_id in self._handled_open_tpsl:
                return

        trade_direction = str(db_order.get("trade_direction") or "").upper()
        if trade_direction != "OPEN":
            return

        tp_price = _safe_float(db_order.get("tp_price") or 0)
        sl_price = _safe_float(db_order.get("sl_price") or 0)
        if not tp_price and not sl_price:
            return

        user_id_raw = db_order.get("user_id")
        if user_id_raw is None:
            user = db.get_user_by_username(self.username)
            if not user:
                return
            user_id = int(user["id"])
        else:
            user_id = int(user_id_raw)

        symbol = str(db_order.get("symbol") or "")
        order_side = str(db_order.get("side") or "").upper()
        position_side = "LONG" if order_side == "BUY" else "SHORT"
        quantity = executed_qty if executed_qty and executed_qty > 0 else _safe_float(db_order.get("filled_qty") or db_order.get("quantity") or 0)
        entry_price = avg_price if avg_price and avg_price > 0 else _safe_float(db_order.get("avg_price") or db_order.get("price") or 0)
        if not quantity or quantity <= 0:
            return

        position = db.get_position(user_id, symbol, position_side)
        position_id = int(position["id"]) if position and position.get("id") else None

        errors = place_tp_sl_orders(
            username=self.username,
            user_id=user_id,
            symbol=symbol,
            position_side=position_side,
            quantity=quantity,
            entry_price=entry_price,
            tp_price=tp_price,
            sl_price=sl_price,
            position_id=position_id,
        )
        if errors:
            logger.warning(
                "Open fill TP/SL placement failed: user=%s order=%s symbol=%s errors=%s",
                self.username,
                exchange_order_id,
                symbol,
                "; ".join(errors),
            )
            return

        with self._handled_open_tpsl_lock:
            self._handled_open_tpsl.add(exchange_order_id)
        logger.info(
            "Open fill TP/SL placed: user=%s order=%s symbol=%s tp=%s sl=%s qty=%s position_side=%s",
            self.username,
            exchange_order_id,
            symbol,
            tp_price,
            sl_price,
            quantity,
            position_side,
        )

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
            # Prefer leverage from the Binance payload ("l" key from REST, or "leverage"); fall back to DB value
            payload_leverage = _safe_float(position.get("l") or position.get("leverage"))
            if payload_leverage and payload_leverage > 0:
                leverage = int(payload_leverage)
            else:
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
            # Update entry_price cache so close-fill handlers can find it even after
            # the position row is deleted (e.g. when second of two partial closes arrives).
            if entry_price:
                # Fetch the row to get its DB id (upsert may have created it above)
                upserted = db.get_position(user_id, symbol, normalized_side)
                pid = int(upserted["id"]) if upserted and upserted.get("id") else None
                with self._entry_price_cache_lock:
                    self._entry_price_cache[(symbol, normalized_side)] = (pid, entry_price)

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

    def _on_open(self, _ws) -> None:
        logger.info(
            "Order status websocket connected for user=%s (reconnect_count was %d)",
            self.username, self.reconnect_count,
        )
        self.reconnect_count = 0
        self.last_message_time = time.time()

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

    def _notify_listeners(self, event: dict, force: bool = False) -> None:
        now = time.time()
        with _listeners_lock:
            last = _notify_last.get(self.username, 0.0)
            if not force and now - last < _notify_cooldown:
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


def notify_user_stream_event(username: str, event: dict, force: bool = False) -> None:
    with _streams_lock:
        stream = _streams.get(username)
    if stream:
        stream._notify_listeners(event, force=force)


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


def sync_active_orders_on_startup() -> None:
    """At startup, query Binance for the current status of every active (NEW/PARTIALLY_FILLED)
    order in the DB and update it.  Results are written to the log."""
    active_rows = db.get_active_orders_for_sync()
    if not active_rows:
        logger.info("Startup order sync: no active orders found in DB")
        return

    logger.info("Startup order sync: checking %d active order(s)", len(active_rows))

    for row in active_rows:
        username = str(row.get("username") or "")
        exchange_order_id = str(row.get("exchange_order_id") or "")
        symbol = str(row.get("symbol") or "")
        db_status = str(row.get("status") or "")

        if not exchange_order_id or not symbol or not username:
            logger.warning(
                "Startup order sync: skipping order id=%s (missing exchange_order_id/symbol/username)",
                row.get("id"),
            )
            continue

        api_key = cfg.get_api_key(username)
        api_secret = cfg.get_api_secret(username)
        if not api_key or not api_secret:
            logger.warning(
                "Startup order sync: skipping order exchange_id=%s user=%s (no API credentials)",
                exchange_order_id, username,
            )
            continue

        try:
            client = BinanceClient(api_key=api_key, secret_key=api_secret, testnet=cfg.is_testnet(username))
            result = client.get_order_status(symbol, exchange_order_id)
            if result is None:
                logger.warning(
                    "Startup order sync: exchange_order_id=%s user=%s symbol=%s — Binance returned no result (order may be expired or unknown)",
                    exchange_order_id, username, symbol,
                )
                continue

            new_status = str(result.get("status") or "").upper()
            executed_qty = float(result.get("executedQty") or 0)
            avg_price_raw = result.get("avgPrice")
            avg_price = float(avg_price_raw) if avg_price_raw not in (None, "", "0", "0.00000000") else None

            logger.info(
                "Startup order sync: exchange_order_id=%s user=%s symbol=%s db_status=%s binance_status=%s "
                "executedQty=%s avgPrice=%s",
                exchange_order_id, username, symbol, db_status, new_status, executed_qty, avg_price,
            )

            if new_status and new_status != db_status:
                updated = db.update_order_status_by_exchange_id(
                    username=username,
                    exchange_order_id=exchange_order_id,
                    status=new_status,
                    filled_qty=executed_qty if executed_qty else None,
                    avg_price=avg_price,
                )
                if updated:
                    logger.info(
                        "Startup order sync: updated exchange_order_id=%s %s → %s",
                        exchange_order_id, db_status, new_status,
                    )
            else:
                logger.info(
                    "Startup order sync: exchange_order_id=%s status unchanged (%s)",
                    exchange_order_id, db_status,
                )

        except Exception:
            logger.exception(
                "Startup order sync: error querying exchange_order_id=%s user=%s",
                exchange_order_id, username,
            )


def sync_initial_positions_for_user(username: str, api_key: str, api_secret: str, testnet: bool) -> None:
    """Pull current positions from Binance for a single user and write to DB.
    Called when the user's WebSocket connection is established (they are authenticated).
    """
    try:
        client = BinanceClient(api_key=api_key, secret_key=api_secret, testnet=testnet)
        rows = client.get_position_information()
        open_rows = [r for r in rows if float(r.get("positionAmt", 0) or 0) != 0]
        logger.info("Initial position sync for user=%s: %d open positions (total %d from Binance)", username, len(open_rows), len(rows))

        stream = UserOrderStatusStream(username, api_key, api_secret, testnet)

        # Pass ALL rows (including zero-amount) so _sync_positions can delete closed positions.
        # REST API uses "unRealizedProfit" (capital R)
        payload = [
            {
                "s": r.get("symbol"),
                "ps": r.get("positionSide", "BOTH"),
                "pa": r.get("positionAmt"),
                "ep": r.get("entryPrice"),
                "up": r.get("unRealizedProfit") or r.get("unrealizedProfit") or "0",
                "cr": "0",
                "mt": r.get("marginType", "cross").upper(),
                "l": r.get("leverage"),
            }
            for r in rows
        ]
        stream._sync_positions(payload)

        # Binance v3/positionRisk only returns non-zero positions, so DB rows not present
        # in the Binance response are stale (position already closed). Delete them.
        user_obj = db.get_user_by_username(username)
        if user_obj:
            user_id = int(user_obj["id"])
            # Build set of (symbol, normalised_side) that Binance says are open
            binance_open: set[tuple[str, str]] = set()
            for r in open_rows:
                sym = str(r.get("symbol") or "")
                side = str(r.get("positionSide") or "BOTH").upper()
                if sym:
                    binance_open.add((sym, side))

            db_positions = db.get_positions(user_id=user_id)
            for pos in db_positions:
                sym = str(pos.get("symbol") or "")
                side = str(pos.get("position_side") or "BOTH").upper()
                if (sym, side) not in binance_open:
                    logger.info(
                        "Initial position sync: deleting stale DB position user=%s symbol=%s side=%s (not in Binance)",
                        username, sym, side,
                    )
                    db.delete_position(user_id, sym, side)

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
                    "l": r.get("leverage"),
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