def test_run_once_reschedules_partial_close_candidate(monkeypatch):
    from trade_relay.trading import close_tpsl_retry_worker

    scheduled = []

    candidate = {
        "id": 227,
        "user_id": 5,
        "username": "Will",
        "symbol": "BTCUSDC",
        "side": "SELL",
        "trade_direction": "CLOSE",
        "position_mode": "DUAL",
        "status": "PARTIALLY_FILLED",
        "close_tpsl_sync_attempts": 1,
    }

    monkeypatch.setattr(
        close_tpsl_retry_worker.db_module,
        "get_due_order_close_tpsl_retry_candidates",
        lambda limit=100: [candidate],
    )
    monkeypatch.setattr(
        close_tpsl_retry_worker.db_module,
        "schedule_order_close_tpsl_retry",
        lambda order_id, **kwargs: scheduled.append((order_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        close_tpsl_retry_worker.db_module,
        "clear_order_close_tpsl_sync_state",
        lambda order_id: (_ for _ in ()).throw(AssertionError("should not clear while partial fill is still inflight")),
    )

    close_tpsl_retry_worker._run_once()

    assert scheduled == [(
        227,
        {
            "delay_seconds": close_tpsl_retry_worker.RETRY_BACKOFF_SECONDS[1],
            "error_message": "close_fill_still_inflight",
        },
    )]


def test_run_once_refreshes_remaining_close_tpsl_and_clears_state(monkeypatch):
    from trade_relay.trading import close_tpsl_retry_worker

    refreshed = []
    cleared = []

    candidate = {
        "id": 228,
        "user_id": 5,
        "username": "Will",
        "symbol": "BTCUSDC",
        "side": "SELL",
        "trade_direction": "CLOSE",
        "position_mode": "DUAL",
        "status": "FILLED",
        "close_tpsl_sync_attempts": 0,
    }

    monkeypatch.setattr(
        close_tpsl_retry_worker.db_module,
        "get_due_order_close_tpsl_retry_candidates",
        lambda limit=100: [candidate],
    )
    monkeypatch.setattr(
        close_tpsl_retry_worker.db_module,
        "get_position",
        lambda user_id, symbol, position_side: {
            "id": 509,
            "quantity": 0.02,
            "avg_entry_price": 77732.83,
            "position_mode": "DUAL",
        },
    )
    monkeypatch.setattr(
        close_tpsl_retry_worker,
        "sync_close_tpsl_quantity",
        lambda **kwargs: refreshed.append(kwargs) or [],
    )
    monkeypatch.setattr(
        close_tpsl_retry_worker.db_module,
        "clear_order_close_tpsl_sync_state",
        lambda order_id: cleared.append(order_id) or True,
    )
    monkeypatch.setattr(
        close_tpsl_retry_worker.db_module,
        "schedule_order_close_tpsl_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not reschedule on successful refresh")),
    )

    close_tpsl_retry_worker._run_once()

    assert refreshed == [{
        "username": "Will",
        "user_id": 5,
        "symbol": "BTCUSDC",
        "position_side": "LONG",
        "quantity": 0.02,
        "entry_price": 77732.83,
    }]
    assert cleared == [228]