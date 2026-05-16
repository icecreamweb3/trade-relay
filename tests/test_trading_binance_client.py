import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trade_relay.trading import binance_client as trading_binance_client


def test_place_order_surfaces_stop_market_error(monkeypatch):
    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def set_leverage(self, symbol, leverage):
            return None

        def place_stop_loss_order(self, symbol, side, stop_price, quantity, position_side):
            return {
                "error": True,
                "error_message": "APIError(code=-2021): Order would immediately trigger.",
            }

    monkeypatch.setattr(trading_binance_client, "FuturesBinanceClient", StubClient)

    result = asyncio.run(
        trading_binance_client.place_order(
            api_key="key",
            api_secret="secret",
            symbol="BTCUSDC",
            side="BUY",
            order_type="STOP_MARKET",
            quantity=0.002,
            stop_price=80709.8,
            leverage=20,
            testnet=False,
            position_direction="OPEN",
        )
    )

    assert result.success is False
    assert result.error == "APIError(code=-2021): Order would immediately trigger."


def test_place_order_accepts_algo_id_for_stop_market(monkeypatch):
    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def set_leverage(self, symbol, leverage):
            return None

        def place_stop_loss_order(self, symbol, side, stop_price, quantity, position_side):
            return {
                "algoId": 987654321,
                "clientAlgoId": "client-algo-123",
                "algoStatus": "NEW",
            }

    monkeypatch.setattr(trading_binance_client, "FuturesBinanceClient", StubClient)

    result = asyncio.run(
        trading_binance_client.place_order(
            api_key="key",
            api_secret="secret",
            symbol="BTCUSDC",
            side="BUY",
            order_type="STOP_MARKET",
            quantity=0.002,
            stop_price=80709.8,
            leverage=20,
            testnet=False,
            position_direction="OPEN",
        )
    )

    assert result.success is True
    assert result.order_id == "987654321"
    assert result.client_order_id == "client-algo-123"
    assert result.status == "NEW"


def test_submit_order_persists_stop_price_and_client_order_id(monkeypatch):
    from trade_relay.auth.manager import Session
    from trade_relay.trading import order_manager

    captured: dict = {}

    async def fake_place_order(**kwargs):
        return trading_binance_client.BinanceOrderResult(
            success=True,
            order_id="987654321",
            client_order_id="client-algo-123",
            status="NEW",
        )

    def fake_create_order(**kwargs):
        captured.update(kwargs)
        return 42

    monkeypatch.setattr(order_manager.cfg, "is_mock_mode", lambda username: False)
    monkeypatch.setattr(order_manager.cfg, "get_api_key", lambda username: "key")
    monkeypatch.setattr(order_manager.cfg, "get_api_secret", lambda username: "secret")
    monkeypatch.setattr(order_manager.cfg, "is_testnet", lambda username: False)
    monkeypatch.setattr(order_manager, "place_order", fake_place_order)
    monkeypatch.setattr(order_manager.db, "create_order", fake_create_order)
    monkeypatch.setattr(order_manager.db, "log_operation", lambda *args, **kwargs: None)
    monkeypatch.setattr(order_manager.db, "get_position", lambda *args, **kwargs: None)
    monkeypatch.setattr(order_manager, "ensure_user_order_status_stream", lambda *args, **kwargs: None)
    monkeypatch.setattr(order_manager, "sync_order_status_once", lambda *args, **kwargs: None)

    result = asyncio.run(
        order_manager.submit_order(
            Session(1, "Will", "user"),
            "BTCUSDC",
            "SELL",
            "STOP_MARKET",
            0.002,
            None,
            80683.7,
            None,
            None,
            20,
            "OPEN",
        )
    )

    assert result.success is True
    assert captured["stop_price"] == 80683.7
    assert captured["client_order_id"] == "client-algo-123"
    assert captured["binance_order_id"] == "987654321"


