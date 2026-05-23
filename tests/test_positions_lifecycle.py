import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trade_relay import database as db


class FakePositionsCursor:
    def __init__(self, state):
        self.state = state
        self.result = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        normalized_sql = " ".join(sql.split())
        if normalized_sql.startswith("INSERT INTO positions"):
            (
                user_id,
                username,
                exchange,
                symbol,
                position_side,
                position_mode,
                status,
                open_position_slot,
                quantity,
                avg_entry_price,
                liquidation_price,
                unrealized_pnl,
                realized_pnl,
                leverage,
                margin_type,
            ) = params
            existing = next(
                (
                    row for row in self.state["rows"]
                    if row["user_id"] == user_id
                    and row["exchange"] == exchange
                    and row["symbol"] == symbol
                    and row["position_side"] == position_side
                    and row.get("open_position_slot") == open_position_slot
                    and open_position_slot is not None
                ),
                None,
            )
            if existing:
                existing.update(
                    {
                        "username": username,
                        "position_mode": position_mode,
                        "status": status,
                        "open_position_slot": open_position_slot,
                        "quantity": quantity,
                        "avg_entry_price": avg_entry_price,
                        "liquidation_price": liquidation_price,
                        "unrealized_pnl": unrealized_pnl,
                        "realized_pnl": realized_pnl,
                        "leverage": leverage,
                        "margin_type": margin_type,
                    }
                )
                self.rowcount = 2
            else:
                self.state["rows"].append(
                    {
                        "id": self.state["next_id"],
                        "user_id": user_id,
                        "username": username,
                        "exchange": exchange,
                        "symbol": symbol,
                        "position_side": position_side,
                        "position_mode": position_mode,
                        "status": status,
                        "open_position_slot": open_position_slot,
                        "quantity": quantity,
                        "avg_entry_price": avg_entry_price,
                        "liquidation_price": liquidation_price,
                        "unrealized_pnl": unrealized_pnl,
                        "realized_pnl": realized_pnl,
                        "leverage": leverage,
                        "margin_type": margin_type,
                    }
                )
                self.state["next_id"] += 1
                self.rowcount = 1
            self.result = None
            return

        if normalized_sql.startswith("UPDATE positions SET status = 'CLOSE'"):
            user_id, exchange, symbol, position_side = params
            updated = 0
            for row in self.state["rows"]:
                if (
                    row["user_id"] == user_id
                    and row["exchange"] == exchange
                    and row["symbol"] == symbol
                    and row["position_side"] == position_side
                    and str(row.get("status") or "OPEN").upper() == "OPEN"
                ):
                    row["status"] = "CLOSE"
                    row["open_position_slot"] = None
                    row["quantity"] = 0
                    row["liquidation_price"] = None
                    row["unrealized_pnl"] = 0
                    updated += 1
            self.rowcount = updated
            self.result = None
            return

        if normalized_sql.startswith("SELECT * FROM positions WHERE user_id = %s AND exchange = %s AND symbol = %s AND position_side = %s"):
            user_id, exchange, symbol, position_side, *rest = params
            rows = [
                row for row in self.state["rows"]
                if row["user_id"] == user_id
                and row["exchange"] == exchange
                and row["symbol"] == symbol
                and row["position_side"] == position_side
            ]
            if rest:
                status = str(rest[0] or "").upper()
                rows = [row for row in rows if str(row.get("status") or "OPEN").upper() == status]
            rows.sort(key=lambda row: row["id"], reverse=True)
            self.result = rows[0] if rows else None
            self.rowcount = 1 if self.result else 0
            return

        raise AssertionError(f"Unexpected SQL: {normalized_sql}")

    def fetchone(self):
        return self.result

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakePositionsConnection:
    def __init__(self, state):
        self.state = state

    def cursor(self):
        return FakePositionsCursor(self.state)

    def commit(self):
        return None

    def close(self):
        return None


def test_position_reopen_gets_new_id_while_add_keeps_existing_id(monkeypatch):
    state = {"rows": [], "next_id": 1}

    monkeypatch.setattr(db, "get_connection", lambda: FakePositionsConnection(state))

    db.upsert_position(
        user_id=7,
        username="alice",
        symbol="BTCUSDC",
        quantity=0.01,
        avg_entry_price=70000.0,
        position_side="LONG",
        position_mode="DUAL",
        status="OPEN",
    )
    first_open = db.get_position(7, "BTCUSDC", "LONG")

    db.upsert_position(
        user_id=7,
        username="alice",
        symbol="BTCUSDC",
        quantity=0.02,
        avg_entry_price=70500.0,
        position_side="LONG",
        position_mode="DUAL",
        status="OPEN",
    )
    increased_open = db.get_position(7, "BTCUSDC", "LONG")

    db.close_position(7, "BTCUSDC", "LONG")
    assert db.get_position(7, "BTCUSDC", "LONG") is None

    db.upsert_position(
        user_id=7,
        username="alice",
        symbol="BTCUSDC",
        quantity=0.01,
        avg_entry_price=71000.0,
        position_side="LONG",
        position_mode="DUAL",
        status="OPEN",
    )
    reopened = db.get_position(7, "BTCUSDC", "LONG")
    closed_rows = [row for row in state["rows"] if row["status"] == "CLOSE"]

    assert first_open["id"] == 1
    assert increased_open["id"] == first_open["id"]
    assert increased_open["avg_entry_price"] == 70500.0
    assert reopened["id"] == 2
    assert reopened["avg_entry_price"] == 71000.0
    assert len(closed_rows) == 1
    assert closed_rows[0]["id"] == 1
    assert closed_rows[0]["open_position_slot"] is None