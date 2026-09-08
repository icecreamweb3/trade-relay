#!/usr/bin/env python3
"""Repair and recalculate historical position MFE/MAE metrics.

By default the task scans every closed, position-linked cycle whose metrics are
missing, failed, or from an older algorithm version.  Dry-run is the default.

Examples:
    .venv/bin/python task/recalculate_historical_excursion_metrics.py
    .venv/bin/python task/recalculate_historical_excursion_metrics.py --user-id 5
    .venv/bin/python task/recalculate_historical_excursion_metrics.py \
        --start-time "2026-09-01 00:00:00" --apply
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trade_relay.env_loader import load_env


load_env(root=ROOT, override=False)

from trade_relay import database as db
from trade_relay.trading.excursion_metrics import (
    ALGORITHM_VERSION,
    calculate_excursion_metrics,
    choose_position_cycle,
)
from trade_relay.trading.excursion_retry_worker import (
    _process_candidate,
    _target_ids,
    _validate_cycle_identity,
    _validate_realized_pnl,
)


class ProgressBar:
    def __init__(self, label: str, total: int) -> None:
        self.label = label
        self.total = max(0, int(total))
        self.started_at = time.monotonic()
        self.is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self.line_open = False

    def update(self, done: int, details: str = "") -> None:
        done = max(0, min(int(done), self.total)) if self.total else int(done)
        fraction = done / self.total if self.total else 1.0
        width = 28
        filled = min(width, int(width * fraction))
        elapsed = int(time.monotonic() - self.started_at)
        line = (
            f"{self.label:<12} [{'#' * filled}{'-' * (width - filled)}]"
            f" {done}/{self.total} {fraction * 100:6.2f}% elapsed={elapsed}s"
        )
        if details:
            line += f" {details}"
        if self.is_tty:
            print(f"\r{line}", end="", flush=True)
            self.line_open = True
        else:
            print(line, flush=True)

    def log(self, message: str) -> None:
        if self.line_open:
            print(flush=True)
            self.line_open = False
        print(message, flush=True)

    def finish(self, details: str = "") -> None:
        self.update(self.total, details)
        if self.line_open:
            print(flush=True)
            self.line_open = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repair duplicate close histories and recalculate historical MFE/MAE. "
            "The default scope is all eligible position-linked records."
        )
    )
    parser.add_argument("--user-id", "--user_id", type=int, help="Only process one users.id.")
    parser.add_argument(
        "--start-time",
        "--start_time",
        help="Only process records closed at or after this time (ISO-8601 or YYYY-MM-DD HH:MM:SS).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", dest="dry_run", action="store_false", help="Write repairs and calculate metrics.")
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", help="Scan and validate only (default).")
    parser.set_defaults(dry_run=True)
    return parser.parse_args()


def _parse_start_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _fetch_candidates(user_id: int | None, start_time: datetime | None) -> list[dict]:
    sql = [
        """SELECT p.*, f.id AS final_id, f.close_time AS final_close_time,
                  f.metric_status, f.metric_version,
                  GROUP_CONCAT(DISTINCT ph.close_order_id ORDER BY ph.id SEPARATOR ',')
                      AS target_close_order_ids
             FROM positions p
             JOIN position_history_final f ON f.position_id = p.id
             JOIN position_history ph ON ph.position_id = p.id
            WHERE UPPER(COALESCE(p.status, 'OPEN')) = 'CLOSE'
              AND ph.close_order_id IS NOT NULL
              AND (COALESCE(f.metric_status, '') <> 'CALCULATED'
                   OR COALESCE(f.metric_version, 0) < %s)"""
    ]
    params: list[Any] = [ALGORITHM_VERSION]
    if user_id is not None:
        sql.append("AND p.user_id = %s")
        params.append(int(user_id))
    if start_time is not None:
        sql.append("AND COALESCE(f.close_time, f.updated_at, f.created_at) >= %s")
        params.append(start_time)
    sql.append(
        "GROUP BY p.id, f.id "
        "ORDER BY COALESCE(f.close_time, f.updated_at, f.created_at), p.id"
    )
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("\n".join(sql), params)
            return cur.fetchall()
    finally:
        conn.close()


def _fetch_history_rows(position_id: int, *, for_update: bool = False) -> list[dict]:
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM position_history
                    WHERE position_id = %s
                    ORDER BY close_order_id, id""" + (" FOR UPDATE" if for_update else ""),
                (int(position_id),),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _row_rank(row: dict) -> tuple[float, str, int]:
    try:
        quantity = abs(float(row.get("quantity") or 0))
    except (TypeError, ValueError):
        quantity = 0.0
    event_at = row.get("updated_at") or row.get("created_at") or ""
    return quantity, str(event_at), int(row.get("id") or 0)