def test_submit_order_persists_requested_tp_sl_prices(monkeypatch):
    from trade_relay.auth.manager import Session
    from trade_relay.trading import order_manager

    captured: dict = {}

    async def fake_place_order(**kwargs):
        return trading_binance_client.BinanceOrderResult(
            success=True,
            order_id="12345",
            client_order_id="client-12345",
            status="NEW",
        )

    def fake_create_order(**kwargs):
        captured.update(kwargs)
        return 43

    monkeypatch.setattr(order_manager.cfg, "is_mock_mode", lambda username: False)
    monkeypatch.setattr(order_manager.cfg, "get_api_key", lambda username: "key")
    monkeypatch.setattr(order_manager.cfg, "get_api_secret", lambda username: "secret")
    monkeypatch.setattr(order_manager.cfg, "is_testnet", lambda username: False)
    monkeypatch.setattr(order_manager, "place_order", fake_place_order)
    monkeypatch.setattr(order_manager.db, "create_order", fake_create_order)
    monkeypatch.setattr(order_manager.db, "log_operation", lambda *args, **kwargs: None)
    monkeypatch.setattr(order_manager.db, "get_position", lambda *args, **kwargs: None)
    monkeypatch.setattr(order_manager, "ensure_user_order_status_stream", lambda *args, **kwargs: None)
    monkeypatch.setattr(order_manager, "sync_order_status_once", lambda *args, **kwargs: None)

    result = asyncio.run(
        order_manager.submit_order(
            Session(1, "Will", "user"),
            "BTCUSDC",
            "BUY",
            "LIMIT",
            0.002,
            80000.0,
            None,
            81000.0,
            79000.0,
            20,
            "OPEN",
        )
    )

    assert result.success is True
    assert captured["tp_price"] == 81000.0
    assert captured["sl_price"] == 79000.0


def test_order_status_stream_places_tp_sl_for_filled_open_order(monkeypatch):
    from trade_relay.trading import order_status_stream

    stream = order_status_stream.UserOrderStatusStream("Will", "key", "secret", False)
    placed: list[dict] = []

    monkeypatch.setattr(order_status_stream.db, "get_position", lambda *args, **kwargs: {"id": 99})
    monkeypatch.setattr(
        order_status_stream,
        "place_tp_sl_orders",
        lambda **kwargs: placed.append(kwargs) or [],
    )

    db_order = {
        "exchange_order_id": "12345",
        "user_id": 1,
        "symbol": "BTCUSDC",
        "side": "BUY",
        "trade_direction": "OPEN",
        "tp_price": 81000.0,
        "sl_price": 79000.0,
        "filled_qty": 0.002,
        "avg_price": 80000.0,
    }

    stream._place_open_fill_tpsl(db_order, executed_qty=0.002, avg_price=80000.0)

    assert placed == [{
        "username": "Will",
        "user_id": 1,
        "symbol": "BTCUSDC",
        "position_side": "LONG",
        "quantity": 0.002,
        "entry_price": 80000.0,
        "tp_price": 81000.0,
        "sl_price": 79000.0,
        "position_id": 99,
    }]


def test_get_conditional_orders_merges_trade_direction_from_db(monkeypatch):
    from backend.routers import orders as orders_router

    db_row = {
        "id": 42,
        "symbol": "BTCUSDC",
        "side": "SELL",
        "order_type": "STOP_MARKET",
        "quantity": 0.002,
        "price": None,
        "stop_price": None,
        "status": "NEW",
        "exchange_order_id": "987654321",
        "client_order_id": None,
        "trade_direction": "OPEN",
        "created_at": "2026-05-15 16:14:59",
    }
    backfill_calls = []

    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def get_open_algo_orders(self):
            return [{
                "algoId": 987654321,
                "clientAlgoId": "client-algo-123",
                "symbol": "BTCUSDC",
                "side": "SELL",
                "type": "STOP_MARKET",
                "quantity": "0.002",
                "triggerPrice": "80683.7",
                "algoStatus": "NEW",
            }]

    monkeypatch.setattr(orders_router.cfg, "get_api_key", lambda username: "key")
    monkeypatch.setattr(orders_router.cfg, "get_api_secret", lambda username: "secret")
    monkeypatch.setattr(orders_router.cfg, "is_testnet", lambda username: False)
    monkeypatch.setattr(orders_router.db_module, "query_orders", lambda **kwargs: [db_row])
    monkeypatch.setattr(orders_router.db_module, "update_order_metadata", lambda order_id, **kwargs: backfill_calls.append((order_id, kwargs)) or True)
    monkeypatch.setattr(orders_router, "FuturesBinanceClient", StubClient)

    result = asyncio.run(orders_router.get_conditional_orders({"username": "Will", "sub": "1", "role": "user"}))

    assert len(result) == 1
    assert result[0].trade_direction == "OPEN"
    assert result[0].client_order_id == "client-algo-123"
    assert result[0].trigger_price == 80683.7
    assert backfill_calls == [(42, {"client_order_id": "client-algo-123", "stop_price": 80683.7})]


