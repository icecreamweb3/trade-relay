from trade_relay.trading import trade_details_retry_worker


def test_run_once_retries_due_filled_order_and_clears_state_on_success(monkeypatch):
    processed = []
    cleared = []

    candidate = {
        "id": 215,
        "username": "Will",
        "trade_direction": "CLOSE",
        "trade_details_sync_attempts": 0,
    }
    rows_by_id = {
        215: {
            **candidate,
            "commission": None,
            "commission_asset": None,
            "realized_pnl": None,
        }
    }

    class StubClient:
        pass

    monkeypatch.setattr(
        trade_details_retry_worker.db_module,
        "get_due_order_trade_details_retry_candidates",
        lambda limit=100: [candidate],
    )
    monkeypatch.setattr(trade_details_retry_worker, "_build_client", lambda username: StubClient())
    monkeypatch.setattr(
        trade_details_retry_worker.db_module,
        "get_order_by_id",
        lambda order_id: rows_by_id[order_id],
    )

    def fake_sync_filled_order_trade_details(*, username, client, order_row):
        processed.append((username, order_row["id"]))
        rows_by_id[order_row["id"]] = {
            **rows_by_id[order_row["id"]],
            "commission": 1.11,
            "commission_asset": "USDC",
            "realized_pnl": -3.87,
        }

    monkeypatch.setattr(
        trade_details_retry_worker,
        "sync_filled_order_trade_details",
        fake_sync_filled_order_trade_details,
    )
    monkeypatch.setattr(
        trade_details_retry_worker.db_module,
        "clear_order_trade_details_sync_state",
        lambda order_id: cleared.append(order_id) or True,
    )
    monkeypatch.setattr(
        trade_details_retry_worker.db_module,
        "schedule_order_trade_details_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not schedule retry on success")),
    )

    trade_details_retry_worker._run_once()

    assert processed == [("Will", 215)]
    assert cleared == [215]


def test_run_once_schedules_retry_when_trade_details_still_missing(monkeypatch):
    scheduled = []

    candidate = {
        "id": 216,
        "username": "Will",
        "trade_direction": "CLOSE",
        "trade_details_sync_attempts": 1,
    }
    rows_by_id = {
        216: {
            **candidate,
            "commission": None,
            "commission_asset": None,
            "realized_pnl": None,
        }
    }

    class StubClient:
        pass

    monkeypatch.setattr(
        trade_details_retry_worker.db_module,
        "get_due_order_trade_details_retry_candidates",
        lambda limit=100: [candidate],
    )
    monkeypatch.setattr(trade_details_retry_worker, "_build_client", lambda username: StubClient())
    monkeypatch.setattr(
        trade_details_retry_worker.db_module,
        "get_order_by_id",
        lambda order_id: rows_by_id[order_id],
    )
    monkeypatch.setattr(
        trade_details_retry_worker,
        "sync_filled_order_trade_details",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        trade_details_retry_worker.db_module,
        "clear_order_trade_details_sync_state",
        lambda order_id: (_ for _ in ()).throw(AssertionError("should not clear state while fields are missing")),
    )
    monkeypatch.setattr(
        trade_details_retry_worker.db_module,
        "schedule_order_trade_details_retry",
        lambda order_id, **kwargs: scheduled.append((order_id, kwargs)) or True,
    )

    trade_details_retry_worker._run_once()

    assert scheduled == [(
        216,
        {
            "delay_seconds": trade_details_retry_worker.RETRY_BACKOFF_SECONDS[1],
            "error_message": "trade_fills_not_ready",
        },
    )]


def test_run_once_processes_open_order_when_filled_qty_is_incomplete(monkeypatch):
    processed = []
    cleared = []

    candidate = {
        "id": 241,
        "username": "Will",
        "trade_direction": "OPEN",
        "trade_details_sync_attempts": 0,
        "quantity": 0.025,
        "trade_details_sync_next_retry_at": None,
    }
    rows_by_id = {
        241: {
            **candidate,
            "commission": 0.0,
            "commission_asset": "USDC",
            "realized_pnl": None,
            "filled_qty": 0.001,
        }
    }

    class StubClient:
        pass

    monkeypatch.setattr(
        trade_details_retry_worker.db_module,
        "get_due_order_trade_details_retry_candidates",
        lambda limit=100: [candidate],
    )
    monkeypatch.setattr(trade_details_retry_worker, "_build_client", lambda username: StubClient())
    monkeypatch.setattr(
        trade_details_retry_worker.db_module,
        "get_order_by_id",
        lambda order_id: rows_by_id[order_id],
    )

    def fake_sync_filled_order_trade_details(*, username, client, order_row):
        processed.append((username, order_row["id"]))
        rows_by_id[order_row["id"]] = {
            **rows_by_id[order_row["id"]],
            "filled_qty": 0.025,
        }

    monkeypatch.setattr(
        trade_details_retry_worker,
        "sync_filled_order_trade_details",
        fake_sync_filled_order_trade_details,
    )
    monkeypatch.setattr(
        trade_details_retry_worker.db_module,
        "clear_order_trade_details_sync_state",
        lambda order_id: cleared.append(order_id) or True,
    )
    monkeypatch.setattr(
        trade_details_retry_worker.db_module,
        "schedule_order_trade_details_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not schedule retry on successful quantity reconciliation")),
    )

    trade_details_retry_worker._run_once()

    assert processed == [("Will", 241)]
    assert cleared == [241]