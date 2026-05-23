#!/usr/bin/env python3
"""Diagnose position_history rows whose entry_price is mathematically inconsistent.

This script is read-only. It recomputes the expected entry_price from:
    side + quantity + close_price + realized_pnl

and reports rows where the stored entry_price differs by more than the chosen
tolerance.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from trade_relay.env_loader import load_env


load_env(root=ROOT, override=False)


from trade_relay import database as db
from scripts.backfill_position_history_entry_price import _derive_entry_price


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date value: {value!r}. Expected YYYY-MM-DD.") from exc


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _analyze_row(row: dict, tolerance: float) -> tuple[str, float | None, float | None]:
    expected = _derive_entry_price(
        side=row.get("side"),
        quantity=row.get("quantity"),
        close_price=row.get("close_price"),
        realized_pnl=row.get("realized_pnl"),
    )
    if expected is None:
        return "unverifiable", None, None

    actual = _safe_float(row.get("entry_price"))
    delta = abs(actual - expected)
    if delta > tolerance:
        return "mismatch", expected, delta
    return "ok", expected, delta


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose mathematically inconsistent position_history.entry_price values.",
    )
    parser.add_argument("--username", help="Only inspect rows for a single username.")
    parser.add_argument("--start-date", type=_parse_date, help="Inclusive UTC date filter for position_history.created_at.")
    parser.add_argument("--end-date", type=_parse_date, help="Inclusive UTC date filter for position_history.created_at.")
    parser.add_argument("--tolerance", type=float, default=0.01, help="Absolute price tolerance. Default: 0.01")
    parser.add_argument("--limit", type=int, default=200, help="Maximum mismatches to print. Default: 200")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.start_date and args.end_date and args.start_date > args.end_date:
        print("Error: start-date cannot be later than end-date.", file=sys.stderr)
        return 1
    if args.tolerance < 0:
        print("Error: --tolerance must be non-negative.", file=sys.stderr)
        return 1
    if args.limit <= 0:
        print("Error: --limit must be greater than 0.", file=sys.stderr)
        return 1

    db.init_db()

    normalized_username = str(args.username or "").strip() or None
    user_id = None
    if normalized_username:
        user_row = db.get_user_by_username(normalized_username)
        if not user_row:
            print(f"Error: username={normalized_username!r} was not found.", file=sys.stderr)
            return 1
        user_id = int(user_row["id"])

    sql = [
        "SELECT id, user_id, username, symbol, side, quantity, entry_price, close_price, realized_pnl, close_order_id, created_at",
        "FROM position_history",
        "WHERE 1 = 1",
    ]
    params: list[object] = []
    if user_id is not None:
        sql.append("AND user_id = %s")
        params.append(user_id)
    if args.start_date is not None:
        sql.append("AND created_at >= %s")
        params.append(datetime.combine(args.start_date, time.min))
    if args.end_date is not None:
        sql.append("AND created_at < %s")
        params.append(datetime.combine(args.end_date + timedelta(days=1), time.min))
    sql.append("ORDER BY created_at ASC, id ASC")

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("\n".join(sql), params)
            rows = cur.fetchall() or []
    finally:
        conn.close()

    scanned = 0
    ok = 0
    mismatches = 0
    unverifiable = 0
    printed = 0

    for row in rows:
        scanned += 1
        status, expected, delta = _analyze_row(row, args.tolerance)
        if status == "ok":
            ok += 1
            continue
        if status == "unverifiable":
            unverifiable += 1
            continue

        mismatches += 1
        if printed < args.limit:
            printed += 1
            print(
                "MISMATCH"
                f" history_id={row.get('id')}"
                f" username={row.get('username')}"
                f" symbol={row.get('symbol')}"
                f" side={row.get('side')}"
                f" entry_price={_safe_float(row.get('entry_price')):.10f}"
                f" expected_entry_price={(expected or 0.0):.10f}"
                f" delta={(delta or 0.0):.10f}"
                f" close_price={_safe_float(row.get('close_price')):.10f}"
                f" quantity={_safe_float(row.get('quantity')):.10f}"
                f" realized_pnl={_safe_float(row.get('realized_pnl')):.10f}"
                f" close_order_id={row.get('close_order_id')}"
                f" created_at={row.get('created_at')}"
            )

    scope = normalized_username or "ALL"
    print(
        "DONE"
        f" scope={scope}"
        f" scanned={scanned}"
        f" ok={ok}"
        f" mismatches={mismatches}"
        f" unverifiable={unverifiable}"
        f" tolerance={args.tolerance}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())