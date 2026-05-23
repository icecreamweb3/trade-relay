#!/usr/bin/env python3
"""Backfill Binance income history into income_history and print reconciliation.

Usage:
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_income_history.py
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_income_history.py --username Carden
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_income_history.py --days 30
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from trade_relay.env_loader import load_env


load_env(root=ROOT, override=False)


from trade_relay import config as cfg
from trade_relay import database as db
from trade_relay.exchange.binance_client import BinanceClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Binance income history and print reconciliation.")
    parser.add_argument("--username", help="Only sync one username.")
    parser.add_argument("--symbol", default="", help="Optional symbol filter for Binance income history. Default: account-level (no symbol filter)")
    parser.add_argument("--days", type=int, default=0, help="Only fetch the most recent N days. 0 means full available history.")
    parser.add_argument("--page-limit", type=int, default=1000, help="Page size per Binance request. Max 1000.")
    parser.add_argument("--max-pages", type=int, default=20, help="Maximum pages to fetch per user.")
    return parser.parse_args()


def _income_dedupe_key(row: dict) -> tuple[str, ...]:
    return (
        str(row.get("tranId") or "").strip(),
        str(row.get("tradeId") or "").strip(),
        str(row.get("incomeType") or "").strip().upper(),
        str(row.get("time") or "").strip(),
        str(row.get("symbol") or "").strip().upper(),
        str(row.get("asset") or "").strip().upper(),
        str(row.get("income") or "").strip(),
    )


def _fetch_all_income_rows(*, client: BinanceClient, symbol: str | None, start_time: datetime | None, page_limit: int, max_pages: int) -> list[dict]:
    collected: list[dict] = []
    seen: set[tuple[str, ...]] = set()
    current_end_time: datetime | None = None

    for _ in range(max_pages):
        batch = client.get_income_history(
            symbol=symbol,
            income_type=None,
            start_time=start_time,
            end_time=current_end_time,
            limit=page_limit,
        ) or []
        if not batch:
            break

        batch_times = []
        for row in batch:
            key = _income_dedupe_key(row)
            if key in seen:
                continue
            seen.add(key)
            collected.append(row)
            raw_time = row.get("time")
            if raw_time not in (None, ""):
                batch_times.append(int(raw_time))

        if len(batch) < page_limit or not batch_times:
            break

        oldest_ms = min(batch_times)
        if start_time is not None and oldest_ms <= int(start_time.timestamp() * 1000):
            break
        current_end_time = datetime.fromtimestamp((oldest_ms - 1) / 1000.0, tz=timezone.utc)

    return collected


def _iter_target_users(username: str | None) -> list[dict]:
    users = db.get_all_active_users_with_api_keys()
    if username:
        normalized = username.strip().lower()
        return [user for user in users if str(user.get("username") or "").strip().lower() == normalized]
    return users


def main() -> int:
    args = parse_args()
    db.init_db()

    symbol = str(args.symbol or "").strip().upper() or None
    start_time = None
    if args.days and args.days > 0:
        start_time = datetime.now(timezone.utc) - timedelta(days=args.days)

    users = _iter_target_users(args.username)
    if not users:
        print("No users matched the requested scope.")
        return 1

    for user in users:
        user_id = int(user.get("id") or 0)
        username = str(user.get("username") or "").strip()
        if user_id <= 0 or not username:
            continue

        client = BinanceClient(
            api_key=cfg.get_api_key(username),
            secret_key=cfg.get_api_secret(username),
            testnet=cfg.is_testnet(username),
        )
        rows = _fetch_all_income_rows(
            client=client,
            symbol=symbol,
            start_time=start_time,
            page_limit=max(1, min(int(args.page_limit), 1000)),
            max_pages=max(1, int(args.max_pages)),
        )
        written = db.upsert_income_history_entries(user_id, username, rows)
        summary = db.get_income_reconciliation_summary(user_id)

        print(
            "SYNC"
            f" username={username}"
            f" fetched={len(rows)}"
            f" written={written}"
            f" income_total={summary['income_total']:.8f}"
            f" balance_net={(summary['balance_net'] if summary['balance_net'] is not None else 0):.8f}"
            f" unexplained_gap={(summary['unexplained_gap'] if summary['unexplained_gap'] is not None else 0):.8f}"
        )
        print(
            "RECON"
            f" username={username}"
            f" realized_pnl={summary['income_realized_pnl']:.8f}"
            f" commission={summary['income_commission']:.8f}"
            f" funding_fee={summary['income_funding_fee']:.8f}"
            f" other_income={summary['income_other']:.8f}"
            f" order_net={summary['order_net']:.8f}"
            f" income_vs_order_realized_gap={summary['income_vs_order_realized_gap']:.8f}"
            f" income_vs_order_commission_gap={summary['income_vs_order_commission_gap']:.8f}"
            f" income_vs_order_net_gap={summary['income_vs_order_net_gap']:.8f}"
            f" position_trades={summary['position_trade_count']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())