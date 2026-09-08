#!/usr/bin/env python3
"""Backfill position_history_final OPEN/CLOSE order IDs from local fills.

The task reconstructs cycles using the exact position_history.close_order_id anchor.
It never calls Binance. Ambiguous or incomplete cycles are skipped instead of linked
to a nearby position.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trade_relay.env_loader import load_env


load_env(root=ROOT, override=False)

from trade_relay.trading.excursion_retry_worker import _repair_missing_order_links


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill orders.position_id and position_history_final "
            "open_orders_id/close_orders_id from local filled orders."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Maximum linked-position rows and legacy rows to scan (default: 5000 each).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report repairable rows without updating the database.",
    )
    parser.add_argument("--user-id", "--user_id", type=int, help="Only process one users.id.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit <= 0:
        print("ERROR limit must be greater than zero", file=sys.stderr)
        return 2

    if args.user_id is not None and args.user_id <= 0:
        print("ERROR --user-id must be greater than zero", file=sys.stderr)
        return 2
    repaired, failed = _repair_missing_order_links(
        args.limit,
        dry_run=args.dry_run,
        user_id=args.user_id,
    )
    print(
        "DONE"
        f" mode={'dry-run' if args.dry_run else 'apply'}"
        f" repairable_or_repaired={repaired}"
        f" skipped={failed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
