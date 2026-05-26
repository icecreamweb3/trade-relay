from __future__ import annotations

import logging
import os
import threading

from trade_relay import config as cfg_module
from trade_relay import database as db_module
from trade_relay.exchange.binance_client import BinanceClient
from trade_relay.trading.close_trade_sync import sync_filled_order_trade_details

_log = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = max(10.0, float(os.environ.get("TRADE_DETAILS_SYNC_INTERVAL_SECONDS", "30")))
RETRY_BACKOFF_SECONDS = (30.0, 120.0, 300.0, 900.0)
MAX_RETRY_ATTEMPTS = max(1, int(os.environ.get("TRADE_DETAILS_SYNC_MAX_ATTEMPTS", "8")))
INITIAL_DELAY_SECONDS = max(0.0, float(os.environ.get("TRADE_DETAILS_SYNC_INITIAL_DELAY_SECONDS", "10")))
BATCH_SIZE = max(1, int(os.environ.get("TRADE_DETAILS_SYNC_BATCH_SIZE", "100")))

_stop_event = threading.Event()
_sync_thread: threading.Thread | None = None
_QTY_SYNC_TOLERANCE = 1e-9


def _build_client(username: str) -> BinanceClient | None:
    api_key = cfg_module.get_api_key(username)
    api_secret = cfg_module.get_api_secret(username)
    if not api_key or not api_secret:
        return None
    return BinanceClient(
        api_key=api_key,
        secret_key=api_secret,
        testnet=cfg_module.is_testnet(username),
    )


def _backoff_seconds(attempts: int) -> float:
    index = min(max(0, attempts - 1), len(RETRY_BACKOFF_SECONDS) - 1)
    return RETRY_BACKOFF_SECONDS[index]


def _order_needs_trade_details_sync(order_row: dict) -> bool:
    trade_direction = str(order_row.get("trade_direction") or "").upper()
    quantity = abs(float(order_row.get("quantity") or 0.0))
    filled_qty = abs(float(order_row.get("filled_qty") or 0.0))
    quantity_incomplete = abs(quantity - filled_qty) > _QTY_SYNC_TOLERANCE

    # Some Binance CLOSE fills can settle with a final filled_qty that differs slightly
    # from the original requested quantity. Once a CLOSE order has realized PnL and
    # commission metadata, retrying trade-details sync will only churn updated_at.
    close_trade_details_complete = (
        trade_direction == "CLOSE"
        and order_row.get("realized_pnl") is not None
        and order_row.get("commission") is not None
        and str(order_row.get("commission_asset") or "").strip()
    )
    if close_trade_details_complete:
        quantity_incomplete = False

    return (
        order_row.get("commission") is None
        or not str(order_row.get("commission_asset") or "").strip()
        or quantity_incomplete
        or (trade_direction == "CLOSE" and order_row.get("realized_pnl") is None)
    )


def _process_candidate(row: dict, client_cache: dict[str, BinanceClient | None]) -> None:
    order_id = int(row["id"])
    username = str(row.get("username") or "").strip()
    if not username:
        return

    attempts = int(row.get("trade_details_sync_attempts") or 0)
    if attempts >= MAX_RETRY_ATTEMPTS:
        _log.warning(
            "[TRADE_DETAILS_SYNC] phase=max_attempts_reached order_id=%s username=%s attempts=%s",
            order_id,
            username,
            attempts,
        )
        return

    if username not in client_cache:
        client_cache[username] = _build_client(username)

    client = client_cache[username]
    if client is None:
        db_module.schedule_order_trade_details_retry(
            order_id,
            delay_seconds=_backoff_seconds(attempts + 1),
            error_message="missing_api_credentials",
        )
        _log.warning(
            "[TRADE_DETAILS_SYNC] phase=missing_credentials order_id=%s username=%s",
            order_id,
            username,
        )
        return

    before = db_module.get_order_by_id(order_id) or row
    sync_filled_order_trade_details(username=username, client=client, order_row=before)
    after = db_module.get_order_by_id(order_id) or before

    if _order_needs_trade_details_sync(after):
        after_attempts = int(after.get("trade_details_sync_attempts") or attempts)
        after_next_retry_at = after.get("trade_details_sync_next_retry_at")
        if after_attempts > attempts and after_next_retry_at is not None:
            _log.info(
                "[TRADE_DETAILS_SYNC] phase=retry_already_scheduled order_id=%s username=%s attempts=%s",
                order_id,
                username,
                after_attempts,
            )
            return

        next_attempts = after_attempts + 1
        db_module.schedule_order_trade_details_retry(
            order_id,
            delay_seconds=_backoff_seconds(next_attempts),
            error_message="trade_fills_not_ready",
        )
        _log.info(
            "[TRADE_DETAILS_SYNC] phase=retry_scheduled order_id=%s username=%s attempts=%s",
            order_id,
            username,
            next_attempts,
        )
        return

    db_module.clear_order_trade_details_sync_state(order_id)
    _log.info(
        "[TRADE_DETAILS_SYNC] phase=sync_success order_id=%s username=%s",
        order_id,
        username,
    )


def _run_once() -> None:
    try:
        candidates = db_module.get_due_order_trade_details_retry_candidates(limit=BATCH_SIZE)
    except Exception:
        _log.exception("[TRADE_DETAILS_SYNC] phase=query_candidates_error")
        return

    if not candidates:
        return

    _log.info("[TRADE_DETAILS_SYNC] phase=run_once candidates=%s", len(candidates))
    client_cache: dict[str, BinanceClient | None] = {}
    for row in candidates:
        try:
            _process_candidate(row, client_cache)
        except Exception:
            order_id = row.get("id")
            attempts = int(row.get("trade_details_sync_attempts") or 0) + 1
            try:
                db_module.schedule_order_trade_details_retry(
                    int(order_id),
                    delay_seconds=_backoff_seconds(attempts),
                    error_message="unexpected_worker_error",
                )
            except Exception:
                _log.exception("[TRADE_DETAILS_SYNC] phase=schedule_retry_error order_id=%s", order_id)
            _log.exception("[TRADE_DETAILS_SYNC] phase=process_candidate_error order_id=%s", order_id)


def _sync_loop() -> None:
    _log.info(
        "[TRADE_DETAILS_SYNC] phase=thread_start interval_seconds=%s initial_delay_seconds=%s batch_size=%s",
        SYNC_INTERVAL_SECONDS,
        INITIAL_DELAY_SECONDS,
        BATCH_SIZE,
    )
    if INITIAL_DELAY_SECONDS > 0 and _stop_event.wait(timeout=INITIAL_DELAY_SECONDS):
        _log.info("[TRADE_DETAILS_SYNC] phase=thread_stop")
        return
    _run_once()
    while not _stop_event.wait(timeout=SYNC_INTERVAL_SECONDS):
        _run_once()
    _log.info("[TRADE_DETAILS_SYNC] phase=thread_stop")


def start_trade_details_sync_worker() -> None:
    global _sync_thread
    if _sync_thread and _sync_thread.is_alive():
        return
    _stop_event.clear()
    _sync_thread = threading.Thread(target=_sync_loop, name="trade-details-sync", daemon=True)
    _sync_thread.start()


def stop_trade_details_sync_worker() -> None:
    _stop_event.set()
    if _sync_thread:
        _sync_thread.join(timeout=5)