#!/usr/bin/env python3
"""Convert legacy local timestamps stored as UTC+8 into UTC.

This is a one-time migration script for tables that historically stored local
time values in DATETIME columns. The script subtracts 8 hours from the selected
columns so the persisted values become UTC.

Default mode is preview-only. Use --apply to execute updates.

Usage:
    /home/will/project/trade-relay/.venv/bin/python scripts/convert_local_utc8_timestamps_to_utc.py
    /home/will/project/trade-relay/.venv/bin/python scripts/convert_local_utc8_timestamps_to_utc.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from trade_relay.env_loader import load_env


load_env(root=ROOT, override=False)


from trade_relay import database as db


MIGRATION_ACTION = "one_time_convert_local_utc8_to_utc"
UTC8_OFFSET_SQL = "INTERVAL 8 HOUR"


@dataclass(frozen=True)
class TablePlan:
    table: str
    columns: tuple[str, ...]


TABLE_PLANS: tuple[TablePlan, ...] = (
    TablePlan("account_summary", ("synced_at",)),
    TablePlan("operation_logs", ("created_at",)),
    TablePlan("orders", ("created_at", "updated_at")),
    TablePlan("position_history", ("updated_at",)),
    TablePlan("users", ("created_at", "updated_at")),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert legacy UTC+8 DATETIME values to UTC.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the updates. Without this flag, the script only prints a preview.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow execution even if the migration marker already exists.",
    )
    return parser.parse_args()


def _preview_sql(table: str, column: str) -> str:
    return (
        f"SELECT COUNT(*) AS non_null_count, MIN({column}) AS min_value, MAX({column}) AS max_value "
        f"FROM {table} WHERE {column} IS NOT NULL"
    )


def _update_sql(plan: TablePlan) -> str:
    assignments = ", ".join(
        f"{column} = DATE_SUB({column}, {UTC8_OFFSET_SQL})" for column in plan.columns
    )
    predicates = " OR ".join(f"{column} IS NOT NULL" for column in plan.columns)
    return f"UPDATE {plan.table} SET {assignments} WHERE {predicates}"


def _table_exists(cur, table: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table,))
    return cur.fetchone() is not None


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(f"SHOW COLUMNS FROM {table} LIKE %s", (column,))
    return cur.fetchone() is not None


def _validate_schema(cur) -> list[str]:
    issues: list[str] = []
    for plan in TABLE_PLANS:
        if not _table_exists(cur, plan.table):
            issues.append(f"Missing table: {plan.table}")
            continue
        for column in plan.columns:
            if not _column_exists(cur, plan.table, column):
                issues.append(f"Missing column: {plan.table}.{column}")
    return issues


def _get_migration_marker(cur) -> dict | None:
    cur.execute(
        """
        SELECT id, action, details, created_at
        FROM operation_logs
        WHERE action = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        (MIGRATION_ACTION,),
    )
    return cur.fetchone()


def _collect_preview(cur) -> list[dict]:
    rows: list[dict] = []
    for plan in TABLE_PLANS:
        for column in plan.columns:
            cur.execute(_preview_sql(plan.table, column))
            stats = cur.fetchone() or {}
            rows.append(
                {
                    "table": plan.table,
                    "column": column,
                    "non_null_count": int(stats.get("non_null_count") or 0),
                    "min_value": stats.get("min_value"),
                    "max_value": stats.get("max_value"),
                }
            )
    return rows


def _format_preview(preview_rows: Iterable[dict]) -> str:
    lines = []
    for row in preview_rows:
        lines.append(
            "PREVIEW"
            f" table={row['table']}"
            f" column={row['column']}"
            f" non_null={row['non_null_count']}"
            f" min={row['min_value'] or 'NULL'}"
            f" max={row['max_value'] or 'NULL'}"
        )
    return "\n".join(lines)


def _apply_updates(cur) -> list[dict]:
    results: list[dict] = []
    for plan in TABLE_PLANS:
        sql = _update_sql(plan)
        cur.execute(sql)
        results.append(
            {
                "table": plan.table,
                "columns": list(plan.columns),
                "affected_rows": cur.rowcount,
            }
        )
    return results


def main() -> int:
    args = parse_args()
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            issues = _validate_schema(cur)
            if issues:
                for issue in issues:
                    print(f"Error: {issue}", file=sys.stderr)
                return 1

            marker = _get_migration_marker(cur)
            preview_rows = _collect_preview(cur)
            print(_format_preview(preview_rows))

            if marker:
                print(
                    "MARKER"
                    f" id={marker.get('id')}"
                    f" created_at={marker.get('created_at')}"
                    f" details={marker.get('details') or ''}"
                )
                if not args.force:
                    print(
                        "SKIP migration marker already exists. Use --force only if you have verified this has not been applied.",
                        file=sys.stderr,
                    )
                    return 1 if args.apply else 0

            if not args.apply:
                print("DRY_RUN no changes applied. Re-run with --apply to execute.")
                return 0

            results = _apply_updates(cur)
            marker_details = json.dumps(
                {
                    "mode": "apply",
                    "offset": "-08:00",
                    "tables": results,
                },
                ensure_ascii=True,
                separators=(",", ":"),
            )
            cur.execute(
                "INSERT INTO operation_logs (user_id, username, action, details) VALUES (NULL, NULL, %s, %s)",
                (MIGRATION_ACTION, marker_details),
            )
            conn.commit()

            for row in results:
                print(
                    "UPDATED"
                    f" table={row['table']}"
                    f" columns={','.join(row['columns'])}"
                    f" affected_rows={row['affected_rows']}"
                )
            print(f"DONE marker_action={MIGRATION_ACTION}")
            return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())