def _classify_history_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep the most complete row for each local close order."""
    groups: dict[int, list[dict]] = {}
    keepers: list[dict] = []
    for row in rows:
        close_order_id = row.get("close_order_id")
        if close_order_id is None:
            keepers.append(row)
            continue
        groups.setdefault(int(close_order_id), []).append(row)
    shadows: list[dict] = []
    for group in groups.values():
        keeper = max(group, key=_row_rank)
        keepers.append(keeper)
        shadows.extend(row for row in group if int(row["id"]) != int(keeper["id"]))
    return keepers, shadows


def _sum_realized(rows: list[dict]) -> float:
    return math.fsum(float(row.get("realized_pnl") or 0) for row in rows)


def _repair_and_requeue(position_id: int) -> tuple[list[int], float]:
    """Deduplicate one cycle and atomically rebuild its PnL/final snapshot."""
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM position_history
                    WHERE position_id = %s
                    ORDER BY close_order_id, id
                    FOR UPDATE""",
                (int(position_id),),
            )
            rows = cur.fetchall()
            keepers, shadows = _classify_history_rows(rows)
            shadow_ids = [int(row["id"]) for row in shadows]
            if shadow_ids:
                placeholders = ", ".join(["%s"] * len(shadow_ids))
                cur.execute(
                    f"DELETE FROM position_history WHERE id IN ({placeholders})",
                    shadow_ids,
                )
            realized_pnl = _sum_realized(keepers)
            cur.execute(
                """UPDATE positions
                      SET realized_pnl = %s,
                          mfe_usdc = NULL, mae_usdc = NULL,
                          mfe_at = NULL, mae_at = NULL,
                          net_pnl = NULL, mfe_r = NULL, mae_r = NULL, net_pnl_r = NULL,
                          profit_capture_rate = NULL, exit_efficiency = NULL,
                          profit_giveback_usdc = NULL, profit_giveback_rate = NULL,
                          excursion_status = 'PENDING', excursion_attempts = 0,
                          excursion_next_retry_at = UTC_TIMESTAMP(3),
                          excursion_last_error = NULL, excursion_source = NULL,
                          excursion_version = NULL, excursion_calculated_at = NULL
                    WHERE id = %s""",
                (realized_pnl, int(position_id)),
            )
            db._upsert_position_history_final_from_position_cursor(cur, int(position_id))
            affected_dates = {
                value.date()
                for row in rows
                if (value := row.get("created_at")) is not None and hasattr(value, "date")
            }
            if rows:
                user_id = int(rows[0].get("user_id") or 0)
                username = str(rows[0].get("username") or "")
                for trade_date in affected_dates:
                    db._refresh_daily_profile_for_user_date(cur, user_id, username, trade_date)
            conn.commit()
            return shadow_ids, realized_pnl
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _validate_local_cycle(row: dict, expected_realized: float) -> None:
    orders = db.get_filled_orders_for_position_excursion(row)
    cycle = choose_position_cycle(
        orders,
        str(row.get("position_side") or ""),
        target_close_order_ids=_target_ids(row.get("target_close_order_ids")),
        closed_at=row.get("final_close_time") or row.get("updated_at"),
    )
    _validate_cycle_identity(row, cycle)
    metrics = calculate_excursion_metrics(
        cycle,
        [],
        stored_initial_risk=row.get("initial_risk_usdc"),
        stored_stop_price=row.get("planned_stop_price"),
    )
    checked_row = {**row, "realized_pnl": expected_realized}
    _validate_realized_pnl(checked_row, metrics)


