#!/usr/bin/env python3
"""Backfill daily_profile.account_balance using a fixed initial balance.

Each user's daily closing balance is computed as:

    closing_balance = initial_balance + cumulative_sum(pnl - commission)

ordered by profile_date ascending.

Usage:
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_daily_profile_account_balance.py
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_daily_profile_account_balance.py --username alice
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_daily_profile_account_balance.py --initial-balance 200
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_daily_profile_account_balance.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from trade_relay.env_loader import load_env


load_env(root=ROOT, override=False)


from trade_relay import database as db


BALANCE_PRECISION = Decimal("0.0001")


def _parse_decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"Invalid decimal value: {value!r}") from exc


def _to_decimal(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def build_balance_updates(rows: list[dict], initial_balance: Decimal) -> list[tuple[Decimal, int]]:
    updates: list[tuple[Decimal, int]] = []
    current_user_id: int | None = None
    running_balance = initial_balance

    for row in rows:
        user_id = int(row["user_id"])
        if user_id != current_user_id:
            current_user_id = user_id
            running_balance = initial_balance

        net_profit = _to_decimal(row.get("pnl")) - _to_decimal(row.get("commission"))
        running_balance = (running_balance + net_profit).quantize(BALANCE_PRECISION, rounding=ROUND_HALF_UP)
        updates.append((running_balance, int(row["id"])))

    return updates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill daily_profile.account_balance from a fixed initial balance.",
    )
    parser.add_argument("--username", help="Only backfill rows for a single username.")
    parser.add_argument(
        "--initial-balance",
        type=_parse_decimal,
        default=Decimal("200"),
        help="Initial account balance for each user before their first trading day. Default: 200",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the result summary without committing updates.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    db.init_db()

    normalized_username = str(args.username or "").strip() or None
    if normalized_username:
        user = db.get_user_by_username(normalized_username)
        if not user:
            print(f"Error: username={normalized_username!r} was not found.", file=sys.stderr)
            return 1
        scoped_user_id = int(user["id"])
    else:
        scoped_user_id = None

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            if scoped_user_id is None:
                cur.execute(
                    """
                    SELECT id, user_id, username, profile_date, pnl, commission
                    FROM daily_profile
                    ORDER BY user_id ASC, profile_date ASC, id ASC
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT id, user_id, username, profile_date, pnl, commission
                    FROM daily_profile
                    WHERE user_id = %s
                    ORDER BY user_id ASC, profile_date ASC, id ASC
                    """,
                    (scoped_user_id,),
                )

            rows = cur.fetchall() or []
            updates = build_balance_updates(rows, args.initial_balance)

            for account_balance, row_id in updates:
                cur.execute(
                    "UPDATE daily_profile SET account_balance = %s WHERE id = %s",
                    (account_balance, row_id),
                )

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()

        affected_users = len({int(row["user_id"]) for row in rows})
        print(
            "DONE"
            f" username={normalized_username or 'ALL'}"
            f" initial_balance={args.initial_balance.quantize(BALANCE_PRECISION, rounding=ROUND_HALF_UP)}"
            f" rows_scanned={len(rows)}"
            f" rows_updated={len(updates)}"
            f" users={affected_users}"
            f" committed={'no' if args.dry_run else 'yes'}"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())