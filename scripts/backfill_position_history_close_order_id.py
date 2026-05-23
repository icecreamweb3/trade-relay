#!/usr/bin/env python3
"""Backfill position_history.close_order_id from actual filled close orders.

This one-off task links historical position_history rows to the actual filled close
order row in orders.id. Trigger-only conditional rows are naturally excluded because
the backfill only considers FILLED CLOSE orders.

Matching is intentionally conservative:
1. same user, symbol, and close side
2. exact/safe match on filled quantity and close price
3. prefer matching position_id when available
4. use realized_pnl / commission / timestamp proximity as tie-breakers
5. never reuse an order id that is already linked to another position_history row

Ambiguous rows are skipped instead of force-linked.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, NamedTuple, Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from trade_relay.env_loader import load_env


load_env(root=ROOT, override=False)


from trade_relay import database as db


QTY_EPSILON = 1e-9
PRICE_EPSILON = 0.01
PNL_EPSILON = 1e-6
COMMISSION_EPSILON = 1e-6
MIN_CONFIDENCE_SCORE = 80
MIN_SCORE_GAP = 15


class MatchDecision(NamedTuple):
    order_id: int | None
    reason: str
    score: int = 0


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date value: {value!r}. Expected YYYY-MM-DD.") from exc


def _safe_float(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _history_expected_order_side(history_row: dict) -> str | None:
    side = str(history_row.get("side") or "").upper()
    if side == "LONG":
        return "SELL"
    if side == "SHORT":
        return "BUY"
    return None


def _time_distance_seconds(history_row: dict, order_row: dict) -> float:
    history_created_at = _safe_datetime(history_row.get("created_at"))
    if history_created_at is None:
        return math.inf

    candidates = [
        _safe_datetime(order_row.get("filled_at")),
        _safe_datetime(order_row.get("updated_at")),
        _safe_datetime(order_row.get("created_at")),
    ]
    distances = [
        abs((history_created_at - candidate).total_seconds())
        for candidate in candidates
        if candidate is not None
    ]
    return min(distances) if distances else math.inf


def _score_order_match(history_row: dict, order_row: dict) -> int | None:
    expected_side = _history_expected_order_side(history_row)
    if not expected_side or str(order_row.get("side") or "").upper() != expected_side:
        return None

    if str(order_row.get("trade_direction") or "").upper() != "CLOSE":
        return None
    if str(order_row.get("status") or "").upper() != "FILLED":
        return None
    if int(order_row.get("user_id") or 0) != int(history_row.get("user_id") or 0):
        return None
    if str(order_row.get("symbol") or "").upper() != str(history_row.get("symbol") or "").upper():
        return None

    history_qty = abs(_safe_float(history_row.get("quantity")))
    order_qty = abs(_safe_float(order_row.get("filled_qty")))
    if abs(history_qty - order_qty) > QTY_EPSILON:
        return None

    history_close_price = _safe_float(history_row.get("close_price"))
    order_close_price = _safe_float(order_row.get("avg_price"))
    if history_close_price <= 0 or order_close_price <= 0:
        return None
    if abs(history_close_price - order_close_price) > PRICE_EPSILON:
        return None

    score = 100

    history_position_id = int(history_row.get("position_id") or 0)
    order_position_id = int(order_row.get("position_id") or 0)
    if history_position_id > 0 and order_position_id > 0:
        if history_position_id != order_position_id:
            return None
        score += 60

    history_realized_pnl = _safe_float(history_row.get("realized_pnl"))
    order_realized_pnl = _safe_float(order_row.get("realized_pnl"))
    if abs(history_realized_pnl - order_realized_pnl) <= PNL_EPSILON:
        score += 20

    history_commission = _safe_float(history_row.get("commission"))
    order_commission = _safe_float(order_row.get("commission"))
    if abs(history_commission - order_commission) <= COMMISSION_EPSILON:
        score += 10

    time_distance = _time_distance_seconds(history_row, order_row)
    if time_distance <= 5:
        score += 25
    elif time_distance <= 60:
        score += 20
    elif time_distance <= 300:
        score += 10
    elif time_distance <= 1800:
        score += 5

    return score


def find_best_order_match(history_row: dict, candidate_orders: Iterable[dict], used_order_ids: set[int]) -> MatchDecision:
    scored_candidates: list[tuple[int, int]] = []
    for order_row in candidate_orders:
        order_id = int(order_row.get("id") or 0)
        if order_id <= 0 or order_id in used_order_ids:
            continue
        score = _score_order_match(history_row, order_row)
        if score is not None:
            scored_candidates.append((score, order_id))

    if not scored_candidates:
        return MatchDecision(order_id=None, reason="no_candidate")

    scored_candidates.sort(reverse=True)
    best_score, best_order_id = scored_candidates[0]
    if best_score < MIN_CONFIDENCE_SCORE:
        return MatchDecision(order_id=None, reason="low_confidence", score=best_score)

    if len(scored_candidates) > 1 and (best_score - scored_candidates[1][0]) < MIN_SCORE_GAP:
        return MatchDecision(order_id=None, reason="ambiguous", score=best_score)

    return MatchDecision(order_id=best_order_id, reason="matched", score=best_score)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill position_history.close_order_id from actual filled close orders.")
    parser.add_argument("--username", help="Only backfill rows for a single username.")
    parser.add_argument("--start-date", type=_parse_date, help="Inclusive UTC date filter for position_history.created_at.")
    parser.add_argument("--end-date", type=_parse_date, help="Inclusive UTC date filter for position_history.created_at.")
    parser.add_argument("--dry-run", action="store_true", help="Print summary without committing updates.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.start_date and args.end_date and args.start_date > args.end_date:
        print("Error: start-date cannot be later than end-date.", file=sys.stderr)
        return 1

    db.init_db()

    normalized_username = str(args.username or "").strip() or None
    user_id: Optional[int] = None
    if normalized_username:
        user_row = db.get_user_by_username(normalized_username)
        if not user_row:
            print(f"Error: username={normalized_username!r} was not found.", file=sys.stderr)
            return 1
        user_id = int(user_row["id"])

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            history_sql = [
                "SELECT id, user_id, username, symbol, side, quantity, close_price, realized_pnl, commission, position_id, created_at, updated_at",
                "FROM position_history",
                "WHERE close_order_id IS NULL",
            ]
            history_params: list[object] = []
            if user_id is not None:
                history_sql.append("AND user_id = %s")
                history_params.append(user_id)
            if args.start_date is not None:
                history_sql.append("AND created_at >= %s")
                history_params.append(datetime.combine(args.start_date, time.min))
            if args.end_date is not None:
                history_sql.append("AND created_at < %s")
                history_params.append(datetime.combine(args.end_date + timedelta(days=1), time.min))
            history_sql.append("ORDER BY user_id ASC, created_at ASC, id ASC")

            cur.execute("\n".join(history_sql), history_params)
            history_rows = cur.fetchall() or []

            orders_sql = [
                "SELECT id, user_id, username, symbol, side, trade_direction, status, order_type, order_category,",
                "       filled_qty, avg_price, realized_pnl, commission, position_id, created_at, updated_at, filled_at",
                "FROM orders",
                "WHERE UPPER(COALESCE(trade_direction, '')) = 'CLOSE'",
                "  AND UPPER(COALESCE(status, '')) = 'FILLED'",
                "  AND filled_qty IS NOT NULL",
                "  AND avg_price IS NOT NULL",
            ]
            order_params: list[object] = []
            if user_id is not None:
                orders_sql.append("AND user_id = %s")
                order_params.append(user_id)
            orders_sql.append("ORDER BY COALESCE(filled_at, updated_at, created_at) ASC, id ASC")
            cur.execute("\n".join(orders_sql), order_params)
            order_rows = cur.fetchall() or []

            cur.execute("SELECT close_order_id FROM position_history WHERE close_order_id IS NOT NULL")
            used_order_ids = {
                int(row["close_order_id"])
                for row in (cur.fetchall() or [])
                if row.get("close_order_id") is not None
            }

            orders_by_user: dict[int, list[dict]] = {}
            for order_row in order_rows:
                orders_by_user.setdefault(int(order_row.get("user_id") or 0), []).append(order_row)

            matched = 0
            ambiguous = 0
            low_confidence = 0
            no_candidate = 0
            updates: list[tuple[int, int]] = []

            for history_row in history_rows:
                candidates = orders_by_user.get(int(history_row.get("user_id") or 0), [])
                decision = find_best_order_match(history_row, candidates, used_order_ids)
                if decision.order_id is None:
                    if decision.reason == "ambiguous":
                        ambiguous += 1
                    elif decision.reason == "low_confidence":
                        low_confidence += 1
                    else:
                        no_candidate += 1
                    continue

                updates.append((decision.order_id, int(history_row["id"])))
                used_order_ids.add(decision.order_id)
                matched += 1

            for close_order_id, history_id in updates:
                cur.execute(
                    "UPDATE position_history SET close_order_id = %s WHERE id = %s AND close_order_id IS NULL",
                    (close_order_id, history_id),
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
            f" scanned={len(history_rows)}"
            f" matched={matched}"
            f" ambiguous={ambiguous}"
            f" low_confidence={low_confidence}"
            f" no_candidate={no_candidate}"
            f" committed={'no' if args.dry_run else 'yes'}"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())