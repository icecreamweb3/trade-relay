import logging

from trade_relay import database as db


class _Cursor:
    def __init__(self):
        self.executed = []
        self.rowcount = 0
        self._snapshot_count = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "SELECT id, user_id" in sql:
            self._snapshot_count += 1
            self.rowcount = 1
        else:
            self.rowcount = 1

    def fetchone(self):
        return {
            "id": 23,
            "symbol": "BTCUSDC",
            "quantity": 0.005,
            "avg_entry_price": 79941.8,
            "planned_stop_price": 79823.6,
            "initial_risk_usdc": 0.0175 if self._snapshot_count > 1 else None,
        }


def test_small_initial_risk_write_logs_sql_params_stack_and_snapshots(caplog):
    cursor = _Cursor()
    sql = "UPDATE positions SET initial_risk_usdc = %s WHERE id = %s"

    with caplog.at_level(logging.WARNING, logger=db.__name__):
        affected = db._execute_initial_risk_write(
            cursor,
            source="unit_test_source",
            position_id=23,
            candidate_risk=0.02,
            sql=sql,
            params=(0.02, 23),
        )

    assert affected == 1
    assert len(cursor.executed) == 3
    assert "SELECT id, user_id" in cursor.executed[0][0]
    assert cursor.executed[1] == (sql, (0.02, 23))
    assert "SELECT id, user_id" in cursor.executed[2][0]
    messages = "\n".join(caplog.messages)
    assert "[INITIAL_RISK_MONITOR] phase=write_attempt" in messages
    assert "event_time_utc=" in messages
    assert "source=unit_test_source" in messages
    assert "candidate_risk=0.02" in messages
    assert "sql=UPDATE positions SET initial_risk_usdc = %s WHERE id = %s" in messages
    assert "params=(0.02, 23)" in messages
    assert "row_before=" in messages
    assert "caller_stack=" in messages
    assert "[INITIAL_RISK_MONITOR] phase=write_result" in messages
    assert "row_after=" in messages


def test_normal_initial_risk_write_has_no_monitor_queries_or_warning(caplog):
    cursor = _Cursor()
    sql = "UPDATE positions SET initial_risk_usdc = %s WHERE id = %s"

    with caplog.at_level(logging.WARNING, logger=db.__name__):
        affected = db._execute_initial_risk_write(
            cursor,
            source="unit_test_source",
            position_id=23,
            candidate_risk=0.0201,
            sql=sql,
            params=(0.0201, 23),
        )

    assert affected == 1
    assert cursor.executed == [(sql, (0.0201, 23))]
    assert "INITIAL_RISK_MONITOR" not in "\n".join(caplog.messages)


def test_all_position_initial_risk_write_paths_use_monitor(monkeypatch):
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Connection:
        def __init__(self):
            self.commits = 0

        def cursor(self):
            return Cursor()

        def commit(self):
            self.commits += 1

        def close(self):
            return None

    connections = []

    def connection_factory():
        connection = Connection()
        connections.append(connection)
        return connection

    def monitored(_cursor, **kwargs):
        calls.append(kwargs)
        return 1

    monkeypatch.setattr(db, "get_connection", connection_factory)
    monkeypatch.setattr(db, "_execute_initial_risk_write", monitored)
    monkeypatch.setattr(db, "_upsert_position_history_final_from_position_cursor", lambda *_args: None)

    assert db.initialize_position_risk(23, 79823.6, 0.0175) is True
    assert db.update_position_initial_risk(23, 0.018) is True
    assert db.save_position_excursion_metrics(23, {"initial_risk_usdc": 0.019}) is True

    assert [call["source"] for call in calls] == [
        "initialize_position_risk",
        "update_position_initial_risk",
        "save_position_excursion_metrics",
    ]
    assert [call["candidate_risk"] for call in calls] == [0.0175, 0.018, 0.019]
    assert all(call["position_id"] == 23 for call in calls)
    assert all("initial_risk_usdc" in call["sql"] for call in calls)
    assert all(connection.commits == 1 for connection in connections)
