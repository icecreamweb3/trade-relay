import asyncio
import json
import sys
from datetime import datetime, timezone
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
    assert result.client_order_id is None
    assert result.algo_client_id == "client-algo-123"
    assert result.status == "NEW"


def test_submit_order_persists_stop_price_and_client_order_id(monkeypatch):
    from trade_relay.auth.manager import Session
    from trade_relay.trading import order_manager

    captured: dict = {}

    async def fake_place_order(**kwargs):
        return trading_binance_client.BinanceOrderResult(
            success=True,
            order_id="987654321",
            algo_client_id="client-algo-123",
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
    assert captured["client_order_id"] is None
    assert captured["algo_client_id"] == "client-algo-123"
    assert captured["binance_order_id"] is None
    assert captured["algo_id"] == "987654321"


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


def test_order_status_stream_resolves_actual_order_id_for_triggered_conditional_close(monkeypatch):
    from trade_relay.trading import order_status_stream

    stream = order_status_stream.UserOrderStatusStream("Will", "key", "secret", False)
    metadata_updates = []
    status_updates = []
    history_creations = []
    trade_sync_calls = []

    monkeypatch.setattr(
        order_status_stream.db,
        "get_active_orders_for_user",
        lambda username: [{
            "id": 91,
            "username": "Will",
            "user_id": 1,
            "symbol": "BTCUSDC",
            "side": "SELL",
            "trade_direction": "CLOSE",
            "order_type": "TAKE_PROFIT_MARKET",
            "order_category": "Conditional",
            "status": "NEW",
            "algo_id": "4000001326609744",
            "exchange_order_id": None,
            "quantity": 0.012,
        }],
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "update_order_metadata",
        lambda order_id, **kwargs: metadata_updates.append((order_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "update_order_status_by_exchange_id",
        lambda username, exchange_order_id, status, **kwargs: status_updates.append((username, exchange_order_id, status, kwargs)) or True,
    )
    monkeypatch.setattr(
        stream,
        "_create_position_history_from_poll",
        lambda **kwargs: history_creations.append(kwargs),
    )
    monkeypatch.setattr(
        order_status_stream,
        "sync_filled_order_trade_details",
        lambda **kwargs: trade_sync_calls.append(kwargs),
    )
    monkeypatch.setattr(stream, "_sync_position_from_rest", lambda symbol: None)
    monkeypatch.setattr(stream, "_notify_listeners", lambda event, force=False: None)

    class _StubClient:
        def get_algo_order(self, algo_id=None, client_algo_id=None):
            assert algo_id == 4000001326609744
            return {
                "algoId": 4000001326609744,
                "actualOrderId": 4000001327195551,
                "algoStatus": "TRIGGERED",
            }

        def get_order_status(self, symbol, order_id):
            assert symbol == "BTCUSDC"
            assert order_id == "4000001327195551"
            return {
                "status": "FILLED",
                "executedQty": "0.012",
                "avgPrice": "78391.7",
            }

    stream.client = _StubClient()

    stream._poll_open_orders_once()

    assert metadata_updates == [(91, {"exchange_order_id": "4000001327195551"})]
    assert status_updates == [(
        "Will",
        "4000001327195551",
        "FILLED",
        {"filled_qty": 0.012, "avg_price": 78391.7},
    )]
    assert history_creations == [{
        "symbol": "BTCUSDC",
        "position_side": "LONG",
        "fill_qty": 0.012,
        "fill_price": 78391.7,
    }]
    assert trade_sync_calls == [{
        "username": "Will",
        "client": stream.client,
        "order_row": {
            "id": 91,
            "username": "Will",
            "user_id": 1,
            "symbol": "BTCUSDC",
            "side": "SELL",
            "trade_direction": "CLOSE",
            "order_type": "TAKE_PROFIT_MARKET",
            "order_category": "Conditional",
            "status": "NEW",
            "algo_id": "4000001326609744",
            "exchange_order_id": "4000001327195551",
            "quantity": 0.012,
        },
    }]


def test_public_ticker_stream_transforms_binance_payload(monkeypatch):
    from trade_relay.exchange import public_ticker_stream

    monkeypatch.setattr(public_ticker_stream, 'get_proxy_config', lambda: (False, None, None))

    stream = public_ticker_stream.PublicTicker24hStream('BTCUSDC')
    captured: list[dict] = []

    stream.add_listener(captured.append)
    stream._on_message(None, json.dumps({
        'e': '24hrTicker',
        'E': 1710000000000,
        's': 'BTCUSDC',
        'p': '120.5',
        'P': '1.25',
        'o': '9640.0',
        'c': '9760.5',
        'h': '9800.0',
        'l': '9500.0',
        'v': '12.34',
        'q': '120345.67',
        'O': 1709913600000,
        'C': 1710000000000,
    }))

    assert captured == [{
        'type': 'ticker24h',
        'symbol': 'BTCUSDC',
        'lastPrice': 9760.5,
        'priceChange': 120.5,
        'priceChangePercent': 1.25,
        'openPrice': 9640.0,
        'highPrice': 9800.0,
        'lowPrice': 9500.0,
        'volume': 12.34,
        'quoteVolume': 120345.67,
        'openTime': 1709913600000,
        'closeTime': 1710000000000,
        'eventTime': 1710000000000,
    }]


def test_public_ticker_stream_replays_last_payload_to_new_listener(monkeypatch):
    from trade_relay.exchange import public_ticker_stream

    monkeypatch.setattr(public_ticker_stream, 'get_proxy_config', lambda: (False, None, None))

    stream = public_ticker_stream.PublicTicker24hStream('BTCUSDC')
    stream.last_payload = {'type': 'ticker24h', 'symbol': 'BTCUSDC', 'lastPrice': 1.0}
    captured: list[dict] = []

    stream.add_listener(captured.append)

    assert captured == [{'type': 'ticker24h', 'symbol': 'BTCUSDC', 'lastPrice': 1.0}]


def test_place_tp_sl_orders_replaces_existing_stop_loss_order(monkeypatch):
    from trade_relay.trading import tpsl_service

    cancel_calls = []
    created_orders = []
    status_updates = []

    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def cancel_algo_order(self, algo_id=None, client_algo_id=None, symbol=None, max_retries=None):
            cancel_calls.append({
                "algo_id": algo_id,
                "client_algo_id": client_algo_id,
                "symbol": symbol,
            })
            return {"success": True}

        def place_take_profit_order(self, symbol, side, price, quantity, position_side):
            return {"algoId": 2001, "clientAlgoId": "tp-1", "status": "NEW"}

        def place_stop_loss_order(self, symbol, side, stop_price, quantity, position_side):
            return {"algoId": 2002, "clientAlgoId": "sl-2", "status": "NEW"}

    monkeypatch.setattr(tpsl_service.cfg, "get_api_key", lambda username: "key")
    monkeypatch.setattr(tpsl_service.cfg, "get_api_secret", lambda username: "secret")
    monkeypatch.setattr(tpsl_service.cfg, "is_testnet", lambda username: False)
    monkeypatch.setattr(tpsl_service, "BinanceClient", StubClient)
    monkeypatch.setattr(
        tpsl_service.db,
        "query_orders",
        lambda **kwargs: [{
            "id": 11,
            "user_id": 1,
            "symbol": "BTCUSDC",
            "side": "SELL",
            "trade_direction": "CLOSE",
            "order_type": "STOP_MARKET",
            "position_id": 99,
            "exchange_order_id": "123456789",
            "status": "NEW",
        }],
    )
    monkeypatch.setattr(tpsl_service.db, "update_order_status", lambda order_id, status, **kwargs: status_updates.append((order_id, status, kwargs)) or True)
    monkeypatch.setattr(tpsl_service.db, "create_order", lambda **kwargs: created_orders.append(kwargs) or 100)

    errors = tpsl_service.place_tp_sl_orders(
        username="Will",
        user_id=1,
        symbol="BTCUSDC",
        position_side="LONG",
        quantity=0.002,
        entry_price=78000.0,
        tp_price=78500.0,
        sl_price=77600.0,
        position_id=99,
    )

    assert errors == []
    assert cancel_calls == [{"algo_id": 123456789, "client_algo_id": None, "symbol": "BTCUSDC"}]
    assert status_updates == [(11, "CANCELED", {})]
    assert len(created_orders) == 2
    assert created_orders[0]["order_type"] == "TAKE_PROFIT_MARKET"
    assert created_orders[1]["order_type"] == "STOP_MARKET"


def test_sync_close_order_trade_details_updates_orders_and_position_history(monkeypatch):
    from trade_relay.trading import close_trade_sync

    order_updates = []
    history_updates = []

    class StubClient:
        def get_trade_fills(self, symbol, order_id):
            assert symbol == "BTCUSDC"
            assert order_id == "90001"
            return [
                {
                    "qty": "0.001",
                    "commission": "0.10",
                    "commissionAsset": "USDC",
                    "realizedPnl": "1.50",
                },
                {
                    "qty": "0.002",
                    "commission": "0.20",
                    "commissionAsset": "USDC",
                    "realizedPnl": "2.50",
                },
            ]

    latest_order = {
        "id": 50,
        "user_id": 1,
        "username": "Will",
        "symbol": "BTCUSDC",
        "side": "SELL",
        "trade_direction": "CLOSE",
        "position_id": 9,
        "exchange_order_id": "90001",
        "status": "FILLED",
    }

    monkeypatch.setattr(close_trade_sync.db, "get_order_by_id", lambda order_id: latest_order if order_id == 50 else None)
    monkeypatch.setattr(
        close_trade_sync.db,
        "update_order_status",
        lambda order_id, status, **kwargs: order_updates.append((order_id, status, kwargs)) or True,
    )
    monkeypatch.setattr(
        close_trade_sync.db,
        "get_position_history",
        lambda user_id=None, limit=200: [
            {
                "id": 201,
                "user_id": 1,
                "username": "Will",
                "symbol": "BTCUSDC",
                "side": "LONG",
                "quantity": 0.001,
                "realized_pnl": 0.0,
                "commission": 0.0,
                "position_id": 9,
            },
            {
                "id": 202,
                "user_id": 1,
                "username": "Will",
                "symbol": "BTCUSDC",
                "side": "LONG",
                "quantity": 0.002,
                "realized_pnl": 0.0,
                "commission": 0.0,
                "position_id": 9,
            },
        ],
    )
    monkeypatch.setattr(
        close_trade_sync.db,
        "update_position_history_values",
        lambda history_id, realized_pnl, commission, commission_asset=None: history_updates.append((history_id, realized_pnl, commission, commission_asset)) or True,
    )

    close_trade_sync.sync_close_order_trade_details(
        username="Will",
        client=StubClient(),
        order_row={
            "id": 50,
            "trade_direction": "CLOSE",
            "exchange_order_id": "90001",
            "symbol": "BTCUSDC",
        },
    )

    assert order_updates == [(
        50,
        "FILLED",
        {
            "filled_qty": 0.003,
            "realized_pnl": 4.0,
            "commission": 0.30000000000000004,
            "commission_asset": "USDC",
        },
    )]
    assert len(history_updates) == 2
    assert round(sum(item[1] for item in history_updates), 8) == 4.0
    assert round(sum(item[2] for item in history_updates), 8) == 0.3
    assert {item[3] for item in history_updates} == {"USDC"}


def test_sync_filled_open_order_trade_details_updates_order_commission_only(monkeypatch):
    from trade_relay.trading import close_trade_sync

    order_updates = []
    history_updates = []

    class StubClient:
        def get_trade_fills(self, symbol, order_id):
            assert symbol == "BTCUSDC"
            assert order_id == "90002"
            return [
                {
                    "qty": "0.001",
                    "commission": "0.10",
                    "commissionAsset": "USDC",
                    "realizedPnl": "0",
                },
                {
                    "qty": "0.002",
                    "commission": "0.20",
                    "commissionAsset": "USDC",
                    "realizedPnl": "0",
                },
            ]

    latest_order = {
        "id": 51,
        "user_id": 1,
        "username": "Will",
        "symbol": "BTCUSDC",
        "side": "BUY",
        "trade_direction": "OPEN",
        "position_id": 10,
        "exchange_order_id": "90002",
        "status": "FILLED",
    }

    monkeypatch.setattr(close_trade_sync.db, "get_order_by_id", lambda order_id: latest_order if order_id == 51 else None)
    monkeypatch.setattr(
        close_trade_sync.db,
        "update_order_status",
        lambda order_id, status, **kwargs: order_updates.append((order_id, status, kwargs)) or True,
    )
    monkeypatch.setattr(
        close_trade_sync.db,
        "update_position_history_values",
        lambda history_id, realized_pnl, commission, commission_asset=None: history_updates.append((history_id, realized_pnl, commission, commission_asset)) or True,
    )

    close_trade_sync.sync_filled_order_trade_details(
        username="Will",
        client=StubClient(),
        order_row={
            "id": 51,
            "trade_direction": "OPEN",
            "exchange_order_id": "90002",
            "symbol": "BTCUSDC",
        },
    )

    assert order_updates == [(
        51,
        "FILLED",
        {
            "filled_qty": 0.003,
            "commission": 0.30000000000000004,
            "commission_asset": "USDC",
        },
    )]
    assert history_updates == []


def test_sync_position_history_from_filled_close_order_uses_order_totals(monkeypatch):
    from trade_relay.trading import close_trade_sync

    history_updates = []

    latest_order = {
        "id": 61,
        "user_id": 1,
        "username": "Will",
        "symbol": "BTCUSDC",
        "side": "SELL",
        "trade_direction": "CLOSE",
        "position_id": 12,
        "filled_qty": 0.003,
        "realized_pnl": 4.0,
        "commission": 0.3,
        "commission_asset": "USDC",
    }

    monkeypatch.setattr(close_trade_sync.db, "get_order_by_id", lambda order_id: latest_order if order_id == 61 else None)
    monkeypatch.setattr(
        close_trade_sync.db,
        "get_position_history",
        lambda user_id, limit=200: [
            {
                "id": 701,
                "symbol": "BTCUSDC",
                "side": "LONG",
                "quantity": 0.001,
                "realized_pnl": 0.0,
                "commission": 0.0,
                "commission_asset": None,
                "position_id": 12,
            },
            {
                "id": 702,
                "symbol": "BTCUSDC",
                "side": "LONG",
                "quantity": 0.002,
                "realized_pnl": 0.0,
                "commission": 0.0,
                "commission_asset": None,
                "position_id": 12,
            },
        ],
    )
    monkeypatch.setattr(
        close_trade_sync.db,
        "update_position_history_values",
        lambda history_id, realized_pnl, commission, commission_asset=None: history_updates.append((history_id, realized_pnl, commission, commission_asset)) or True,
    )

    updated_rows = close_trade_sync.sync_position_history_from_filled_close_order({"id": 61, "trade_direction": "CLOSE"})

    assert updated_rows == 2
    assert round(sum(item[1] for item in history_updates), 8) == 4.0
    assert round(sum(item[2] for item in history_updates), 8) == 0.3
    assert {item[3] for item in history_updates} == {"USDC"}


def test_sync_position_history_from_filled_close_order_backfills_asset_without_numeric_delta(monkeypatch):
    from trade_relay.trading import close_trade_sync

    history_updates = []

    latest_order = {
        "id": 62,
        "user_id": 1,
        "username": "Will",
        "symbol": "BTCUSDC",
        "side": "SELL",
        "trade_direction": "CLOSE",
        "position_id": 13,
        "filled_qty": 0.003,
        "realized_pnl": 4.0,
        "commission": 0.3,
        "commission_asset": "USDC",
    }

    monkeypatch.setattr(close_trade_sync.db, "get_order_by_id", lambda order_id: latest_order if order_id == 62 else None)
    monkeypatch.setattr(
        close_trade_sync.db,
        "get_position_history",
        lambda user_id, limit=200: [
            {
                "id": 703,
                "symbol": "BTCUSDC",
                "side": "LONG",
                "quantity": 0.003,
                "realized_pnl": 4.0,
                "commission": 0.3,
                "commission_asset": None,
                "position_id": 13,
            },
        ],
    )
    monkeypatch.setattr(
        close_trade_sync.db,
        "update_position_history_values",
        lambda history_id, realized_pnl, commission, commission_asset=None: history_updates.append((history_id, realized_pnl, commission, commission_asset)) or True,
    )

    updated_rows = close_trade_sync.sync_position_history_from_filled_close_order({"id": 62, "trade_direction": "CLOSE"})

    assert updated_rows == 1
    assert history_updates == [(703, 4.0, 0.3, "USDC")]


def test_sync_position_history_from_filled_close_order_creates_missing_history_row(monkeypatch):
    from trade_relay.trading import close_trade_sync

    created_rows = []

    latest_order = {
        "id": 63,
        "user_id": 3,
        "username": "Simba",
        "symbol": "BTCUSDC",
        "side": "BUY",
        "trade_direction": "CLOSE",
        "position_id": 9,
        "filled_qty": 0.012,
        "avg_price": 78556.2,
        "realized_pnl": 2.41439999,
        "commission": 0.37706976,
        "commission_asset": "USDC",
        "created_at": "2026-05-16 14:42:34",
        "updated_at": "2026-05-16 17:58:51",
        "exchange_order_id": "58103959698",
    }

    monkeypatch.setattr(close_trade_sync.db, "get_order_by_id", lambda order_id: latest_order if order_id == 63 else None)
    monkeypatch.setattr(
        close_trade_sync.db,
        "get_position_history",
        lambda user_id, limit=200: [
            {
                "id": 801,
                "symbol": "BTCUSDC",
                "side": "SHORT",
                "quantity": 0.012,
                "close_price": 77592.2,
                "realized_pnl": 2.8056,
                "commission": 0.37244256,
                "commission_asset": "USDC",
                "position_id": None,
                "created_at": "2026-05-16 10:29:13",
            },
        ],
    )
    monkeypatch.setattr(close_trade_sync.db, "update_position_history_values", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        close_trade_sync.db,
        "add_position_history",
        lambda **kwargs: created_rows.append(kwargs) or 901,
    )

    updated_rows = close_trade_sync.sync_position_history_from_filled_close_order({"id": 63, "trade_direction": "CLOSE"})

    assert updated_rows == 1
    assert len(created_rows) == 1
    assert created_rows[0]["user_id"] == 3
    assert created_rows[0]["username"] == "Simba"
    assert created_rows[0]["symbol"] == "BTCUSDC"
    assert created_rows[0]["side"] == "SHORT"
    assert created_rows[0]["quantity"] == 0.012
    assert created_rows[0]["close_price"] == 78556.2
    assert round(created_rows[0]["entry_price"], 4) == 78757.4
    assert created_rows[0]["realized_pnl"] == 2.41439999
    assert created_rows[0]["commission"] == 0.37706976
    assert created_rows[0]["commission_asset"] == "USDC"
    assert created_rows[0]["position_id"] == 9


def test_order_status_stream_close_fill_updates_order_trade_fields_without_user_trades(monkeypatch):
    from trade_relay.trading import order_status_stream

    stream = order_status_stream.UserOrderStatusStream("Will", "key", "secret", False)
    order_updates = []
    history_rows = []
    trade_sync_calls = []

    monkeypatch.setattr(
        order_status_stream.db,
        "get_order_by_exchange_id",
        lambda username, exchange_order_id: {
            "id": 75,
            "username": "Will",
            "user_id": 1,
            "symbol": "BTCUSDC",
            "side": "SELL",
            "trade_direction": "CLOSE",
            "exchange_order_id": exchange_order_id,
            "filled_qty": None,
            "avg_price": None,
            "realized_pnl": None,
            "commission": None,
            "commission_asset": None,
        },
    )
    monkeypatch.setattr(order_status_stream.db, "get_user_by_username", lambda username: {"id": 1})
    monkeypatch.setattr(
        order_status_stream.db,
        "get_position",
        lambda user_id, symbol, side: {"id": 13, "avg_entry_price": 78050.0},
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "update_order_status_by_exchange_id",
        lambda username, exchange_order_id, status, **kwargs: order_updates.append((username, exchange_order_id, status, kwargs)) or True,
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "add_position_history",
        lambda **kwargs: history_rows.append(kwargs) or 901,
    )
    monkeypatch.setattr(order_status_stream, "sync_filled_order_trade_details", lambda **kwargs: trade_sync_calls.append(kwargs))
    monkeypatch.setattr(order_status_stream.threading, "Thread", lambda target, args=(), daemon=None: type("_T", (), {"start": lambda self: None})())

    stream._handle_close_fill({
        "x": "TRADE",
        "X": "FILLED",
        "i": "58137689662",
        "s": "BTCUSDC",
        "ps": "LONG",
        "l": "0.025",
        "z": "0.025",
        "L": "78034.2",
        "ap": "78034.2",
        "n": "0.192",
        "N": "USDC",
        "rp": "-0.395",
    })

    assert history_rows[0]["commission"] == 0.192
    assert history_rows[0]["commission_asset"] == "USDC"
    assert history_rows[0]["realized_pnl"] == -0.395
    assert order_updates == [(
        "Will",
        "58137689662",
        "FILLED",
        {
            "filled_qty": 0.025,
            "avg_price": 78034.2,
            "realized_pnl": -0.395,
            "commission": 0.192,
            "commission_asset": "USDC",
        },
    )]
    assert len(trade_sync_calls) == 1


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
        "algo_id": "987654321",
        "algo_client_id": None,
        "exchange_order_id": None,
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
    assert result[0].algo_client_id == "client-algo-123"
    assert result[0].trigger_price == 80683.7
    assert backfill_calls == [(42, {"algo_client_id": "client-algo-123", "stop_price": 80683.7})]


def test_get_conditional_orders_finalizes_stale_finished_algo_order(monkeypatch):
    from backend.routers import orders as orders_router

    db_row = {
        "id": 77,
        "user_id": 1,
        "username": "Will",
        "symbol": "BTCUSDC",
        "side": "SELL",
        "order_type": "STOP_MARKET",
        "quantity": 0.002,
        "price": None,
        "stop_price": 80683.7,
        "status": "NEW",
        "algo_id": "987654321",
        "algo_client_id": "client-algo-123",
        "exchange_order_id": None,
        "client_order_id": None,
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
                "actualOrderId": 123456789,
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
    monkeypatch.setattr(orders_router.db_module, "update_order_metadata", lambda order_id, **kwargs: True)
    monkeypatch.setattr(orders_router.db_module, "update_order_status", lambda order_id, status, **kwargs: status_updates.append((order_id, status, kwargs)) or True)
    monkeypatch.setattr(orders_router, "FuturesBinanceClient", StubClient)

    result = asyncio.run(orders_router.get_conditional_orders({"username": "Will", "sub": "1", "role": "user"}))

    assert result == []
    assert status_updates == [(77, "FILLED", {"filled_qty": 0.002, "avg_price": 80650.5})]


def test_get_conditional_orders_creates_position_history_for_filled_close_algo_order(monkeypatch):
    from backend.routers import orders as orders_router

    db_row = {
        "id": 88,
        "user_id": 1,
        "username": "Will",
        "symbol": "BTCUSDC",
        "side": "SELL",
        "trade_direction": "CLOSE",
        "position_id": 9,
        "order_type": "TAKE_PROFIT_MARKET",
        "quantity": 0.012,
        "price": 78587.0,
        "stop_price": None,
        "status": "NEW",
        "algo_id": "4000001326609744",
        "algo_client_id": "0l8MYbmR4kqGrHoXtxSryG",
        "exchange_order_id": None,
        "client_order_id": None,
        "created_at": "2026-05-16 14:40:00",
    }
    status_updates = []
    history_calls = []
    metadata_calls = []

    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def get_open_algo_orders(self):
            return []

        def get_algo_order(self, algo_id=None, client_algo_id=None):
            return {
                "algoId": 4000001326609744,
                "clientAlgoId": "0l8MYbmR4kqGrHoXtxSryG",
                "actualOrderId": 4000001327195551,
                "orderType": "TAKE_PROFIT_MARKET",
                "algoStatus": "FINISHED",
                "actualPrice": "78556.2",
                "quantity": "0.012",
                "triggerPrice": "78587.0",
            }

    monkeypatch.setattr(orders_router.cfg, "get_api_key", lambda username: "key")
    monkeypatch.setattr(orders_router.cfg, "get_api_secret", lambda username: "secret")
    monkeypatch.setattr(orders_router.cfg, "is_testnet", lambda username: False)
    monkeypatch.setattr(orders_router.db_module, "query_orders", lambda **kwargs: [db_row] if kwargs.get("status") == "NEW" else [])
    monkeypatch.setattr(orders_router.db_module, "update_order_metadata", lambda order_id, **kwargs: metadata_calls.append((order_id, kwargs)) or True)
    monkeypatch.setattr(orders_router.db_module, "update_order_status", lambda order_id, status, **kwargs: status_updates.append((order_id, status, kwargs)) or True)
    monkeypatch.setattr(orders_router.db_module, "get_positions", lambda user_id=None: [{
        "id": 9,
        "symbol": "BTCUSDC",
        "position_side": "LONG",
        "avg_entry_price": 78000.0,
    }])
    monkeypatch.setattr(orders_router.db_module, "add_position_history", lambda **kwargs: history_calls.append(kwargs) or 123)
    monkeypatch.setattr(orders_router, "FuturesBinanceClient", StubClient)

    result = asyncio.run(orders_router.get_conditional_orders({"username": "Will", "sub": "1", "role": "user"}))

    assert result == []
    assert metadata_calls == [(88, {"exchange_order_id": "4000001327195551"})]
    assert status_updates == [(88, "FILLED", {"filled_qty": 0.012, "avg_price": 78556.2})]
    assert history_calls == [{
        "user_id": 1,
        "username": "Will",
        "symbol": "BTCUSDC",
        "side": "LONG",
        "entry_price": 78000.0,
        "close_price": 78556.2,
        "quantity": 0.012,
        "realized_pnl": (78556.2 - 78000.0) * 0.012,
        "commission": 0.0,
        "position_id": 9,
    }]


def test_create_order_stores_conditional_algo_id_separately(monkeypatch):
    from trade_relay import database as db_module

    writes = []

    class _StubCursor:
        lastrowid = 999

        def execute(self, sql, params):
            writes.append((sql, params))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _StubConn:
        def cursor(self):
            return _StubCursor()

        def commit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(db_module, "get_connection", lambda: _StubConn())

    order_id = db_module.create_order(
        user_id=1,
        username="Will",
        symbol="BTCUSDC",
        side="SELL",
        order_type="STOP_MARKET",
        quantity=0.01,
        price=None,
        stop_price=78000.0,
        status="NEW",
        binance_order_id="4000001327195551",
        algo_client_id="client-algo-1",
        client_order_id=None,
        trade_direction="CLOSE",
        order_category="Conditional",
    )

    assert order_id == 999
    insert_sql, insert_params = writes[-1]
    assert "algo_id, algo_client_id, exchange_order_id, client_order_id" in insert_sql
    assert insert_params[12] == "4000001327195551"
    assert insert_params[13] == "client-algo-1"
    assert insert_params[14] is None


def test_get_position_history_orders_by_latest_update_at(monkeypatch):
    from trade_relay import database as db_module

    queries = []

    class _StubCursor:
        def execute(self, sql, params):
            queries.append((sql, params))

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _StubConn:
        def cursor(self):
            return _StubCursor()

        def close(self):
            return None

    monkeypatch.setattr(db_module, "get_connection", lambda: _StubConn())

    rows = db_module.get_position_history(user_id=5, limit=20)

    assert rows == []
    sql, params = queries[-1]
    assert "ORDER BY COALESCE(update_at, created_at) DESC, id DESC LIMIT %s" in sql
    assert params == [5, 20]


def test_create_mysql_connection_sets_session_timezone_to_utc(monkeypatch):
    from trade_relay import database as db_module

    executed = []

    class _StubCursor:
        def execute(self, sql, params=None):
            executed.append((sql, params))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _StubConn:
        def cursor(self):
            return _StubCursor()

    monkeypatch.setattr(db_module, "_mysql_cfg", lambda: {"host": "127.0.0.1"})
    monkeypatch.setattr(db_module, "_mysql_proxy_cfg", lambda: None)
    monkeypatch.setattr(db_module.pymysql, "connect", lambda **kwargs: _StubConn())

    conn = db_module._create_mysql_connection()

    assert conn is not None
    assert executed == [(db_module._MYSQL_SESSION_UTC_SQL, None)]


def test_pooled_connection_checkout_resets_session_timezone_to_utc():
    from trade_relay import database as db_module

    executed = []

    class _StubCursor:
        def execute(self, sql, params=None):
            executed.append((sql, params))

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _StubConn:
        def __init__(self):
            self.pings = []

        def ping(self, reconnect=True):
            self.pings.append(reconnect)

        def cursor(self):
            return _StubCursor()

    conn = _StubConn()
    pool = db_module._MySQLConnectionPool(max_size=1)

    assert pool._prepare_for_checkout(conn) is True
    assert conn.pings == [True]
    assert executed == [(db_module._MYSQL_SESSION_UTC_SQL, None)]


def test_serialize_utc_timestamp_normalizes_datetime_and_epoch_millis():
    from backend.time_utils import serialize_utc_timestamp

    assert serialize_utc_timestamp(datetime(2026, 5, 16, 20, 34, 1)) == "2026-05-16T20:34:01Z"

    epoch_ms = int(datetime(2026, 5, 16, 18, 29, 13, tzinfo=timezone.utc).timestamp() * 1000)
    assert serialize_utc_timestamp(epoch_ms) == "2026-05-16T18:29:13Z"
    assert serialize_utc_timestamp(str(epoch_ms)) == "2026-05-16T18:29:13Z"


def test_order_row_to_out_serializes_created_at_as_utc_iso():
    from backend.routers import orders as orders_router

    out = orders_router._row_to_out({
        "id": 1,
        "username": "Will",
        "symbol": "BTCUSDC",
        "side": "BUY",
        "order_type": "LIMIT",
        "trade_direction": "OPEN",
        "quantity": 0.002,
        "price": 78000.0,
        "stop_price": None,
        "reduce_only": 0,
        "post_only": 0,
        "order_category": "Basic",
        "status": "NEW",
        "filled_qty": 0,
        "avg_price": None,
        "realized_pnl": None,
        "commission": None,
        "commission_asset": None,
        "algo_id": None,
        "algo_client_id": None,
        "exchange_order_id": None,
        "error_message": None,
        "created_at": datetime(2026, 5, 16, 12, 34, 2),
    })

    assert out.created_at == "2026-05-16T12:34:02Z"


def test_user_out_from_row_serializes_created_and_updated_at_as_utc_iso(monkeypatch):
    from backend.routers import users as users_router

    monkeypatch.setattr(users_router.cfg_module, "load_user_config", lambda username: {})
    monkeypatch.setattr(users_router.db_module, "decrypt_api_credential", lambda value: "")

    out = users_router._user_out_from_row({
        "id": 5,
        "username": "Will",
        "role": "user",
        "is_active": 1,
        "binance_api_key": "",
        "binance_api_secret": "",
        "created_at": datetime(2026, 5, 16, 14, 21, 16),
        "updated_at": datetime(2026, 5, 16, 20, 34, 1),
    })

    assert out.created_at == "2026-05-16T14:21:16Z"
    assert out.updated_at == "2026-05-16T20:34:01Z"


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