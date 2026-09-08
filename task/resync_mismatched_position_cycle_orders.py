#!/usr/bin/env python3
"""Resync orders for closed position cycles whose OPEN average is inconsistent.

Detection is local and read-only.  Apply mode refreshes every OPEN/CLOSE order in
the selected cycle from Binance, refreshes its fills, and rebuilds the durable
order-to-position/final-history associations.

Examples:
    python task/resync_mismatched_position_cycle_orders.py
    python task/resync_mismatched_position_cycle_orders.py --apply --username alice
    python task/resync_mismatched_position_cycle_orders.py --apply --position-id 123
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trade_relay.env_loader import load_env


load_env(root=ROOT, override=False)

from trade_relay import config as cfg
from trade_relay import database as db
from trade_relay.exchange.binance_client import BinanceClient
from trade_relay.trading.close_trade_sync import sync_filled_order_trade_details
from trade_relay.trading.excursion_metrics import (
    ExcursionCalculationError,
    calculate_cycle_entry_average as weighted_open_average,
    choose_position_cycle,
)


DEFAULT_ABSOLUTE_TOLERANCE = 0.01
DEFAULT_RELATIVE_TOLERANCE = 1e-6
DEFAULT_MAX_CYCLE_ORDERS = 100


class ProgressBar:
    """Small dependency-free progress display that also works with redirected logs."""

    def __init__(self, label: str, total: int) -> None:
        self.label = label
        self.total = max(0, int(total))
        self.started_at = time.monotonic()
        self.last_printed_at = 0.0
        self.is_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
        self.line_open = False

    def update(self, done: int, *, details: str = "", force: bool = False) -> None:
        now = time.monotonic()
        if not force and not self.is_tty and now - self.last_printed_at < 5.0:
            return
        done = max(0, min(int(done), self.total)) if self.total else max(0, int(done))
        fraction = (done / self.total) if self.total else 1.0
        width = 28
        filled = min(width, int(fraction * width))
        bar = "#" * filled + "-" * (width - filled)
        elapsed = max(0.0, now - self.started_at)
        if done > 0 and self.total > done:
            eta = elapsed * (self.total - done) / done
            eta_text = f" ETA={_format_duration(eta)}"
        else:
            eta_text = ""
        line = (
            f"{self.label:<12} [{bar}] {done}/{self.total}"
            f" {fraction * 100:6.2f}% elapsed={_format_duration(elapsed)}{eta_text}"
        )
        if details:
            line += f" {details}"
        if self.is_tty:
            print(f"\r{line}", end="", flush=True)
            self.line_open = True
        else:
            print(line, flush=True)
        self.last_printed_at = now

    def log(self, message: str) -> None:
        if self.line_open:
            print(flush=True)
            self.line_open = False
        print(message, flush=True)

    def finish(self, done: int, *, details: str = "") -> None:
        self.update(done, details=details, force=True)
        if self.line_open:
            print(flush=True)
            self.line_open = False


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Resync Binance orders and fills for closed position cycles whose "
            "weighted OPEN average differs from positions.avg_entry_price."
        )
    )
    parser.add_argument("--username", help="Only scan one username.")
    parser.add_argument("--position-id", type=int, help="Only scan one positions.id.")
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum number of closed positions to scan (default: 5000).",
    )
    parser.add_argument(
        "--absolute-tolerance",
        type=float,
        default=DEFAULT_ABSOLUTE_TOLERANCE,
        help="Minimum allowed price difference (default: 0.01).",
    )
    parser.add_argument(
        "--relative-tolerance",
        type=float,
        default=DEFAULT_RELATIVE_TOLERANCE,
        help="Allowed difference relative to the position average (default: 1e-6).",
    )
    parser.add_argument(
        "--max-cycle-orders",
        type=int,
        default=DEFAULT_MAX_CYCLE_ORDERS,
        help=(
            "Reject a reconstructed cycle containing more than this many orders "
            "instead of risking a cross-cycle resync (default: 100)."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="Call Binance and write refreshed orders, fills, and position associations.",
    )
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Only detect and print mismatches (the default).",
    )
    parser.set_defaults(dry_run=True)
    return parser.parse_args()


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _utc_from_ms(value: Any) -> datetime | None:
    try:
        return datetime.utcfromtimestamp(int(value) / 1000.0)
    except (TypeError, ValueError, OSError):
        return None


def _target_close_order_ids(row: dict) -> list[int]:
    raw = str(row.get("target_close_order_ids") or "")
    result: list[int] = []
    for value in raw.split(","):
        try:
            result.append(int(value.strip()))
        except (TypeError, ValueError):
            continue
    return result


def _foreign_position_ids(position_id: int, cycle: list[dict]) -> list[int]:
    result: set[int] = set()
    for order in cycle:
        raw = order.get("position_id")
        if raw in (None, ""):
            continue
        try:
            linked_position_id = int(raw)
        except (TypeError, ValueError):
            continue
        if linked_position_id != position_id:
            result.add(linked_position_id)
    return sorted(result)


def _summarize_order_ids(cycle: list[dict], visible: int = 12) -> str:
    order_ids = [str(order.get("id")) for order in cycle if order.get("id")]
    if len(order_ids) <= visible:
        return ",".join(order_ids)
    head_size = max(1, visible // 2)
    tail_size = max(1, visible - head_size)
    return f"{','.join(order_ids[:head_size])},...,{','.join(order_ids[-tail_size:])}"


def _fetch_closed_positions(*, username: str | None, position_id: int | None, limit: int) -> list[dict]:
    sql = [
        """
        SELECT p.*,
               f.entry_avg_price AS final_entry_avg_price,
               GROUP_CONCAT(DISTINCT ph.close_order_id ORDER BY ph.id SEPARATOR ',')
                   AS target_close_order_ids
          FROM positions p
          JOIN position_history_final f ON f.position_id = p.id
          JOIN position_history ph ON ph.position_id = p.id
         WHERE UPPER(COALESCE(p.status, 'OPEN')) = 'CLOSE'
           AND ph.close_order_id IS NOT NULL
        """
    ]
    params: list[Any] = []
    if username:
        sql.append("AND p.username = %s")
        params.append(username.strip())
    if position_id is not None:
        sql.append("AND p.id = %s")
        params.append(int(position_id))
    sql.append("GROUP BY p.id, f.id ORDER BY p.updated_at DESC, p.id DESC LIMIT %s")
    params.append(int(limit))

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("\n".join(sql), params)
            return cur.fetchall()
    finally:
        conn.close()


def find_mismatched_cycles(
    rows: list[dict],
    *,
    absolute_tolerance: float,
    relative_tolerance: float,
    max_cycle_orders: int = DEFAULT_MAX_CYCLE_ORDERS,
    progress: Callable[[int, int, int], None] | None = None,
) -> tuple[list[dict], list[tuple[dict, str]], list[dict]]:
    mismatches: list[dict] = []
    skipped: list[tuple[dict, str]] = []
    stale_final_rows: list[dict] = []
    orders_cache: dict[tuple[int, str, str], list[dict]] = {}
    for index, row in enumerate(rows, start=1):
        expected = _safe_float(row.get("avg_entry_price"))
        targets = _target_close_order_ids(row)
        if expected <= 0 or not targets:
            skipped.append((row, "missing_position_average_or_close_anchor"))
            if progress:
                progress(index, len(mismatches), len(skipped))
            continue
        try:
            cache_key = (
                int(row["user_id"]),
                str(row.get("exchange") or "binance"),
                str(row["symbol"]),
            )
            if cache_key not in orders_cache:
                orders_cache[cache_key] = db.get_filled_orders_for_position_excursion(row)
            orders = orders_cache[cache_key]
            cycle = choose_position_cycle(
                orders,
                str(row.get("position_side") or ""),
                target_close_order_ids=targets,
                closed_at=row.get("closed_at") or row.get("updated_at"),
            )
        except (ExcursionCalculationError, TypeError, ValueError) as exc:
            skipped.append((row, str(exc)))
            if progress:
                progress(index, len(mismatches), len(skipped))
            continue
        if len(cycle) > max_cycle_orders:
            skipped.append(
                (row, f"cycle_order_count_exceeds_limit:{len(cycle)}>{max_cycle_orders}")
            )
            if progress:
                progress(index, len(mismatches), len(skipped))
            continue
        foreign_position_ids = _foreign_position_ids(int(row["id"]), cycle)
        if foreign_position_ids:
            preview = ",".join(str(value) for value in foreign_position_ids[:10])
            if len(foreign_position_ids) > 10:
                preview += ",..."
            skipped.append((row, f"cycle_crosses_other_positions:{preview}"))
            if progress:
                progress(index, len(mismatches), len(skipped))
            continue
        actual, open_qty = weighted_open_average(cycle)
        if actual <= 0 or open_qty <= 0:
            skipped.append((row, "cycle_has_no_valid_open_fill"))
            if progress:
                progress(index, len(mismatches), len(skipped))
            continue
        tolerance = max(absolute_tolerance, abs(expected) * relative_tolerance)
        difference = abs(actual - expected)
        if difference > tolerance:
            mismatches.append(
                {
                    "position": row,
                    "cycle": cycle,
                    "expected": expected,
                    "actual": actual,
                    "difference": difference,
                    "tolerance": tolerance,
                    "target_close_order_ids": targets,
                }
            )
        else:
            final_average = _safe_float(row.get("final_entry_avg_price"))
            if final_average <= 0 or abs(final_average - expected) > tolerance:
                stale_final_rows.append(
                    {
                        "position": row,
                        "expected": expected,
                        "current": final_average,
                    }
                )
        if progress:
            progress(index, len(mismatches), len(skipped))
    return mismatches, skipped, stale_final_rows


def _build_client(username: str) -> BinanceClient | None:
    api_key = cfg.get_api_key(username)
    api_secret = cfg.get_api_secret(username)
    if not api_key or not api_secret:
        return None
    return BinanceClient(
        api_key=api_key,
        secret_key=api_secret,
        testnet=cfg.is_testnet(username),
    )


def _resync_order(*, username: str, client: BinanceClient, order: dict) -> tuple[bool, str]:
    local_id = int(order.get("id") or 0)
    exchange_order_id = str(order.get("exchange_order_id") or "").strip()
    symbol = str(order.get("symbol") or "").upper().strip()
    if local_id <= 0 or not exchange_order_id or not symbol:
        return False, "missing_local_id_exchange_order_id_or_symbol"

    before = db.get_order_by_id(local_id) or dict(order)
    status_error: Exception | None = None
    try:
        remote = client.get_order_status(symbol, exchange_order_id) or {}
        status = str(remote.get("status") or before.get("status") or "FILLED").upper()
        executed_qty = _safe_float(remote.get("executedQty"))
        avg_price = _safe_float(remote.get("avgPrice"))
        event_time = remote.get("updateTime") or remote.get("time")
        db.update_order_status(
            local_id,
            status,
            filled_qty=executed_qty if executed_qty > 0 else None,
            avg_price=avg_price if avg_price > 0 else None,
            filled_at=_utc_from_ms(event_time) if executed_qty > 0 else None,
        )
    except Exception as exc:  # Fills may still be available when order lookup fails.
        status_error = exc

    refreshed = db.get_order_by_id(local_id) or before
    try:
        sync_filled_order_trade_details(username=username, client=client, order_row=refreshed)
    except Exception as exc:
        return False, f"trade_fill_sync_failed:{exc}"

    after = db.get_order_by_id(local_id) or refreshed
    watched = ("status", "filled_qty", "avg_price", "filled_at", "commission", "commission_asset", "realized_pnl")
    changed = any(before.get(key) != after.get(key) for key in watched)
    if status_error is not None:
        return changed, f"order_status_failed_but_fills_synced:{status_error}"
    return changed, "updated" if changed else "no_change"


def _rebuild_position_cycle(item: dict) -> tuple[bool, float, str]:
    position = item["position"]
    position_id = int(position["id"])
    orders = db.get_filled_orders_for_position_excursion(position)
    try:
        cycle = choose_position_cycle(
            orders,
            str(position.get("position_side") or ""),
            target_close_order_ids=item["target_close_order_ids"],
            closed_at=position.get("closed_at") or position.get("updated_at"),
        )
    except (ExcursionCalculationError, TypeError, ValueError) as exc:
        return False, 0.0, str(exc)

    cycle_ids = [int(order["id"]) for order in cycle if order.get("id")]
    if not cycle_ids:
        return False, 0.0, "rebuilt_cycle_has_no_orders"
    db.replace_filled_orders_for_position(position_id, cycle_ids)
    db.upsert_position_history_final(position_id)
    for close_order_id in item["target_close_order_ids"]:
        db.schedule_position_excursion_for_close_order(close_order_id)

    actual, _ = weighted_open_average(cycle)
    resolved = abs(actual - item["expected"]) <= item["tolerance"]
    return resolved, actual, "resolved" if resolved else "still_mismatched"


def main() -> int:
    args = parse_args()
    if (
        args.limit <= 0
        or args.max_cycle_orders <= 0
        or (args.position_id is not None and args.position_id <= 0)
    ):
        print("ERROR limits and --position-id must be greater than zero", file=sys.stderr)
        return 2
    if args.absolute_tolerance < 0 or args.relative_tolerance < 0:
        print("ERROR tolerances cannot be negative", file=sys.stderr)
        return 2

    print(
        "LOAD         querying closed position cycles..."
        f" mode={'dry-run' if args.dry_run else 'apply'} limit={args.limit}",
        flush=True,
    )
    rows = _fetch_closed_positions(
        username=args.username,
        position_id=args.position_id,
        limit=args.limit,
    )
    print(f"LOAD         complete total={len(rows)}; starting cycle validation", flush=True)
    scan_progress = ProgressBar("SCAN", len(rows))
    scan_progress.update(0, details="mismatched=0 unresolvable=0", force=True)
    mismatches, skipped_cycles, stale_final_rows = find_mismatched_cycles(
        rows,
        absolute_tolerance=args.absolute_tolerance,
        relative_tolerance=args.relative_tolerance,
        max_cycle_orders=args.max_cycle_orders,
        progress=lambda done, mismatch_count, skipped_count: scan_progress.update(
            done,
            details=f"mismatched={mismatch_count} unresolvable={skipped_count}",
        ),
    )
    scan_progress.finish(
        len(rows),
        details=f"mismatched={len(mismatches)} unresolvable={len(skipped_cycles)}",
    )
    for item in mismatches:
        position = item["position"]
        print(
            "MISMATCH"
            f" position_id={position.get('id')}"
            f" username={position.get('username')}"
            f" symbol={position.get('symbol')}"
            f" side={position.get('position_side')}"
            f" position_avg={item['expected']:.12g}"
            f" cycle_entry_avg={item['actual']:.12g}"
            f" difference={item['difference']:.12g}"
            f" order_count={len(item['cycle'])}"
            f" local_order_ids={_summarize_order_ids(item['cycle'])}",
            flush=True,
        )
    for row, reason in skipped_cycles:
        if reason.startswith(("cycle_order_count_exceeds_limit", "cycle_crosses_other_positions")):
            print(
                "UNSAFE"
                f" position_id={row.get('id')} username={row.get('username')}"
                f" symbol={row.get('symbol')} side={row.get('position_side')} reason={reason}",
                flush=True,
            )
    for item in stale_final_rows:
        position = item["position"]
        print(
            "STALE_FINAL"
            f" position_id={position.get('id')} username={position.get('username')}"
            f" symbol={position.get('symbol')} side={position.get('position_side')}"
            f" current_final_avg={item['current']:.12g}"
            f" corrected_final_avg={item['expected']:.12g}",
            flush=True,
        )

    if args.dry_run:
        print(
            "DONE mode=dry-run"
            f" scanned={len(rows)} mismatched={len(mismatches)}"
            f" unresolvable={len(skipped_cycles)} stale_final={len(stale_final_rows)}"
            f" planned_orders={sum(len(x['cycle']) for x in mismatches)}",
            flush=True,
        )
        return 0

    clients: dict[str, BinanceClient | None] = {}
    orders_processed = orders_updated = orders_skipped = orders_failed = 0
    resolved = still_mismatched = cycle_failed = 0
    total_orders = sum(len(item["cycle"]) for item in mismatches)
    total_sync_steps = total_orders + len(mismatches)
    sync_done = 0
    sync_progress = ProgressBar("RESYNC", total_sync_steps)
    sync_progress.update(
        0,
        details=f"positions=0/{len(mismatches)} orders=0/{total_orders} resolved=0",
        force=True,
    )
    for position_index, item in enumerate(mismatches, start=1):
        position = item["position"]
        username = str(position.get("username") or "").strip()
        if username not in clients:
            clients[username] = _build_client(username)
        client = clients[username]
        if client is None:
            cycle_failed += 1
            orders_skipped += len(item["cycle"])
            orders_processed += len(item["cycle"])
            sync_done += len(item["cycle"]) + 1
            sync_progress.log(f"SKIP position_id={position.get('id')} reason=no_api_credentials")
            sync_progress.update(
                sync_done,
                details=(
                    f"positions={position_index}/{len(mismatches)}"
                    f" orders={orders_processed}/{total_orders} resolved={resolved} failed={cycle_failed}"
                ),
                force=True,
            )
            continue

        for order in item["cycle"]:
            orders_processed += 1
            if not str(order.get("exchange_order_id") or "").strip():
                orders_skipped += 1
                sync_progress.log(f"SKIP order_id={order.get('id')} reason=missing_exchange_order_id")
                sync_done += 1
                sync_progress.update(
                    sync_done,
                    details=(
                        f"positions={position_index - 1}/{len(mismatches)}"
                        f" orders={orders_processed}/{total_orders} resolved={resolved} failed={orders_failed}"
                    ),
                    force=True,
                )
                continue
            try:
                changed, message = _resync_order(username=username, client=client, order=order)
            except Exception as exc:
                orders_failed += 1
                sync_progress.log(f"FAIL order_id={order.get('id')} error={exc}")
                sync_done += 1
                sync_progress.update(
                    sync_done,
                    details=(
                        f"positions={position_index - 1}/{len(mismatches)}"
                        f" orders={orders_processed}/{total_orders} resolved={resolved} failed={orders_failed}"
                    ),
                    force=True,
                )
                continue
            if message.startswith("trade_fill_sync_failed"):
                orders_failed += 1
            elif changed:
                orders_updated += 1
            else:
                orders_skipped += 1
            sync_done += 1
            sync_progress.log(
                f"ORDER order_id={order.get('id')} changed={str(changed).lower()} result={message}"
            )
            sync_progress.update(
                sync_done,
                details=(
                    f"positions={position_index - 1}/{len(mismatches)}"
                    f" orders={orders_processed}/{total_orders} resolved={resolved} failed={orders_failed}"
                ),
                force=True,
            )

        try:
            is_resolved, new_average, message = _rebuild_position_cycle(item)
        except Exception as exc:
            cycle_failed += 1
            sync_done += 1
            sync_progress.log(f"FAIL position_id={position.get('id')} phase=rebuild error={exc}")
            sync_progress.update(
                sync_done,
                details=(
                    f"positions={position_index}/{len(mismatches)}"
                    f" orders={orders_processed}/{total_orders} resolved={resolved} failed={cycle_failed}"
                ),
                force=True,
            )
            continue
        if is_resolved:
            resolved += 1
        else:
            still_mismatched += 1
        sync_done += 1
        sync_progress.log(
            f"POSITION position_id={position.get('id')} result={message}"
            f" position_avg={item['expected']:.12g} refreshed_open_order_avg={new_average:.12g}"
        )
        sync_progress.update(
            sync_done,
            details=(
                f"positions={position_index}/{len(mismatches)}"
                f" orders={orders_processed}/{total_orders} resolved={resolved} failed={cycle_failed}"
            ),
            force=True,
        )

    sync_progress.finish(
        sync_done,
        details=(
            f"positions={len(mismatches)}/{len(mismatches)}"
            f" orders={orders_processed}/{total_orders} resolved={resolved}"
            f" still_mismatched={still_mismatched} failed={cycle_failed + orders_failed}"
        ),
    )

    calibrated_final = calibration_failed = 0
    if stale_final_rows:
        calibration_progress = ProgressBar("CALIBRATE", len(stale_final_rows))
        calibration_progress.update(0, details="updated=0 failed=0", force=True)
        for index, item in enumerate(stale_final_rows, start=1):
            position_id = int(item["position"]["id"])
            try:
                if db.update_position_history_final_entry_average(position_id, item["expected"]):
                    calibrated_final += 1
            except Exception as exc:
                calibration_failed += 1
                calibration_progress.log(f"FAIL position_id={position_id} phase=calibrate error={exc}")
            calibration_progress.update(
                index,
                details=f"updated={calibrated_final} failed={calibration_failed}",
                force=True,
            )
        calibration_progress.finish(
            len(stale_final_rows),
            details=f"updated={calibrated_final} failed={calibration_failed}",
        )

    print(
        "DONE mode=apply"
        f" scanned={len(rows)} mismatched={len(mismatches)} unresolvable={len(skipped_cycles)}"
        f" orders_processed={orders_processed} orders_updated={orders_updated}"
        f" orders_skipped={orders_skipped} orders_failed={orders_failed}"
        f" resolved={resolved} still_mismatched={still_mismatched} cycle_failed={cycle_failed}"
        f" calibrated_final={calibrated_final} calibration_failed={calibration_failed}",
        flush=True,
    )
    return 2 if orders_failed or cycle_failed or calibration_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
