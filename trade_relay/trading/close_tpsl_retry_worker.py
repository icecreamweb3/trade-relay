from __future__ import annotations

import logging
import os
import threading

from trade_relay import database as db_module
from trade_relay.trading.close_tpsl_sync import derive_position_side_from_close_order, sync_close_tpsl_quantity

_log = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = max(1.0, float(os.environ.get("CLOSE_TPSL_SYNC_INTERVAL_SECONDS", "2")))
RETRY_BACKOFF_SECONDS = (1.0, 2.0, 5.0, 10.0)
MAX_RETRY_ATTEMPTS = max(1, int(os.environ.get("CLOSE_TPSL_SYNC_MAX_ATTEMPTS", "6")))
INITIAL_DELAY_SECONDS = max(0.0, float(os.environ.get("CLOSE_TPSL_SYNC_INITIAL_DELAY_SECONDS", "1")))
BATCH_SIZE = max(1, int(os.environ.get("CLOSE_TPSL_SYNC_BATCH_SIZE", "100")))

_stop_event = threading.Event()
_sync_thread: threading.Thread | None = None


def _backoff_seconds(attempts: int) -> float:
    index = min(max(0, attempts - 1), len(RETRY_BACKOFF_SECONDS) - 1)
    return RETRY_BACKOFF_SECONDS[index]


def _process_candidate(row: dict) -> None:
    order_id = int(row["id"])
    attempts = int(row.get("close_tpsl_sync_attempts") or 0)
    if attempts >= MAX_RETRY_ATTEMPTS:
        _log.warning(
            "[CLOSE_TPSL_SYNC] phase=max_attempts_reached order_id=%s attempts=%s",
            order_id,
            attempts,
        )
        # 清除 next_retry_at，避免该行每轮都被重复捞出、刷日志；保留错误信息供排查
        db_module.update_order_close_tpsl_sync_state(
            order_id,
            next_retry_at=None,
            last_error="max_attempts_reached",
        )
        return

    status = str(row.get("status") or "").upper()
    user_id = int(row.get("user_id") or 0)
    username = str(row.get("username") or "").strip()
    symbol = str(row.get("symbol") or "").strip().upper()
    position_side = derive_position_side_from_close_order(row)
    if not user_id or not username or not symbol or position_side not in {"LONG", "SHORT"}:
        db_module.clear_order_close_tpsl_sync_state(order_id)
        return

    if str(row.get("position_mode") or "").strip().upper() == "SINGLE":
        db_module.clear_order_close_tpsl_sync_state(order_id)
        return

    if status == "PARTIALLY_FILLED":
        db_module.schedule_order_close_tpsl_retry(
            order_id,
            delay_seconds=_backoff_seconds(attempts + 1),
            error_message="close_fill_still_inflight",
        )
        return

    position = db_module.get_position(user_id, symbol, position_side)
    quantity = abs(float((position or {}).get("quantity") or 0.0))
    entry_price = float((position or {}).get("avg_entry_price") or 0.0) or None
    if quantity <= 0:
        db_module.clear_order_close_tpsl_sync_state(order_id)
        return

    if status not in {"FILLED", "CANCELED", "CANCELLED", "EXPIRED", "REJECTED"}:
        db_module.schedule_order_close_tpsl_retry(
            order_id,
            delay_seconds=_backoff_seconds(attempts + 1),
            error_message=f"close_order_not_stable:{status or 'UNKNOWN'}",
        )
        return

    errors = sync_close_tpsl_quantity(
        username=username,
        user_id=user_id,
        symbol=symbol,
        position_side=position_side,
        quantity=quantity,
        entry_price=entry_price,
    )
    if errors:
        db_module.schedule_order_close_tpsl_retry(
            order_id,
            delay_seconds=_backoff_seconds(attempts + 1),
            error_message="; ".join(errors),
        )
        return

    db_module.clear_order_close_tpsl_sync_state(order_id)
    _log.info(
        "[CLOSE_TPSL_SYNC] phase=sync_success order_id=%s username=%s symbol=%s side=%s qty=%s",
        order_id,
        username,
        symbol,
        position_side,
        quantity,
    )


def _run_once() -> None:
    try:
        candidates = db_module.get_due_order_close_tpsl_retry_candidates(limit=BATCH_SIZE)
    except Exception:
        _log.exception("[CLOSE_TPSL_SYNC] phase=query_candidates_error")
        return

    if not candidates:
        return

    _log.info("[CLOSE_TPSL_SYNC] phase=run_once candidates=%s", len(candidates))
    for row in candidates:
        try:
            _process_candidate(row)
        except Exception:
            order_id = int(row.get("id") or 0)
            attempts = int(row.get("close_tpsl_sync_attempts") or 0) + 1
            if order_id:
                try:
                    db_module.schedule_order_close_tpsl_retry(
                        order_id,
                        delay_seconds=_backoff_seconds(attempts),
                        error_message="unexpected_worker_error",
                    )
                except Exception:
                    _log.exception("[CLOSE_TPSL_SYNC] phase=schedule_retry_error order_id=%s", order_id)
            _log.exception("[CLOSE_TPSL_SYNC] phase=process_candidate_error order_id=%s", row.get("id"))


def _sync_loop() -> None:
    _log.info(
        "[CLOSE_TPSL_SYNC] phase=thread_start interval_seconds=%s initial_delay_seconds=%s batch_size=%s",
        SYNC_INTERVAL_SECONDS,
        INITIAL_DELAY_SECONDS,
        BATCH_SIZE,
    )
    if INITIAL_DELAY_SECONDS > 0 and _stop_event.wait(timeout=INITIAL_DELAY_SECONDS):
        _log.info("[CLOSE_TPSL_SYNC] phase=thread_stop")
        return
    _run_once()
    while not _stop_event.wait(timeout=SYNC_INTERVAL_SECONDS):
        _run_once()
    _log.info("[CLOSE_TPSL_SYNC] phase=thread_stop")


def start_close_tpsl_sync_worker() -> None:
    global _sync_thread
    if _sync_thread and _sync_thread.is_alive():
        return
    _stop_event.clear()
    _sync_thread = threading.Thread(target=_sync_loop, name="close-tpsl-sync", daemon=True)
    _sync_thread.start()


def stop_close_tpsl_sync_worker() -> None:
    _stop_event.set()
    if _sync_thread:
        _sync_thread.join(timeout=5)