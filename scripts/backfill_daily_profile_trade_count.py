#!/usr/bin/env python3
"""Rebuild daily_profile trade_count and related daily stats from position_history.

This one-off task recalculates daily_profile rows from position_history using the
same aggregation logic as the backend. It is intended to repair incorrect
trade_count values while also keeping win_count, win_rate, pnl, and commission
consistent with the source history.

Historical account_balance values are preserved by the underlying rebuild logic.

Usage:
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_daily_profile_trade_count.py
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_daily_profile_trade_count.py --username alice
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_daily_profile_trade_count.py --start-date 2026-05-01 --end-date 2026-05-23
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_daily_profile_trade_count.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from trade_relay.env_loader import load_env


load_env(root=ROOT, override=False)


from trade_relay import database as db


def _parse_date(value: str) -> date:
    parsed = db._coerce_utc_date(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"Invalid date value: {value!r}. Expected YYYY-MM-DD.")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild daily_profile trade_count and related aggregates from position_history.",
    )
    parser.add_argument("--username", help="Only rebuild rows for a single username.")
    parser.add_argument("--start-date", type=_parse_date, help="Inclusive UTC date filter, format: YYYY-MM-DD.")
    parser.add_argument("--end-date", type=_parse_date, help="Inclusive UTC date filter, format: YYYY-MM-DD.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the rebuild summary without committing changes.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db.init_db()

    normalized_username = str(args.username or "").strip() or None
    if args.start_date and args.end_date and args.start_date > args.end_date:
        print("Error: start-date cannot be later than end-date.", file=sys.stderr)
        return 1

    user_id = None
    if normalized_username:
        user = db.get_user_by_username(normalized_username)
        if not user:
            print(f"Error: username={normalized_username!r} was not found.", file=sys.stderr)
            return 1
        user_id = int(user["id"])

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            result = db._rebuild_daily_profile_from_history(
                cur,
                user_id=user_id,
                start_date=args.start_date,
                end_date=args.end_date,
            )

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()

        print(
            "DONE"
            f" username={normalized_username or 'ALL'}"
            f" start_date={args.start_date.isoformat() if args.start_date else 'ALL'}"
            f" end_date={args.end_date.isoformat() if args.end_date else 'ALL'}"
            f" deleted={result.get('deleted', 0)}"
            f" rebuilt={result.get('rebuilt', 0)}"
            f" committed={'no' if args.dry_run else 'yes'}"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())