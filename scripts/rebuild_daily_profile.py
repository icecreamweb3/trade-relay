#!/usr/bin/env python3
"""Rebuild daily_profile rows from position_history.

This task deletes existing daily_profile rows for the selected scope and rebuilds
them from historical position_history records using UTC day boundaries.

Usage:
    /home/will/project/trade-relay/.venv/bin/python scripts/rebuild_daily_profile.py
    /home/will/project/trade-relay/.venv/bin/python scripts/rebuild_daily_profile.py --username alice
    /home/will/project/trade-relay/.venv/bin/python scripts/rebuild_daily_profile.py --from-date 2026-05-01 --to-date 2026-05-16
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
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date: {value!r}. Expected YYYY-MM-DD.") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rebuild daily_profile rows from position_history.",
    )
    parser.add_argument("--username", help="Only rebuild rows for a single username.")
    parser.add_argument(
        "--from-date",
        type=_parse_date,
        help="Only rebuild rows on or after this UTC date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--to-date",
        type=_parse_date,
        help="Only rebuild rows on or before this UTC date (YYYY-MM-DD).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.from_date and args.to_date and args.from_date > args.to_date:
        print("Error: --from-date cannot be later than --to-date.", file=sys.stderr)
        return 1

    db.init_db()
    result = db.rebuild_daily_profile(
        username=args.username,
        start_date=args.from_date,
        end_date=args.to_date,
    )

    if not result.get("ok"):
        print(
            f"Error: username={result.get('username')} was not found or is inactive.",
            file=sys.stderr,
        )
        return 1

    print(
        "DONE"
        f" username={result.get('username') or 'ALL'}"
        f" start_date={result.get('start_date') or 'MIN'}"
        f" end_date={result.get('end_date') or 'MAX'}"
        f" deleted={result.get('deleted')}"
        f" rebuilt={result.get('rebuilt')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())