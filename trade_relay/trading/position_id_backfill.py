"""Strictly reconstruct local position cycles for unlinked imported orders."""
from __future__ import annotations

import math

from trade_relay import database as db
from trade_relay.trading.excursion_metrics import (
    ExcursionCalculationError,
    calculate_cycle_entry_average,
    calculate_excursion_metrics,
    choose_position_cycle,
    order_time,
)
from trade_relay.trading.excursion_retry_worker import (
    _target_ids,
    _validate_cycle_identity,
    _validate_realized_pnl,
)


def _cycle_order_ids(cycle: list[dict], direction: str | None = None) -> list[int]:
    wanted = str(direction or "").upper()
    return [
        int(row["id"])
        for row in cycle
        if row.get("id")
        and (not wanted or str(row.get("trade_direction") or "").upper() == wanted)
    ]


def _validate_history_coverage(row: dict, cycle: list[dict], histories: list[dict]) -> None:
    close_order_ids = _cycle_order_ids(cycle, "CLOSE")
    history_close_ids = [int(item["close_order_id"]) for item in histories if item.get("close_order_id")]
    if not close_order_ids or set(history_close_ids) != set(close_order_ids):
        raise ExcursionCalculationError("完整成交周期的平仓历史不齐全")
    if len(history_close_ids) != len(set(history_close_ids)):
        raise ExcursionCalculationError("同一平仓订单存在重复持仓历史")

    target_ids = set(_target_ids(row.get("target_close_order_ids")))
    if close_order_ids[-1] not in target_ids:
        raise ExcursionCalculationError("当前记录不是完整周期的最后一笔平仓")
    if int(row["id"]) not in {int(item["id"]) for item in histories}:
        raise ExcursionCalculationError("目标持仓历史不属于重建周期")

    expected_realized = math.fsum(float(item.get("realized_pnl") or 0) for item in histories)
    metrics = calculate_excursion_metrics(cycle, [])
    _validate_realized_pnl({"realized_pnl": expected_realized}, metrics)


def backfill_missing_position_ids(
    *,
    user_id: int | None = None,
    limit: int = 5000,
    dry_run: bool = False,
) -> dict:
    """Promote unambiguous legacy snapshots and return a concise repair report."""
    candidates = db.get_unlinked_position_cycle_candidates(limit=limit, user_id=user_id)
    orders_cache: dict[tuple[int, str, str], list[dict]] = {}
    promoted_order_ids: set[int] = set()
    repaired = skipped = failed = 0
    warnings: list[str] = []

    for row in candidates:
        try:
            cache_key = (
                int(row["user_id"]),
                str(row.get("exchange") or "binance"),
                str(row.get("symbol") or ""),
            )
            if cache_key not in orders_cache:
                orders_cache[cache_key] = db.get_filled_orders_for_position_excursion(row)
            cycle = choose_position_cycle(
                orders_cache[cache_key],
                str(row.get("side") or ""),
                target_close_order_ids=_target_ids(row.get("target_close_order_ids")),
                closed_at=row.get("final_close_time") or row.get("updated_at"),
            )
            order_ids = _cycle_order_ids(cycle)
            if promoted_order_ids.intersection(order_ids):
                skipped += 1
                continue
            if any(item.get("position_id") is not None for item in cycle):
                raise ExcursionCalculationError("成交周期已部分关联其他 Position ID")

            _validate_cycle_identity({**row, "id": 0}, cycle)
            entry_avg_price, _ = calculate_cycle_entry_average(cycle)
            close_order_ids = _cycle_order_ids(cycle, "CLOSE")
            histories = db.get_unlinked_position_history_for_close_orders(
                int(row["user_id"]),
                str(row.get("symbol") or ""),
                str(row.get("side") or ""),
                close_order_ids,
            )
            _validate_history_coverage(row, cycle, histories)

            if dry_run:
                repaired += 1
                promoted_order_ids.update(order_ids)
                continue

            position_id = db.promote_unlinked_position_cycle(
                [int(item["id"]) for item in histories],
                order_ids,
                entry_avg_price=entry_avg_price,
                opened_at=order_time(cycle[0]),
            )
            if position_id is None:
                skipped += 1
                continue
            repaired += 1
            promoted_order_ids.update(order_ids)
        except ExcursionCalculationError as exc:
            skipped += 1
            if len(warnings) < 50:
                warnings.append(
                    f"history_id={row.get('id')} {row.get('symbol')}: {exc}"
                )
        except Exception as exc:
            failed += 1
            if len(warnings) < 50:
                warnings.append(
                    f"history_id={row.get('id')} {row.get('symbol')}: {type(exc).__name__}: {exc}"
                )

    return {
        "scanned": len(candidates),
        "repaired": repaired,
        "skipped": skipped,
        "failed": failed,
        "warnings": warnings,
    }
