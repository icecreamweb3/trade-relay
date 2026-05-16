#!/usr/bin/env python3
"""Backfill position_history commission fields from filled CLOSE orders.

This task reads already-synced values from the orders table and pushes the
aggregated commission / commission_asset / realized_pnl values into the related
position_history rows.

Usage:
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_position_history_from_filled_orders.py
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_position_history_from_filled_orders.py --username alice
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_position_history_from_filled_orders.py --batch-size 200 --max-orders 500
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from trade_relay.env_loader import load_env


load_env(root=ROOT, override=False)


from trade_relay import database as db
from trade_relay.trading.close_trade_sync import sync_position_history_from_filled_close_order


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill position_history commission / commission asset from filled CLOSE orders.",
    )
    parser.add_argument("--username", help="Only process orders for a single username.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of candidate orders to fetch per batch. Default: 100.",
    )
    parser.add_argument(
        "--max-orders",
        type=int,
        default=0,
        help="Maximum number of orders to process. 0 means no limit.",
    )
    return parser.parse_args()


def _iter_candidate_orders(*, username: Optional[str], batch_size: int) -> Iterable[list[dict]]:
    processed_ids: set[int] = set()
    while True:
        batch = _fetch_candidate_orders(username=username, batch_size=batch_size, exclude_ids=processed_ids)
        if not batch:
            break
        for row in batch:
            if row.get("id"):
                processed_ids.add(int(row["id"]))
        yield batch


def _fetch_candidate_orders(*, username: Optional[str], batch_size: int, exclude_ids: set[int]) -> list[dict]:
    sql = [
        """
        SELECT *
        FROM orders
        WHERE status = 'FILLED'
          AND UPPER(COALESCE(trade_direction, '')) = 'CLOSE'
          AND user_id IS NOT NULL
          AND (
                commission IS NOT NULL
             OR realized_pnl IS NOT NULL
             OR TRIM(COALESCE(commission_asset, '')) <> ''
          )
        """
    ]
    params: list[object] = []

    if username:
        sql.append("AND username = %s")
        params.append(username)

    if exclude_ids:
        placeholders = ", ".join(["%s"] * len(exclude_ids))
        sql.append(f"AND id NOT IN ({placeholders})")
        params.extend(sorted(exclude_ids))

    sql.append("ORDER BY created_at ASC, id ASC LIMIT %s")
    params.append(batch_size)

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("\n".join(sql), params)
            return cur.fetchall()
    finally:
        conn.close()


def main() -> int:
    args = parse_args()
    if args.batch_size <= 0:
        print("Error: --batch-size must be greater than 0.", file=sys.stderr)
        return 1
    if args.max_orders < 0:
        print("Error: --max-orders cannot be negative.", file=sys.stderr)
        return 1

    processed = 0
    updated = 0
    skipped = 0
    failed = 0

    for batch in _iter_candidate_orders(username=args.username, batch_size=args.batch_size):
        for row in batch:
            if args.max_orders and processed >= args.max_orders:
                break

            processed += 1
            order_id = row.get("id")
            username = str(row.get("username") or "").strip()

            try:
                updated_rows = sync_position_history_from_filled_close_order(row)
            except Exception as exc:
                failed += 1
                print(f"FAIL order_id={order_id} username={username} error={exc}")
                continue

            if updated_rows > 0:
                updated += 1
                print(
                    "UPDATED"
                    f" order_id={order_id}"
                    f" username={username}"
                    f" history_rows={updated_rows}"
                    f" commission={row.get('commission')}"
                    f" asset={row.get('commission_asset')}"
                    f" realized_pnl={row.get('realized_pnl')}"
                )
            else:
                skipped += 1
                print(f"SKIP order_id={order_id} username={username} reason=no_related_change")

        if args.max_orders and processed >= args.max_orders:
            break

    print(
        "DONE"
        f" processed={processed}"
        f" updated={updated}"
        f" skipped={skipped}"
        f" failed={failed}"
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())