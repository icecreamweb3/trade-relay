from datetime import datetime
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from task import recalculate_historical_excursion_metrics as task


def test_parse_start_time_normalizes_timezone_to_utc_naive():
    assert task._parse_start_time("2026-09-01T08:00:00+08:00") == datetime(2026, 9, 1)
    assert task._parse_start_time(None) is None


def test_classify_history_rows_keeps_largest_close_snapshot():
    rows = [
        {"id": 10, "close_order_id": 77, "quantity": 0.001, "realized_pnl": 0.4},
        {"id": 11, "close_order_id": 77, "quantity": 0.02, "realized_pnl": 8.7},
        {"id": 12, "close_order_id": 78, "quantity": 0.01, "realized_pnl": 2.0},
    ]

    keepers, shadows = task._classify_history_rows(rows)

    assert sorted(row["id"] for row in keepers) == [11, 12]
    assert [row["id"] for row in shadows] == [10]
    assert task._sum_realized(keepers) == 10.7


def test_fetch_candidates_defaults_to_full_scope(monkeypatch):
    captured = {}

    class Cursor:
        def execute(self, sql, params):
            captured["sql"] = " ".join(sql.split())
            captured["params"] = params

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr(task.db, "get_connection", lambda: Connection())

    assert task._fetch_candidates(None, None) == []
    assert "p.user_id = %s" not in captured["sql"]
    assert "COALESCE(f.close_time, f.updated_at, f.created_at) >= %s" not in captured["sql"]
    assert captured["params"] == [task.ALGORITHM_VERSION]


def test_fetch_candidates_applies_user_and_start_filters(monkeypatch):
    captured = {}

    class Cursor:
        def execute(self, sql, params):
            captured["sql"] = " ".join(sql.split())
            captured["params"] = params

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr(task.db, "get_connection", lambda: Connection())
    start = datetime(2026, 9, 1)

    task._fetch_candidates(5, start)

    assert "p.user_id = %s" in captured["sql"]
    assert "COALESCE(f.close_time, f.updated_at, f.created_at) >= %s" in captured["sql"]
    assert captured["params"] == [task.ALGORITHM_VERSION, 5, start]


def test_recalculate_missing_metrics_returns_empty_summary(monkeypatch):
    monkeypatch.setattr(task, "_fetch_candidates", lambda user_id, start_time: [])

    assert task.recalculate_missing_metrics(user_id=5) == {
        "scanned": 0,
        "calculated": 0,
        "queued": 0,
        "failed": 0,
        "duplicate_history_rows": 0,
    }