def test_get_conditional_orders_finalizes_stale_finished_algo_order(monkeypatch):
    from backend.routers import orders as orders_router

    db_row = {
        "id": 77,
        "symbol": "BTCUSDC",
        "side": "SELL",
        "order_type": "STOP_MARKET",
        "quantity": 0.002,
        "price": None,
        "stop_price": 80683.7,
        "status": "NEW",
        "exchange_order_id": "987654321",
        "client_order_id": "client-algo-123",
        "trade_direction": "OPEN",
        "created_at": "2026-05-15 16:14:59",
    }
    status_updates = []

    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def get_open_algo_orders(self):
            return []

        def get_algo_order(self, algo_id=None, client_algo_id=None):
            return {
                "algoId": 987654321,
                "clientAlgoId": "client-algo-123",
                "orderType": "STOP_MARKET",
                "algoStatus": "FINISHED",
                "actualPrice": "80650.5",
                "quantity": "0.002",
                "triggerPrice": "80683.7",
            }

    monkeypatch.setattr(orders_router.cfg, "get_api_key", lambda username: "key")
    monkeypatch.setattr(orders_router.cfg, "get_api_secret", lambda username: "secret")
    monkeypatch.setattr(orders_router.cfg, "is_testnet", lambda username: False)
    monkeypatch.setattr(orders_router.db_module, "query_orders", lambda **kwargs: [db_row])
    monkeypatch.setattr(orders_router.db_module, "update_order_status", lambda order_id, status, **kwargs: status_updates.append((order_id, status, kwargs)) or True)
    monkeypatch.setattr(orders_router, "FuturesBinanceClient", StubClient)

    result = asyncio.run(orders_router.get_conditional_orders({"username": "Will", "sub": "1", "role": "user"}))

    assert result == []
    assert status_updates == [(77, "FILLED", {"filled_qty": 0.002, "avg_price": 80650.5})]


def test_positions_restore_tp_sl_from_persisted_conditional_orders(monkeypatch):
    from backend.routers import positions as positions_router

    monkeypatch.setattr(
        positions_router.db_module,
        "get_positions",
        lambda user_id=None: [{
            "id": 7,
            "symbol": "BTCUSDC",
            "position_side": "LONG",
            "quantity": 0.012,
            "avg_entry_price": 78000.0,
            "unrealized_pnl": 12.5,
            "leverage": 20,
            "margin_type": "cross",
        }],
    )
    monkeypatch.setattr(
        positions_router.db_module,
        "query_orders",
        lambda **kwargs: [
            {
                "position_id": 7,
                "symbol": "BTCUSDC",
                "side": "SELL",
                "trade_direction": "CLOSE",
                "order_type": "TAKE_PROFIT_MARKET",
                "price": 79500.0,
                "stop_price": None,
                "status": "NEW",
            },
            {
                "position_id": 7,
                "symbol": "BTCUSDC",
                "side": "SELL",
                "trade_direction": "CLOSE",
                "order_type": "STOP_MARKET",
                "price": None,
                "stop_price": 77200.0,
                "status": "NEW",
            },
        ],
    )

    with positions_router._tpsl_store_lock:
        positions_router._tpsl_store.clear()

    positions = positions_router._db_positions(user_id=5)

    assert len(positions) == 1
    assert positions[0].tp_price == 79500.0
    assert positions[0].sl_price == 77200.0


def test_positions_restore_tp_sl_by_symbol_side_when_position_id_missing(monkeypatch):
    from backend.routers import positions as positions_router

    monkeypatch.setattr(
        positions_router.db_module,
        "get_positions",
        lambda user_id=None: [{
            "id": 15,
            "symbol": "BTCUSDC",
            "position_side": "SHORT",
            "quantity": 0.025,
            "avg_entry_price": 78900.0,
            "unrealized_pnl": 3.2,
            "leverage": 20,
            "margin_type": "cross",
        }],
    )
    monkeypatch.setattr(
        positions_router.db_module,
        "query_orders",
        lambda **kwargs: [
            {
                "position_id": None,
                "symbol": "BTCUSDC",
                "side": "BUY",
                "trade_direction": "CLOSE",
                "order_type": "TAKE_PROFIT_MARKET",
                "price": 78000.0,
                "stop_price": None,
                "status": "NEW",
            },
            {
                "position_id": None,
                "symbol": "BTCUSDC",
                "side": "BUY",
                "trade_direction": "CLOSE",
                "order_type": "STOP_MARKET",
                "price": None,
                "stop_price": 79450.0,
                "status": "NEW",
            },
        ],
    )

    with positions_router._tpsl_store_lock:
        positions_router._tpsl_store.clear()

    positions = positions_router._db_positions(user_id=5)

    assert len(positions) == 1
    assert positions[0].tp_price == 78000.0
    assert positions[0].sl_price == 79450.0