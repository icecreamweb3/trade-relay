"""完全平仓后异步复算持仓周期的 MFE、MAE 和退出质量。"""
from __future__ import annotations

import logging
import math
import os
import threading

from trade_relay import config as cfg_module
from trade_relay import database as db_module
from trade_relay.exchange.binance_client import BinanceClient
from trade_relay.trading.excursion_metrics import (
    ALGORITHM_VERSION,
    ExcursionCalculationError,
    calculate_cycle_entry_average,
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
ORDER_LINK_BACKFILL_SIZE = max(1, int(os.environ.get("ORDER_LINK_BACKFILL_SIZE", "5000")))
ORDER_LINK_MAX_CYCLE_ORDERS = max(1, int(os.environ.get("ORDER_LINK_MAX_CYCLE_ORDERS", "100")))
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
    # Order IDs can be repaired without waiting for Binance kline history. First
    # validate the reconstructed fills against position_history, then persist the
    # exact cycle so position_history_final immediately receives both ID groups.
    preflight_metrics = calculate_excursion_metrics(
        cycle,
        [],
        stored_initial_risk=row.get("initial_risk_usdc"),
        stored_stop_price=row.get("planned_stop_price"),
    )
    _validate_realized_pnl(row, preflight_metrics)
    db_module.replace_filled_orders_for_position(
        position_id,
        [int(order["id"]) for order in cycle if order.get("id")],
    )
    db_module.upsert_position_history_final(position_id)

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
    _validate_realized_pnl(row, metrics)
    db_module.save_position_excursion_metrics(position_id, metrics)
    _log.info(
        "[EXCURSION_SYNC] phase=success position_id=%s symbol=%s mfe=%s mae=%s net_pnl=%s",
        position_id,
        row.get("symbol"),
        metrics["mfe_usdc"],
        metrics["mae_usdc"],
        metrics["net_pnl"],
    )


def _validate_realized_pnl(row: dict, metrics: dict) -> None:
    """Reject an incomplete or cross-position reconstructed order cycle."""
    if row.get("realized_pnl") is not None:
        expected_realized = float(row["realized_pnl"])
        calculated_realized = float(metrics["realized_pnl"])
        tolerance = max(1e-6, abs(expected_realized) * 1e-6)
        if not math.isclose(calculated_realized, expected_realized, abs_tol=tolerance):
            raise ExcursionCalculationError(
                "成交周期已实现盈亏与持仓记录不一致: "
                f"cycle={calculated_realized:.10f}, position={expected_realized:.10f}"
            )


def _validate_cycle_identity(row: dict, cycle: list[dict]) -> None:
    """Validate a target-anchored cycle without trusting legacy PnL aggregates."""
    if len(cycle) > ORDER_LINK_MAX_CYCLE_ORDERS:
        raise ExcursionCalculationError(
            f"成交周期订单数异常: {len(cycle)}>{ORDER_LINK_MAX_CYCLE_ORDERS}"
        )
    position_id = int(row.get("id") or 0)
    foreign_position_ids = sorted({
        int(order["position_id"])
        for order in cycle
        if order.get("position_id") not in (None, "")
        and int(order["position_id"]) != position_id
    })
    if foreign_position_ids:
        preview = ",".join(str(value) for value in foreign_position_ids[:10])
        if len(foreign_position_ids) > 10:
            preview += ",..."
        raise ExcursionCalculationError(f"成交周期跨越其他持仓: {preview}")

    weighted_entry, total_open_qty = calculate_cycle_entry_average(cycle)
    if total_open_qty <= 0:
        raise ExcursionCalculationError("完整成交周期缺少开仓成交")
    expected_entry_raw = row.get("avg_entry_price")
    if expected_entry_raw is not None and float(expected_entry_raw) > 0:
        expected_entry = float(expected_entry_raw)
        tolerance = max(0.01, abs(expected_entry) * 1e-6)
        if not math.isclose(weighted_entry, expected_entry, abs_tol=tolerance):
            raise ExcursionCalculationError(
                "成交周期开仓均价与持仓记录不一致: "
                f"cycle={weighted_entry:.10f}, position={expected_entry:.10f}"
            )


def _repair_missing_order_links(
    limit: int = ORDER_LINK_BACKFILL_SIZE,
    dry_run: bool = False,
) -> tuple[int, int]:
    """One-time local-only repair for historical OPEN/CLOSE order associations."""
    rows = db_module.get_missing_position_order_link_candidates(limit)
    orders_cache: dict[tuple[int, str, str], list[dict]] = {}
    repaired = 0
    failed = 0
    for row in rows:
        try:
            target_ids = _target_ids(row.get("target_close_order_ids"))
            if not target_ids:
                raise ExcursionCalculationError("持仓历史缺少目标平仓订单ID")
            cache_key = (
                int(row["user_id"]),
                str(row.get("exchange") or "binance"),
                str(row["symbol"]),
            )
            if cache_key not in orders_cache:
                orders_cache[cache_key] = db_module.get_filled_orders_for_position_excursion(row)
            cycle = choose_position_cycle(
                orders_cache[cache_key],
                str(row.get("position_side") or ""),
                target_close_order_ids=target_ids,
                closed_at=row.get("updated_at"),
            )
            _validate_cycle_identity(row, cycle)
            position_id = int(row["id"])
            if not dry_run:
                db_module.replace_filled_orders_for_position(
                    position_id,
                    [int(order["id"]) for order in cycle if order.get("id")],
                )
                db_module.upsert_position_history_final(position_id)
            repaired += 1
        except Exception as exc:
            failed += 1
            _log.warning(
                "[ORDER_LINK_BACKFILL] position_id=%s symbol=%s error=%s",
                row.get("id"),
                row.get("symbol"),
                exc,
            )
    legacy_rows = db_module.get_missing_legacy_order_link_candidates(limit)
    for row in legacy_rows:
        try:
            target_ids = _target_ids(row.get("target_close_order_ids"))
            cache_key = (
                int(row["user_id"]),
                str(row.get("exchange") or "binance"),
                str(row["symbol"]),
            )
            if cache_key not in orders_cache:
                orders_cache[cache_key] = db_module.get_filled_orders_for_position_excursion(row)
            cycle = choose_position_cycle(
                orders_cache[cache_key],
                str(row.get("position_side") or ""),
                target_close_order_ids=target_ids,
                closed_at=row.get("updated_at") or row.get("created_at"),
            )
            open_ids = [
                value for order in cycle
                if str(order.get("trade_direction") or "").upper() == "OPEN"
                if (value := _display_order_id(order))
            ]
            close_ids = [
                value for order in cycle
                if str(order.get("trade_direction") or "").upper() == "CLOSE"
                if (value := _display_order_id(order))
            ]
            if not open_ids:
                raise ExcursionCalculationError("完整成交周期缺少开仓订单ID")
            if not dry_run:
                db_module.update_legacy_final_order_ids(int(row["id"]), open_ids, close_ids)
            repaired += 1
        except Exception as exc:
            failed += 1
            _log.warning(
                "[ORDER_LINK_BACKFILL] legacy_history_id=%s symbol=%s error=%s",
                row.get("id"),
                row.get("symbol"),
                exc,
            )
    _log.info(
        "[ORDER_LINK_BACKFILL] phase=complete candidates=%s repaired=%s failed=%s",
        len(rows) + len(legacy_rows),
        repaired,
        failed,
    )
    return repaired, failed


def _display_order_id(order: dict) -> str:
    conditional = str(order.get("order_category") or "").upper() == "CONDITIONAL"
    values = (
        (order.get("algo_id"), order.get("exchange_order_id"))
        if conditional else
        (order.get("exchange_order_id"), order.get("algo_id"))
    )
    return next((str(value).strip() for value in values if str(value or "").strip()), "")


def _run_once() -> None:
    try:
        candidates = db_module.get_due_position_excursion_candidates(
            BATCH_SIZE,
            current_version=ALGORITHM_VERSION,
        )
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
    try:
        _repair_missing_order_links()
    except Exception:
        _log.exception("[ORDER_LINK_BACKFILL] phase=query_error")
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
