from trade_relay import database as db


def test_normalize_income_history_row_maps_binance_fields():
    row = db._normalize_income_history_row(
        7,
        "alice",
        {
            "symbol": "btcusdc",
            "incomeType": "commission",
            "income": "-0.12086000",
            "asset": "usdc",
            "info": "480952255",
            "tradeId": "480952255",
            "tranId": 96111480952255,
            "time": 1779509423000,
        },
    )

    assert row == {
        "user_id": 7,
        "username": "alice",
        "exchange": "binance",
        "symbol": "BTCUSDC",
        "income_type": "COMMISSION",
        "income": "-0.12086000",
        "asset": "USDC",
        "info_text": "480952255",
        "trade_id": "480952255",
        "tran_id": "96111480952255",
        "income_time": db._coerce_utc_naive_datetime(1779509423000),
    }


def test_get_income_reconciliation_summary_combines_sources(monkeypatch):
    monkeypatch.setattr(
        db,
        "get_income_history_totals",
        lambda user_id: {
            "row_count": 5,
            "total_income": 4.03466379,
            "realized_pnl": 8.452,
            "commission": -1.96906048,
            "funding_fee": -0.01740312,
            "other_income": -2.43087261,
        },
    )
    monkeypatch.setattr(
        db,
        "get_filled_order_totals",
        lambda user_id: {
            "close_count": 13,
            "pnl": 6.4219,
            "commission": 2.15690044,
        },
    )
    monkeypatch.setattr(
        db,
        "get_position_history_trade_totals",
        lambda user_id: {
            "trade_count": 13,
            "pnl": 6.4219,
            "commission": 1.44582564,
        },
    )
    monkeypatch.setattr(db, "get_profile_initial_balance", lambda user_id: 200.00001396)
    monkeypatch.setattr(db, "get_profile_current_balance", lambda user_id: 204.03467775)

    summary = db.get_income_reconciliation_summary(2)

    assert summary["income_row_count"] == 5
    assert summary["balance_net"] == 4.03466379
    assert summary["income_total"] == 4.03466379
    assert summary["income_commission_cost"] == 1.96906048
    assert summary["order_close_count"] == 13
    assert summary["position_trade_count"] == 13
    assert summary["order_net"] == 4.26499956
    assert summary["position_net"] == 4.97607436
    assert summary["income_vs_order_realized_gap"] == 2.0301
    assert summary["income_vs_order_commission_gap"] == -0.18783996
    assert summary["income_vs_order_net_gap"] == -0.23033577
    assert summary["unexplained_gap"] == 0.0