from datetime import datetime, timezone

from backend.routers import orders as orders_router
from trade_relay import database as db


class StubConnection:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.queries = []

    def cursor(self):
        connection = self

        class StubCursor:
            def execute(self, sql, params=None):
                connection.queries.append((sql, params))

            def fetchall(self):
                return connection.rows

            def fetchone(self):
                return connection.rows[0] if connection.rows else None

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return StubCursor()

    def close(self):
        return None


def test_get_distinct_order_symbols_normalizes_and_sorts_in_database(monkeypatch):
    connection = StubConnection([
        {"symbol": "BTCUSDC"},
        {"symbol": "ETHUSDC"},
    ])
    monkeypatch.setattr(db, "get_connection", lambda: connection)

    assert db.get_distinct_order_symbols() == ["BTCUSDC", "ETHUSDC"]
    sql, params = connection.queries[0]
    assert "SELECT DISTINCT UPPER(TRIM(symbol)) AS symbol" in sql
    assert "ORDER BY symbol" in sql
    assert params is None


def test_query_orders_filters_by_exact_normalized_symbol(monkeypatch):
    connection = StubConnection()
    monkeypatch.setattr(db, "get_connection", lambda: connection)

    assert db.query_orders(symbol=" ethusdc ") == []
    sql, params = connection.queries[0]
    assert "AND UPPER(symbol) = %s" in sql
    assert params == ["ETHUSDC", 200]


def test_query_orders_sorts_created_time_before_filled_time(monkeypatch):
    connection = StubConnection()
    monkeypatch.setattr(db, "get_connection", lambda: connection)

    assert db.query_orders(sort_by_created_at=True) == []
    sql, params = connection.queries[0]
    assert "ORDER BY created_at DESC, filled_at DESC, id DESC LIMIT %s" in sql
    assert params == [200]


def test_count_positions_opened_in_range_counts_canonical_position_cycles(monkeypatch):
    connection = StubConnection([{"position_count": 2}])
    monkeypatch.setattr(db, "get_connection", lambda: connection)

    start_time = "2026-09-25 00:00:00"
    end_time = "2026-09-26 00:00:00"
    assert db.count_positions_opened_in_range(
        username="Will",
        start_time=start_time,
        end_time=end_time,
    ) == 2

    sql, params = connection.queries[0]
    normalized_sql = " ".join(sql.split())
    assert "FROM positions" in normalized_sql
    assert "opened_at >= %s" in normalized_sql
    assert "opened_at < %s" in normalized_sql
    assert "FROM orders" not in normalized_sql
    assert params == ("Will", start_time, end_time)


def test_daily_position_count_uses_utc_day_boundaries(monkeypatch):
    captured = {}

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 25, 23, 59, 59, tzinfo=timezone.utc)

    def fake_count(**kwargs):
        captured.update(kwargs)
        return 3

    monkeypatch.setattr(orders_router, "datetime", FixedDateTime)
    monkeypatch.setattr(orders_router.db_module, "count_positions_opened_in_range", fake_count)

    result = orders_router.get_daily_position_count({"username": "Will"})

    assert result.count == 3
    assert captured == {
        "username": "Will",
        "start_time": datetime(2026, 9, 25, 0, 0),
        "end_time": datetime(2026, 9, 26, 0, 0),
    }
