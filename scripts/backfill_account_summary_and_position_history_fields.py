#!/usr/bin/env python3
"""Backfill account_summary and position_history fields.

This one-off task backfills:
1. account_summary.position_mode
2. account_summary.configured_leverage
3. position_history.position_mode

For account_summary, the script reuses the live Binance refresh path so the
stored values match the current exchange state.

For position_history, the script force-fills any non-SINGLE/DUAL value to DUAL.

Usage:
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_account_summary_and_position_history_fields.py
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_account_summary_and_position_history_fields.py --username alice
    /home/will/project/trade-relay/.venv/bin/python scripts/backfill_account_summary_and_position_history_fields.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from trade_relay.env_loader import load_env


load_env(root=ROOT, override=False)


from trade_relay import database as db
from trade_relay.exchange.account_sync import _fetch_and_store


VALID_POSITION_MODES = {"SINGLE", "DUAL"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill account_summary.position_mode/configured_leverage and position_history.position_mode.",
    )
    parser.add_argument("--username", help="Only process a single username.")
    parser.add_argument(
        "--skip-account-summary",
        action="store_true",
        help="Skip account_summary refresh/backfill.",
    )
    parser.add_argument(
        "--skip-position-history",
        action="store_true",
        help="Skip position_history.position_mode backfill.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without writing to the database.",
    )
    return parser.parse_args()


def _normalize_position_mode(value: object) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in VALID_POSITION_MODES else "UNKNOWN"


def _fetch_account_summary_targets(username: Optional[str]) -> list[tuple[int, str, Optional[str]]]:
    sql = [
        """
        SELECT DISTINCT u.id AS user_id, u.username, s.symbol
        FROM users u
        INNER JOIN account_summary s ON s.user_id = u.id
        WHERE u.is_active = 1
          AND u.binance_api_key IS NOT NULL
          AND TRIM(COALESCE(u.binance_api_key, '')) <> ''
        """
    ]
    params: list[object] = []
    if username:
        sql.append("AND u.username = %s")
        params.append(username)
    sql.append("ORDER BY u.username ASC, s.symbol ASC")

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("\n".join(sql), params)
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        (int(row["user_id"]), str(row["username"]), row.get("symbol"))
        for row in rows
        if int(row.get("user_id") or 0) > 0 and str(row.get("username") or "").strip()
    ]


def _backfill_account_summary(username: Optional[str], dry_run: bool) -> int:
    targets = _fetch_account_summary_targets(username)
    refreshed = 0

    if not targets:
        print("ACCOUNT_SUMMARY no_targets=1")
        return 0

    for user_id, target_username, symbol in targets:
        symbol_label = symbol or "<latest>"
        if dry_run:
            print(f"ACCOUNT_SUMMARY DRY_RUN username={target_username} user_id={user_id} symbol={symbol_label}")
            refreshed += 1
            continue

        try:
            _fetch_and_store(user_id, target_username, symbol)
            refreshed += 1
            print(f"ACCOUNT_SUMMARY UPDATED username={target_username} user_id={user_id} symbol={symbol_label}")
        except Exception as exc:
            print(
                f"ACCOUNT_SUMMARY FAIL username={target_username}"
                f" user_id={user_id} symbol={symbol_label} error={exc}",
                file=sys.stderr,
            )

    return refreshed


def _count_position_history_force_dual_targets(username: Optional[str]) -> int:
    sql = [
        """
        SELECT COUNT(*) AS total
        FROM position_history
        WHERE UPPER(COALESCE(position_mode, 'UNKNOWN')) NOT IN ('SINGLE', 'DUAL')
        """
    ]
    params: list[object] = []

    if username:
        sql.append("AND username = %s")
        params.append(username)

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("\n".join(sql), params)
            row = cur.fetchone() or {}
            return int(row.get("total") or 0)
    finally:
        conn.close()


def _backfill_position_history_force_dual(username: Optional[str], dry_run: bool) -> int:
    candidate_count = _count_position_history_force_dual_targets(username)
    if dry_run:
        scope = username or "ALL"
        print(f"POSITION_HISTORY DRY_RUN scope={scope} force_mode=DUAL candidate_rows={candidate_count}")
        return candidate_count

    sql = [
        """
        UPDATE position_history
        SET position_mode = 'DUAL', updated_at = CURRENT_TIMESTAMP
        WHERE UPPER(COALESCE(position_mode, 'UNKNOWN')) NOT IN ('SINGLE', 'DUAL')
        """
    ]
    params: list[object] = []
    if username:
        sql.append("AND username = %s")
        params.append(username)

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("\n".join(sql), params)
            conn.commit()
            updated = cur.rowcount
    finally:
        conn.close()

    scope = username or "ALL"
    print(f"POSITION_HISTORY UPDATED scope={scope} force_mode=DUAL rows={updated}")
    return int(updated)


def main() -> int:
    args = parse_args()
    if args.skip_account_summary and args.skip_position_history:
        print("Error: nothing to do; both sections are skipped.", file=sys.stderr)
        return 1

    account_summary_refreshed = 0
    history_force_dual = 0

    if not args.skip_account_summary:
        account_summary_refreshed = _backfill_account_summary(args.username, args.dry_run)

    if not args.skip_position_history:
        history_force_dual = _backfill_position_history_force_dual(args.username, args.dry_run)

    print(
        "DONE"
        f" account_summary_refreshed={account_summary_refreshed}"
        f" history_force_dual={history_force_dual}"
        f" dry_run={int(args.dry_run)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())