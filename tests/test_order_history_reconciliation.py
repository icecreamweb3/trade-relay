from datetime import datetime

from trade_relay.trading import order_history_reconciliation as reconciliation


def test_reconcile_inserts_missing_and_updates_existing_orders(monkeypatch):
    orders = {
        "102": {"id": 2, "exchange_order_id": "102", "trade_direction": "CLOSE", "status": "NEW"},
    }

    class FakeClient:
        def get_account_trades_range(self, start_ms, end_ms):
            return [
                {"id": 1, "orderId": 101, "symbol": "BTCUSDC", "qty": "0.02", "price": "79057.9", "commission": "0.3", "commissionAsset": "USDC", "realizedPnl": "0", "time": 1_000},
                {"id": 2, "orderId": 102, "symbol": "BTCUSDC", "qty": "0.02", "price": "79665", "commission": "0.31", "commissionAsset": "USDC", "realizedPnl": "12.5", "time": 2_000},
            ]

        def get_all_orders_range(self, symbol, start_ms, end_ms):
            return [
                {"orderId": 101, "symbol": symbol, "side": "SELL", "positionSide": "SHORT", "type": "LIMIT", "origQty": "0.02", "executedQty": "0.02", "avgPrice": "79057.9", "price": "79057.9", "status": "FILLED", "time": 900, "updateTime": 1_000},
                {"orderId": 102, "symbol": symbol, "side": "BUY", "positionSide": "SHORT", "type": "MARKET", "origQty": "0.02", "executedQty": "0.02", "avgPrice": "79665", "price": "0", "status": "FILLED", "time": 1_900, "updateTime": 2_000},
            ]

    monkeypatch.setattr(reconciliation.db, "get_order_symbols_for_user_range", lambda *args: [])
    monkeypatch.setattr(reconciliation.db, "get_order_by_exchange_id", lambda username, exchange_id: orders.get(exchange_id))

    def adopt(username, exchange_id, event):
        if exchange_id not in orders:
            orders[exchange_id] = {
                "id": 1,
                "exchange_order_id": exchange_id,
                "trade_direction": "OPEN",
                "status": event["X"],
            }
        return orders[exchange_id]["id"]

    monkeypatch.setattr(reconciliation.db, "adopt_external_order", adopt)
    monkeypatch.setattr(reconciliation.db, "update_order_status", lambda order_id, status, **kwargs: order_id == 2)
    monkeypatch.setattr(reconciliation.db, "update_order_metadata", lambda order_id, **kwargs: False)
    monkeypatch.setattr(reconciliation.db, "get_order_by_id", lambda order_id: next(row for row in orders.values() if row["id"] == order_id))
    monkeypatch.setattr(reconciliation, "sync_position_history_from_filled_close_order", lambda row: 0)

    result = reconciliation.reconcile_order_history(
        username="Will",
        client=FakeClient(),
        start_time=datetime(2026, 8, 28, 22, 0),
        end_time=datetime(2026, 8, 28, 23, 0),
    )

    assert result["symbols"] == ["BTCUSDC"]
    assert result["scanned_orders"] == 2
    assert result["scanned_trades"] == 2
    assert result["inserted"] == 1
    assert result["updated"] == 1
    assert result["failed"] == 0