def recalculate_missing_metrics(
    user_id: int | None = None,
    start_time: datetime | None = None,
    *,
    dry_run: bool = False,
    progress: ProgressBar | None = None,
    _candidates: list[dict] | None = None,
) -> dict[str, int]:
    """Recalculate eligible position metrics and return a UI/API-friendly summary."""
    rows = _candidates if _candidates is not None else _fetch_candidates(user_id, start_time)
    clients: dict = {}
    calculated = queued = failed = duplicate_rows = 0
    if progress:
        progress.update(0, "calculated=0 queued=0 failed=0 duplicates=0")

    for index, original_row in enumerate(rows, start=1):
        position_id = int(original_row["id"])
        history_rows = _fetch_history_rows(position_id)
        keepers, shadows = _classify_history_rows(history_rows)
        expected_realized = _sum_realized(keepers)
        duplicate_rows += len(shadows)
        shadow_ids = ",".join(str(row["id"]) for row in shadows) or "-"

        if dry_run:
            try:
                _validate_local_cycle(original_row, expected_realized)
                if progress:
                    progress.log(
                        f"READY position_id={position_id} user_id={original_row.get('user_id')}"
                        f" symbol={original_row.get('symbol')} duplicate_history_ids={shadow_ids}"
                        f" corrected_realized_pnl={expected_realized:.10f}"
                    )
                queued += 1
            except Exception as exc:
                failed += 1
                if progress:
                    progress.log(
                        f"UNRESOLVED position_id={position_id} user_id={original_row.get('user_id')}"
                        f" symbol={original_row.get('symbol')} duplicate_history_ids={shadow_ids} error={exc}"
                    )
        else:
            try:
                deleted_ids, corrected_realized = _repair_and_requeue(position_id)
                row = {**original_row, "realized_pnl": corrected_realized, "excursion_attempts": 0}
                _process_candidate(row, clients)
                calculated += 1
                if progress:
                    progress.log(
                        f"CALCULATED position_id={position_id} user_id={row.get('user_id')}"
                        f" symbol={row.get('symbol')} deleted_history_ids="
                        f"{','.join(str(value) for value in deleted_ids) or '-'}"
                    )
            except Exception as exc:
                failed += 1
                try:
                    db.schedule_position_excursion_retry(position_id, 0, f"{type(exc).__name__}: {exc}")
                    queued += 1
                except Exception as queue_exc:
                    if progress:
                        progress.log(f"QUEUE_FAIL position_id={position_id} error={queue_exc}")
                if progress:
                    progress.log(
                        f"FAILED position_id={position_id} user_id={original_row.get('user_id')}"
                        f" symbol={original_row.get('symbol')} error={exc}"
                    )
        if progress:
            progress.update(
                index,
                f"calculated={calculated} queued={queued} failed={failed} duplicates={duplicate_rows}",
            )

    if progress:
        progress.finish(
            f"calculated={calculated} queued={queued} failed={failed} duplicates={duplicate_rows}"
        )
    return {
        "scanned": len(rows),
        "calculated": calculated,
        "queued": queued,
        "failed": failed,
        "duplicate_history_rows": duplicate_rows,
    }


def main() -> int:
    args = parse_args()
    if args.user_id is not None and args.user_id <= 0:
        print("ERROR --user-id must be greater than zero", file=sys.stderr)
        return 2
    try:
        start_time = _parse_start_time(args.start_time)
    except ValueError:
        print("ERROR --start-time must be ISO-8601 or YYYY-MM-DD HH:MM:SS", file=sys.stderr)
        return 2

    mode = "dry-run" if args.dry_run else "apply"
    print(
        f"LOAD         querying historical excursion candidates mode={mode}"
        f" user_id={args.user_id if args.user_id is not None else 'ALL'}"
        f" start_time={start_time if start_time is not None else 'ALL'}",
        flush=True,
    )
    rows = _fetch_candidates(args.user_id, start_time)
    print(f"LOAD         complete total={len(rows)}", flush=True)
    # Keep the CLI progress bar while sharing the actual operation with the API.
    progress = ProgressBar("RECALCULATE", len(rows))
    result = recalculate_missing_metrics(
        args.user_id,
        start_time,
        dry_run=args.dry_run,
        progress=progress,
        _candidates=rows,
    )
    print(
        f"DONE mode={mode} scanned={result['scanned']} calculated={result['calculated']}"
        f" ready_or_queued={result['queued']} failed={result['failed']}"
        f" duplicate_history_rows={result['duplicate_history_rows']}",
        flush=True,
    )
    return 1 if not args.dry_run and result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
