#!/usr/bin/env python3
"""Backfill real order ids and trade details for triggered conditional orders.

This task scans conditional orders in the local orders table, queries Binance algo
order details to discover the actual generated orderId after trigger, stores that
real orderId into orders.exchange_order_id, and then reuses the application's
existing fill sync logic to backfill commission, commission_asset, and realized_pnl.

For CLOSE orders, reusing sync_filled_order_trade_details also updates related
position_history aggregated fields.

Usage:
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_triggered_conditional_order_trade_details.py
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_triggered_conditional_order_trade_details.py --username alice
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_triggered_conditional_order_trade_details.py --batch-size 200 --max-orders 500
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
        description="Backfill actual order ids and final trade details for triggered conditional orders.",
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
        WHERE order_category = 'Conditional'
          AND symbol IS NOT NULL
          AND TRIM(symbol) <> ''
          AND UPPER(COALESCE(trade_direction, '')) IN ('OPEN', 'CLOSE')
          AND (
                (algo_id IS NOT NULL AND TRIM(COALESCE(algo_id, '')) <> '')
                 OR (algo_client_id IS NOT NULL AND TRIM(COALESCE(algo_client_id, '')) <> '')
          )
          AND (
                exchange_order_id IS NULL
             OR TRIM(COALESCE(exchange_order_id, '')) = ''
             OR status IN ('NEW', 'PARTIALLY_FILLED')
             OR commission IS NULL
             OR commission_asset IS NULL
             OR TRIM(COALESCE(commission_asset, '')) = ''
             OR (UPPER(COALESCE(trade_direction, '')) = 'CLOSE' AND realized_pnl IS NULL)
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
    for user in db.get_all_users():
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


def _map_algo_status_to_db_status(algo_status: Optional[str]) -> Optional[str]:
    normalized = str(algo_status or "").upper().strip()
    if not normalized:
        return None
    if normalized in {"NEW", "WORKING", "TRIGGERING", "TRIGGERED"}:
        return "NEW"
    if normalized in {"PARTIALLY_FILLED"}:
        return "PARTIALLY_FILLED"
    if normalized in {"FINISHED", "FILLED"}:
        return "FILLED"
    if normalized in {"CANCELED", "EXPIRED", "REJECTED"}:
        return normalized
    return normalized


def _safe_float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _resolve_algo_detail(*, client: BinanceClient, row: dict) -> dict | None:
    algo_id_text = str(row.get("algo_id") or "").strip()
    client_algo_id = str(row.get("algo_client_id") or row.get("client_order_id") or "").strip() or None
    if not algo_id_text and not client_algo_id:
        return None

    try:
        return client.get_algo_order(
            algo_id=int(algo_id_text) if algo_id_text else None,
            client_algo_id=None if algo_id_text else client_algo_id,
        )
    except Exception:
        return None


def _sync_candidate_order(*, client: BinanceClient, row: dict) -> dict:
    order_id = int(row["id"])
    before = db.get_order_by_id(order_id) or row
    username = str(before.get("username") or "")
    symbol = str(before.get("symbol") or "").upper()

    algo_detail = _resolve_algo_detail(client=client, row=before)
    actual_order_id = str(before.get("exchange_order_id") or "").strip() or None
    detail_status = None

    if isinstance(algo_detail, dict):
        actual_order_id = str(algo_detail.get("actualOrderId") or algo_detail.get("orderId") or "").strip() or actual_order_id
        detail_status = _map_algo_status_to_db_status(algo_detail.get("algoStatus") or algo_detail.get("status"))
        metadata_fields = {}
        algo_id_from_detail = str(algo_detail.get("algoId") or "").strip()
        client_algo_id = str(algo_detail.get("clientAlgoId") or "").strip()
        if algo_id_from_detail and not before.get("algo_id"):
            metadata_fields["algo_id"] = algo_id_from_detail
        if client_algo_id and not before.get("algo_client_id"):
            metadata_fields["algo_client_id"] = client_algo_id
        if actual_order_id and actual_order_id != str(before.get("exchange_order_id") or ""):
            metadata_fields["exchange_order_id"] = actual_order_id
        if metadata_fields:
            db.update_order_metadata(order_id, **metadata_fields)

    order_status_result = None
    if actual_order_id and symbol:
        try:
            order_status_result = client.get_order_status(symbol, actual_order_id)
        except Exception:
            order_status_result = None

    status_to_apply = None
    filled_qty = None
    avg_price = None
    if isinstance(order_status_result, dict):
        status_to_apply = str(order_status_result.get("status") or "").upper().strip() or None
        filled_qty = _safe_float(order_status_result.get("executedQty"))
        avg_price = _safe_float(order_status_result.get("avgPrice"))
    elif detail_status:
        status_to_apply = detail_status
        if detail_status in {"FILLED", "PARTIALLY_FILLED"}:
            filled_qty = _safe_float(algo_detail.get("quantity") if isinstance(algo_detail, dict) else None)
            avg_price = _safe_float(algo_detail.get("actualPrice") if isinstance(algo_detail, dict) else None)

    if status_to_apply:
        db.update_order_status(
            order_id,
            status_to_apply,
            filled_qty=filled_qty if filled_qty and filled_qty > 0 else None,
            avg_price=avg_price if avg_price and avg_price > 0 else None,
        )

    latest = db.get_order_by_id(order_id) or before
    if actual_order_id and actual_order_id != str(latest.get("exchange_order_id") or ""):
        latest = {**latest, "exchange_order_id": actual_order_id}

    trade_sync_applied = False
    latest_status = str(latest.get("status") or status_to_apply or "").upper()
    if actual_order_id and latest_status in {"PARTIALLY_FILLED", "FILLED"}:
        sync_filled_order_trade_details(
            username=username,
            client=client,
            order_row={**latest, "exchange_order_id": actual_order_id},
        )
        trade_sync_applied = True

    after = db.get_order_by_id(order_id) or latest
    changed = (
        (before or {}).get("exchange_order_id") != after.get("exchange_order_id")
        or (before or {}).get("status") != after.get("status")
        or (before or {}).get("filled_qty") != after.get("filled_qty")
        or (before or {}).get("avg_price") != after.get("avg_price")
        or (before or {}).get("commission") != after.get("commission")
        or (before or {}).get("commission_asset") != after.get("commission_asset")
        or (before or {}).get("realized_pnl") != after.get("realized_pnl")
    )
    return {
        "changed": changed,
        "trade_sync_applied": trade_sync_applied,
        "before": before,
        "after": after,
        "actual_order_id": actual_order_id,
    }


def main() -> int:
    args = parse_args()
    if args.batch_size <= 0:
        print("Error: --batch-size must be greater than 0.", file=sys.stderr)
        return 1
    if args.max_orders < 0:
        print("Error: --max-orders cannot be negative.", file=sys.stderr)
        return 1

    clients = _build_clients(args.username)
    if not clients:
        scope = args.username or "all users"
        print(f"No Binance API credentials found for {scope}.", file=sys.stderr)
        return 1

    processed = 0
    linked = 0
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
            client = clients.get(username)
            if client is None:
                skipped += 1
                print(f"SKIP order_id={order_id} username={username} reason=no_api_credentials")
                continue

            try:
                result = _sync_candidate_order(client=client, row=row)
            except Exception as exc:
                failed += 1
                print(f"FAIL order_id={order_id} username={username} error={exc}")
                continue

            before = result["before"]
            after = result["after"]
            if (before or {}).get("exchange_order_id") != after.get("exchange_order_id") and after.get("exchange_order_id"):
                linked += 1

            if result["changed"]:
                updated += 1
                print(
                    "UPDATED"
                    f" order_id={order_id}"
                    f" username={username}"
                    f" algo_id={after.get('algo_id')}"
                    f" real_order_id={after.get('exchange_order_id')}"
                    f" status={after.get('status')}"
                    f" commission={after.get('commission')}"
                    f" asset={after.get('commission_asset')}"
                    f" realized_pnl={after.get('realized_pnl')}"
                    f" trade_sync={result['trade_sync_applied']}"
                )
            else:
                skipped += 1
                print(
                    f"SKIP order_id={order_id} username={username} algo_id={row.get('algo_id')} reason=no_change"
                )

        if args.max_orders and processed >= args.max_orders:
            break

    print(
        "DONE"
        f" processed={processed}"
        f" linked={linked}"
        f" updated={updated}"
        f" skipped={skipped}"
        f" failed={failed}"
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())