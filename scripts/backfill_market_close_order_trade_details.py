#!/usr/bin/env python3
"""Backfill commission and realized_pnl for Market Close orders.

This script scans the orders table for FILLED MARKET CLOSE rows that are missing
one or more of:
- commission
- commission_asset
- realized_pnl

For each candidate order it queries Binance per-fill trade details and reuses the
application's existing sync logic to update the row (and related position_history).

Usage:
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_market_close_order_trade_details.py
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_market_close_order_trade_details.py --username alice
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_market_close_order_trade_details.py --batch-size 200 --max-orders 500
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_market_close_order_trade_details.py --dry-run
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


from trade_relay import config as cfg
from trade_relay import database as db
from trade_relay.exchange.binance_client import BinanceClient
from trade_relay.trading.close_trade_sync import sync_filled_order_trade_details


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill missing commission / commission_asset / realized_pnl for MARKET CLOSE orders.",
    )
    parser.add_argument(
        "--username",
        help="Only process orders for a single username.",
    )
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print candidate orders without writing to the database.",
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
          AND UPPER(order_type) = 'MARKET'
          AND UPPER(COALESCE(trade_direction, '')) = 'CLOSE'
          AND exchange_order_id IS NOT NULL
          AND TRIM(exchange_order_id) <> ''
          AND symbol IS NOT NULL
          AND TRIM(symbol) <> ''
          AND (
                commission IS NULL
             OR commission_asset IS NULL
             OR TRIM(COALESCE(commission_asset, '')) = ''
             OR realized_pnl IS NULL
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


def _build_clients(username_filter: Optional[str]) -> dict[str, BinanceClient]:
    clients: dict[str, BinanceClient] = {}
    users = db.get_all_users()
    for user in users:
        username = str(user.get("username") or "").strip()
        if not username:
            continue
        if username_filter and username != username_filter:
            continue
        api_key = cfg.get_api_key(username)
        api_secret = cfg.get_api_secret(username)
        if not api_key or not api_secret:
            continue
        clients[username] = BinanceClient(
            api_key=api_key,
            secret_key=api_secret,
            testnet=cfg.is_testnet(username),
        )
    return clients


def main() -> int:
    args = parse_args()
    if args.batch_size <= 0:
        print("Error: --batch-size must be greater than 0.", file=sys.stderr)
        return 1
    if args.max_orders < 0:
        print("Error: --max-orders cannot be negative.", file=sys.stderr)
        return 1

    if args.dry_run:
        print("[DRY RUN] No database writes will be performed.")

    clients = _build_clients(args.username)
    if not clients:
        scope = args.username or "all users"
        print(f"No Binance API credentials found for {scope}.", file=sys.stderr)
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
            username = str(row.get("username") or "").strip()
            order_id = row.get("id")
            symbol = str(row.get("symbol") or "").strip()
            exchange_order_id = str(row.get("exchange_order_id") or "").strip()
            client = clients.get(username)

            if client is None:
                skipped += 1
                print(
                    f"SKIP order_id={order_id} username={username}"
                    f" symbol={symbol} reason=no_api_credentials"
                )
                continue

            print(
                f"PROCESSING order_id={order_id} username={username}"
                f" symbol={symbol} exchange_order_id={exchange_order_id}"
                f" commission={row.get('commission')} realized_pnl={row.get('realized_pnl')}"
            )

            if args.dry_run:
                skipped += 1
                continue

            before = db.get_order_by_id(int(order_id)) if order_id else None
            try:
                sync_filled_order_trade_details(username=username, client=client, order_row=row)
            except Exception as exc:
                failed += 1
                print(f"FAIL order_id={order_id} username={username} symbol={symbol} error={exc}")
                continue

            after = db.get_order_by_id(int(order_id)) if order_id else None
            changed = bool(after) and (
                (before or {}).get("commission") != after.get("commission")
                or (before or {}).get("commission_asset") != after.get("commission_asset")
                or (before or {}).get("realized_pnl") != after.get("realized_pnl")
            )
            if changed:
                updated += 1
                print(
                    "UPDATED"
                    f" order_id={order_id}"
                    f" username={username}"
                    f" symbol={symbol}"
                    f" commission={after.get('commission')}"
                    f" asset={after.get('commission_asset')}"
                    f" realized_pnl={after.get('realized_pnl')}"
                )
            else:
                skipped += 1
                print(
                    f"SKIP order_id={order_id} username={username}"
                    f" symbol={symbol} reason=no_change"
                )

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
