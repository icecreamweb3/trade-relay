#!/usr/bin/env python3
"""Backfill position_history.entry_price for rows where it is missing or invalid.

This one-off task repairs historical rows conservatively when entry_price is
NULL, zero, or otherwise non-positive.

Priority order for reconstruction:
1. derive from the position_history row itself using side/quantity/close_price/realized_pnl
2. fall back to the linked filled CLOSE order via close_order_id when present

Rows that still cannot be reconstructed safely are skipped.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from trade_relay.env_loader import load_env


load_env(root=ROOT, override=False)


from trade_relay import database as db


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


def _derive_entry_price(*, side: object, quantity: object, close_price: object, realized_pnl: object) -> float | None:
    normalized_side = str(side or "").strip().upper()
    qty = abs(_safe_float(quantity))
    close_px = _safe_float(close_price)
    pnl = _safe_float(realized_pnl)

    if normalized_side not in {"LONG", "SHORT"}:
        return None
    if qty <= 0 or close_px <= 0:
        return None

    if normalized_side == "LONG":
        return close_px - (pnl / qty)
    return close_px + (pnl / qty)


def _resolve_entry_price(history_row: dict, order_row: dict | None) -> tuple[float | None, str]:
    derived = _derive_entry_price(
        side=history_row.get("side"),
        quantity=history_row.get("quantity"),
        close_price=history_row.get("close_price"),
        realized_pnl=history_row.get("realized_pnl"),
    )
    if derived is not None:
        return derived, "history_formula"

    if order_row is None:
        return None, "missing_inputs"

    derived = _derive_entry_price(
        side=history_row.get("side"),
        quantity=order_row.get("filled_qty") or order_row.get("quantity"),
        close_price=order_row.get("avg_price") or order_row.get("price"),
        realized_pnl=order_row.get("realized_pnl"),
    )
    if derived is not None:
        return derived, "close_order_formula"

    return None, "missing_inputs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill NULL or non-positive position_history.entry_price values.",
    )
    parser.add_argument("--username", help="Only process rows for a single username.")
    parser.add_argument("--start-date", type=_parse_date, help="Inclusive UTC date filter for position_history.created_at.")
    parser.add_argument("--end-date", type=_parse_date, help="Inclusive UTC date filter for position_history.created_at.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without committing changes.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.start_date and args.end_date and args.start_date > args.end_date:
        print("Error: start-date cannot be later than end-date.", file=sys.stderr)
        return 1

    db.init_db()

    normalized_username = str(args.username or "").strip() or None
    user_id: Optional[int] = None
    if normalized_username:
        user_row = db.get_user_by_username(normalized_username)
        if not user_row:
            print(f"Error: username={normalized_username!r} was not found.", file=sys.stderr)
            return 1
        user_id = int(user_row["id"])

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            history_sql = [
                "SELECT id, user_id, username, symbol, side, quantity, close_price, realized_pnl, close_order_id, created_at",
                "FROM position_history",
                "WHERE COALESCE(entry_price, 0) <= 0",
            ]
            history_params: list[object] = []
            if user_id is not None:
                history_sql.append("AND user_id = %s")
                history_params.append(user_id)
            if args.start_date is not None:
                history_sql.append("AND created_at >= %s")
                history_params.append(datetime.combine(args.start_date, time.min))
            if args.end_date is not None:
                history_sql.append("AND created_at < %s")
                history_params.append(datetime.combine(args.end_date + timedelta(days=1), time.min))
            history_sql.append("ORDER BY user_id ASC, created_at ASC, id ASC")

            cur.execute("\n".join(history_sql), history_params)
            history_rows = cur.fetchall() or []

            close_order_ids = sorted({
                int(row["close_order_id"])
                for row in history_rows
                if row.get("close_order_id") is not None
            })
            order_by_id: dict[int, dict] = {}
            if close_order_ids:
                placeholders = ", ".join(["%s"] * len(close_order_ids))
                cur.execute(
                    f"""
                    SELECT id, quantity, filled_qty, price, avg_price, realized_pnl
                    FROM orders
                    WHERE id IN ({placeholders})
                    """,
                    close_order_ids,
                )
                order_by_id = {
                    int(row["id"]): row
                    for row in (cur.fetchall() or [])
                    if row.get("id") is not None
                }

            scanned = len(history_rows)
            updated = 0
            skipped = 0

            for history_row in history_rows:
                order_row = None
                if history_row.get("close_order_id") is not None:
                    order_row = order_by_id.get(int(history_row["close_order_id"]))
                entry_price, source = _resolve_entry_price(history_row, order_row)
                if entry_price is None:
                    skipped += 1
                    print(
                        "SKIP"
                        f" history_id={history_row.get('id')}"
                        f" username={history_row.get('username')}"
                        f" symbol={history_row.get('symbol')}"
                        f" source={source}"
                    )
                    continue

                if args.dry_run:
                    updated += 1
                    print(
                        "DRY_RUN"
                        f" history_id={history_row.get('id')}"
                        f" username={history_row.get('username')}"
                        f" symbol={history_row.get('symbol')}"
                        f" entry_price={entry_price:.10f}"
                        f" source={source}"
                    )
                    continue

                cur.execute(
                    "UPDATE position_history SET entry_price = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s AND COALESCE(entry_price, 0) <= 0",
                    (entry_price, int(history_row["id"])),
                )
                if cur.rowcount > 0:
                    updated += 1
                    print(
                        "UPDATED"
                        f" history_id={history_row.get('id')}"
                        f" username={history_row.get('username')}"
                        f" symbol={history_row.get('symbol')}"
                        f" entry_price={entry_price:.10f}"
                        f" source={source}"
                    )
                else:
                    skipped += 1
                    print(
                        "SKIP"
                        f" history_id={history_row.get('id')}"
                        f" username={history_row.get('username')}"
                        f" symbol={history_row.get('symbol')}"
                        f" source=concurrent_change"
                    )

            if args.dry_run:
                conn.rollback()
            else:
                conn.commit()

    finally:
        conn.close()

    scope = normalized_username or "ALL"
    print(
        "DONE"
        f" scope={scope}"
        f" scanned={scanned}"
        f" updated={updated}"
        f" skipped={skipped}"
        f" dry_run={'yes' if args.dry_run else 'no'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())