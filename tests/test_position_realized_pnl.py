import re

from trade_relay import database as db


class RecordingCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))


def test_rebuild_position_realized_pnl_uses_linked_history(monkeypatch):
    cursor = RecordingCursor()
    monkeypatch.setattr(db, "_table_exists", lambda _cur, _table: True)

    db._rebuild_positions_realized_pnl(cursor)

    sql, params = cursor.calls[-1]
    assert params is None
    assert "SUM(COALESCE(realized_pnl, 0))" in sql
    assert "ph.position_id = p.id" in sql
    assert "WHEN UPPER(COALESCE(p.status, 'OPEN')) = 'OPEN' THEN 0" in sql
    assert "ELSE NULL" in sql
    assert "p.updated_at = p.updated_at" in sql


def test_upsert_position_does_not_overwrite_cycle_realized_pnl(monkeypatch):
    cursor = RecordingCursor()

    class Connection:
        def cursor(self):
            class Context:
                def __enter__(self_inner):
                    return cursor

                def __exit__(self_inner, *_args):
                    return False

            return Context()

        def commit(self):
            pass

        def close(self):
            pass

    cursor.rowcount = 1
    monkeypatch.setattr(db, "get_connection", Connection)

    db.upsert_position(
        user_id=7,
        username="alice",
        symbol="BTCUSDC",
        quantity=0.01,
        realized_pnl=-999,
        position_side="LONG",
    )

    sql, _params = cursor.calls[-1]
    update_clause = sql.split("ON DUPLICATE KEY UPDATE", 1)[1]
    assert re.search(r"(?<!un)realized_pnl\s*=", update_clause) is None
