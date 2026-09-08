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
