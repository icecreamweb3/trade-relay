"""完全平仓后异步复算持仓周期的 MFE、MAE 和退出质量。"""
from __future__ import annotations

import logging
import os
import threading

from trade_relay import config as cfg_module
from trade_relay import database as db_module
from trade_relay.exchange.binance_client import BinanceClient
from trade_relay.trading.excursion_metrics import (
    ExcursionCalculationError,
    calculate_excursion_metrics,
    choose_position_cycle,
    fetch_cycle_klines,
    order_time,
)


_log = logging.getLogger(__name__)
SYNC_INTERVAL_SECONDS = max(10.0, float(os.environ.get("EXCURSION_SYNC_INTERVAL_SECONDS", "30")))
INITIAL_DELAY_SECONDS = max(0.0, float(os.environ.get("EXCURSION_SYNC_INITIAL_DELAY_SECONDS", "15")))
MAX_RETRY_ATTEMPTS = max(1, int(os.environ.get("EXCURSION_SYNC_MAX_ATTEMPTS", "8")))
BATCH_SIZE = max(1, int(os.environ.get("EXCURSION_SYNC_BATCH_SIZE", "50")))
RETRY_BACKOFF_SECONDS = (30, 120, 300, 900, 1800)

_stop_event = threading.Event()
_sync_thread: threading.Thread | None = None


def _backoff(attempts: int) -> int:
    return RETRY_BACKOFF_SECONDS[min(max(0, attempts - 1), len(RETRY_BACKOFF_SECONDS) - 1)]


def _target_ids(value) -> list[int]:
    if value is None:
        return []
    result: list[int] = []
    for part in str(value).split(","):
        try:
            result.append(int(part.strip()))
        except (TypeError, ValueError):
            continue
    return result


def _process_candidate(row: dict, clients: dict[str, BinanceClient]) -> None:
    position_id = int(row["id"])
    orders = db_module.get_filled_orders_for_position_excursion(row)
    cycle = choose_position_cycle(
        orders,
        str(row.get("position_side") or ""),
        target_close_order_ids=_target_ids(row.get("target_close_order_ids")),
        closed_at=row.get("updated_at"),
    )
    username = str(row.get("username") or "")
    if username not in clients:
        api_key = cfg_module.get_api_key(username)
        api_secret = cfg_module.get_api_secret(username)
        if not api_key or not api_secret:
            raise ExcursionCalculationError("账户未配置 Binance API 凭证")
        clients[username] = BinanceClient(
            api_key=api_key,
            secret_key=api_secret,
            testnet=cfg_module.is_testnet(username),
        )
    klines = fetch_cycle_klines(
        clients[username],
        str(row["symbol"]),
        order_time(cycle[0]),
        order_time(cycle[-1]),
    )
    if not klines:
        raise ExcursionCalculationError("交易所未返回持仓区间的 1 分钟 K 线")
    metrics = calculate_excursion_metrics(
        cycle,
        klines,
        stored_initial_risk=row.get("initial_risk_usdc"),
        stored_stop_price=row.get("planned_stop_price"),
    )
    db_module.save_position_excursion_metrics(position_id, metrics)
    _log.info(
        "[EXCURSION_SYNC] phase=success position_id=%s symbol=%s mfe=%s mae=%s net_pnl=%s",
        position_id,
        row.get("symbol"),
        metrics["mfe_usdc"],
        metrics["mae_usdc"],
        metrics["net_pnl"],
    )


def _run_once() -> None:
    try:
        candidates = db_module.get_due_position_excursion_candidates(BATCH_SIZE)
    except Exception:
        _log.exception("[EXCURSION_SYNC] phase=query_error")
        return
    clients: dict[str, BinanceClient] = {}
    for row in candidates:
        position_id = int(row["id"])
        attempts = int(row.get("excursion_attempts") or 0) + 1
        try:
            _process_candidate(row, clients)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if attempts >= MAX_RETRY_ATTEMPTS:
                db_module.mark_position_excursion_failed(position_id, error)
                _log.exception(
                    "[EXCURSION_SYNC] phase=failed position_id=%s attempts=%s",
                    position_id,
                    attempts,
                )
            else:
                db_module.schedule_position_excursion_retry(position_id, _backoff(attempts), error)
                _log.warning(
                    "[EXCURSION_SYNC] phase=retry position_id=%s attempts=%s error=%s",
                    position_id,
                    attempts,
                    error,
                )


def _sync_loop() -> None:
    if INITIAL_DELAY_SECONDS > 0 and _stop_event.wait(INITIAL_DELAY_SECONDS):
        return
    _run_once()
    while not _stop_event.wait(SYNC_INTERVAL_SECONDS):
        _run_once()


def start_excursion_sync_worker() -> None:
    global _sync_thread
    if _sync_thread and _sync_thread.is_alive():
        return
    _stop_event.clear()
    _sync_thread = threading.Thread(target=_sync_loop, name="excursion-sync", daemon=True)
    _sync_thread.start()


def stop_excursion_sync_worker() -> None:
    _stop_event.set()
    if _sync_thread:
        _sync_thread.join(timeout=5)
