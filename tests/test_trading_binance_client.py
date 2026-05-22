import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trade_relay.trading import binance_client as trading_binance_client


@pytest.fixture
def restore_backend_locale():
    from trade_relay.i18n import current_locale, set_locale

    original_locale = current_locale()
    try:
        yield set_locale
    finally:
        set_locale(original_locale)


def test_set_position_mode_surfaces_exchange_error(monkeypatch):
    from trade_relay.exchange.binance_client import BinanceClient

    class StubRawClient:
        def futures_change_position_mode(self, dualSidePosition):
            raise Exception("APIError(code=-4067): Position mode cannot be changed while positions or open orders exist.")

    client = BinanceClient.__new__(BinanceClient)
    client.client = StubRawClient()

    with pytest.raises(RuntimeError, match="-4067"):
        client.set_position_mode(False)


def test_account_position_mode_update_rejects_failed_exchange_switch(monkeypatch, restore_backend_locale):
    from fastapi import HTTPException
    from backend.routers import account

    restore_backend_locale('en')

    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def get_positions(self):
            return []

        def get_open_orders(self):
            return []

        def get_open_algo_orders(self):
            return []

        def set_position_mode(self, hedge_mode=False):
            return False

    monkeypatch.setattr(account.cfg_module, "get_api_key", lambda username: "key")
    monkeypatch.setattr(account.cfg_module, "get_api_secret", lambda username: "secret")
    monkeypatch.setattr(account.cfg_module, "is_testnet", lambda username: False)
    monkeypatch.setattr(account, "BinanceClient", StubClient)
    monkeypatch.setattr(account, "_refresh_account_summary_from_exchange", lambda *args, **kwargs: {})
    monkeypatch.setattr(account, "_invalidate_account_summary_cache", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException, match="Failed to set position mode to SINGLE"):
        account.update_account_position_mode(
            account.PositionModeUpdateIn(symbol="BTCUSDC", position_mode="SINGLE"),
            {"username": "Will", "sub": "1"},
        )


def test_account_position_mode_update_rejects_when_open_positions_exist(monkeypatch, restore_backend_locale):
    from fastapi import HTTPException
    from backend.routers import account

    restore_backend_locale('zh')

    called = {"set_position_mode": False}

    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def get_positions(self):
            return [{"symbol": "BTCUSDC", "positionAmt": "0.01"}]

        def get_open_orders(self):
            return []

        def get_open_algo_orders(self):
            return []

        def set_position_mode(self, hedge_mode=False):
            called["set_position_mode"] = True
            return True

    monkeypatch.setattr(account.cfg_module, "get_api_key", lambda username: "key")
    monkeypatch.setattr(account.cfg_module, "get_api_secret", lambda username: "secret")
    monkeypatch.setattr(account.cfg_module, "is_testnet", lambda username: False)
    monkeypatch.setattr(account, "BinanceClient", StubClient)

    with pytest.raises(HTTPException, match="请先平掉全部持仓并取消全部挂单"):
        account.update_account_position_mode(
            account.PositionModeUpdateIn(symbol="BTCUSDC", position_mode="SINGLE"),
            {"username": "Will", "sub": "1"},
        )

    assert called["set_position_mode"] is False


def test_account_position_mode_update_rejects_when_open_orders_exist(monkeypatch, restore_backend_locale):
    from fastapi import HTTPException
    from backend.routers import account

    restore_backend_locale('en')

    called = {"set_position_mode": False}

    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def get_positions(self):
            return []

        def get_open_orders(self):
            return [{"symbol": "BTCUSDC", "orderId": 123}]

        def get_open_algo_orders(self):
            return [{"symbol": "BTCUSDC", "algoId": 456}]

        def set_position_mode(self, hedge_mode=False):
            called["set_position_mode"] = True
            return True

    monkeypatch.setattr(account.cfg_module, "get_api_key", lambda username: "key")
    monkeypatch.setattr(account.cfg_module, "get_api_secret", lambda username: "secret")
    monkeypatch.setattr(account.cfg_module, "is_testnet", lambda username: False)
    monkeypatch.setattr(account, "BinanceClient", StubClient)

    with pytest.raises(HTTPException, match=r"open_orders=1 open_algo_orders=1"):
        account.update_account_position_mode(
            account.PositionModeUpdateIn(symbol="BTCUSDC", position_mode="DUAL"),
            {"username": "Will", "sub": "1"},
        )

    assert called["set_position_mode"] is False


def test_account_position_mode_update_invalid_mode_is_localized(restore_backend_locale):
    from fastapi import HTTPException
    from backend.routers import account

    restore_backend_locale('zh')

    with pytest.raises(HTTPException, match="持仓模式必须是 SINGLE 或 DUAL"):
        account.update_account_position_mode(
            account.PositionModeUpdateIn(symbol="BTCUSDC", position_mode="INVALID"),
            {"username": "Will", "sub": "1"},
        )


def test_place_order_surfaces_stop_market_error(monkeypatch):
    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def set_leverage(self, symbol, leverage):
            return None

        def place_stop_loss_order(self, symbol, side, stop_price, quantity, position_side, reduce_only=False):
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

        def place_stop_loss_order(self, symbol, side, stop_price, quantity, position_side, reduce_only=False):
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
        "position_mode": "UNKNOWN",
    }]


def test_order_status_stream_uses_binance_private_ws_url_for_mainnet():
    from trade_relay.trading import order_status_stream

    stream = order_status_stream.UserOrderStatusStream("Will", "key", "secret", False)
    stream.listen_key = "listen-key-123"

    assert stream.ws_url == (
        "wss://fstream.binance.com/private/ws"
        "?listenKey=listen-key-123&events=ORDER_TRADE_UPDATE%2FACCOUNT_UPDATE"
    )


def test_order_status_stream_skips_close_tpsl_quantity_refresh_for_single_mode(monkeypatch):
    from trade_relay.trading import order_status_stream

    stream = order_status_stream.UserOrderStatusStream("Will", "key", "secret", False)
    placement_attempts = []

    monkeypatch.setattr(
        order_status_stream.db,
        "get_position",
        lambda *args, **kwargs: {"id": 99, "position_mode": "SINGLE"},
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "query_orders",
        lambda **kwargs: [{
            "trade_direction": "CLOSE",
            "symbol": "BTCUSDC",
            "side": "SELL",
            "order_type": "TAKE_PROFIT_MARKET",
            "quantity": 0.001,
            "price": 81000.0,
        }],
    )
    monkeypatch.setattr(
        order_status_stream,
        "place_tp_sl_orders",
        lambda **kwargs: placement_attempts.append(kwargs) or [],
    )

    stream._sync_close_tpsl_quantity(
        user_id=1,
        symbol="BTCUSDC",
        position_side="LONG",
        quantity=0.003,
        entry_price=80000.0,
    )

    assert placement_attempts == []


def test_order_status_stream_skips_close_tpsl_quantity_refresh_during_partial_close_fill(monkeypatch):
    from trade_relay.trading import order_status_stream
    from trade_relay.trading import close_tpsl_sync

    stream = order_status_stream.UserOrderStatusStream("Will", "key", "secret", False)
    placement_attempts = []
    history_rows = []
    enqueue_calls = []

    monkeypatch.setattr(
        order_status_stream.db,
        "get_order_by_exchange_id",
        lambda username, exchange_order_id: {
            "id": 227,
            "username": "Will",
            "user_id": 5,
            "symbol": "BTCUSDC",
            "side": "SELL",
            "trade_direction": "CLOSE",
            "exchange_order_id": exchange_order_id,
            "filled_qty": None,
            "avg_price": None,
            "realized_pnl": None,
            "commission": None,
            "commission_asset": None,
            "position_mode": "DUAL",
        },
    )
    monkeypatch.setattr(order_status_stream.db, "get_user_by_username", lambda username: {"id": 5})
    monkeypatch.setattr(
        order_status_stream.db,
        "get_position",
        lambda user_id, symbol, side: {"id": 509, "avg_entry_price": 77732.83783784, "position_mode": "DUAL"},
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "update_order_status_by_exchange_id",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "has_pending_close_tpsl_refresh",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "enqueue_order_close_tpsl_refresh",
        lambda order_id, **kwargs: enqueue_calls.append((order_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "add_position_history",
        lambda **kwargs: history_rows.append(kwargs) or 902,
    )
    monkeypatch.setattr(order_status_stream, "sync_filled_order_trade_details", lambda **kwargs: None)
    monkeypatch.setattr(order_status_stream.threading, "Thread", lambda target, args=(), daemon=None: type("_T", (), {"start": lambda self: None})())
    monkeypatch.setattr(
        order_status_stream.db,
        "query_orders",
        lambda **kwargs: [{
            "trade_direction": "CLOSE",
            "symbol": "BTCUSDC",
            "side": "SELL",
            "order_type": "STOP_MARKET",
            "quantity": 0.037,
            "stop_price": 77700.0,
            "position_id": 509,
        }],
    )
    monkeypatch.setattr(
        close_tpsl_sync,
        "place_tp_sl_orders",
        lambda **kwargs: placement_attempts.append(kwargs) or [],
    )

    stream._handle_close_fill({
        "x": "TRADE",
        "X": "PARTIALLY_FILLED",
        "i": "58819962882",
        "s": "BTCUSDC",
        "ps": "LONG",
        "l": "0.001",
        "z": "0.001",
        "L": "77673.7",
        "ap": "77673.7",
        "n": "0.03106948",
        "N": "USDC",
        "rp": "-0.05913783",
    })

    stream._sync_close_tpsl_quantity(
        user_id=5,
        symbol="BTCUSDC",
        position_side="LONG",
        quantity=0.036,
        entry_price=77732.83783784,
    )

    assert history_rows[0]["quantity"] == 0.001
    assert placement_attempts == []
    assert enqueue_calls == [(227, {"delay_seconds": 1.0, "error_message": "close_fill_inflight"})]


def test_order_status_stream_keeps_close_tpsl_refresh_suppressed_until_final_fill(monkeypatch):
    from trade_relay.trading import order_status_stream
    from trade_relay.trading import close_tpsl_sync

    stream = order_status_stream.UserOrderStatusStream("Will", "key", "secret", False)
    placement_attempts = []
    history_rows = []
    pending_states = [True, True, True, False, False]

    monkeypatch.setattr(
        order_status_stream.db,
        "get_order_by_exchange_id",
        lambda username, exchange_order_id: {
            "id": 227,
            "username": "Will",
            "user_id": 5,
            "symbol": "BTCUSDC",
            "side": "SELL",
            "trade_direction": "CLOSE",
            "exchange_order_id": exchange_order_id,
            "filled_qty": None,
            "avg_price": None,
            "realized_pnl": None,
            "commission": None,
            "commission_asset": None,
            "position_mode": "DUAL",
        },
    )
    monkeypatch.setattr(order_status_stream.db, "get_user_by_username", lambda username: {"id": 5})
    monkeypatch.setattr(
        order_status_stream.db,
        "get_position",
        lambda user_id, symbol, side: {"id": 509, "avg_entry_price": 77732.83783784, "position_mode": "DUAL"},
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "update_order_status_by_exchange_id",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "has_pending_close_tpsl_refresh",
        lambda **kwargs: pending_states[0] if len(pending_states) == 1 else pending_states.pop(0),
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "enqueue_order_close_tpsl_refresh",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "add_position_history",
        lambda **kwargs: history_rows.append(kwargs) or (900 + len(history_rows)),
    )
    monkeypatch.setattr(order_status_stream, "sync_filled_order_trade_details", lambda **kwargs: None)
    monkeypatch.setattr(order_status_stream.threading, "Thread", lambda target, args=(), daemon=None: type("_T", (), {"start": lambda self: None})())
    monkeypatch.setattr(
        order_status_stream.db,
        "query_orders",
        lambda **kwargs: [{
            "trade_direction": "CLOSE",
            "symbol": "BTCUSDC",
            "side": "SELL",
            "order_type": "STOP_MARKET",
            "quantity": 0.037,
            "stop_price": 77700.0,
            "position_id": 509,
        }],
    )
    monkeypatch.setattr(
        close_tpsl_sync,
        "place_tp_sl_orders",
        lambda **kwargs: placement_attempts.append(kwargs) or [],
    )

    stream._handle_close_fill({
        "x": "TRADE",
        "X": "PARTIALLY_FILLED",
        "i": "58819962882",
        "s": "BTCUSDC",
        "ps": "LONG",
        "l": "0.001",
        "z": "0.001",
        "L": "77673.7",
        "ap": "77673.7",
        "n": "0.03106948",
        "N": "USDC",
        "rp": "-0.05913783",
    })
    stream._sync_close_tpsl_quantity(
        user_id=5,
        symbol="BTCUSDC",
        position_side="LONG",
        quantity=0.036,
        entry_price=77732.83783784,
    )

    stream._handle_close_fill({
        "x": "TRADE",
        "X": "PARTIALLY_FILLED",
        "i": "58819962882",
        "s": "BTCUSDC",
        "ps": "LONG",
        "l": "0.010",
        "z": "0.011",
        "L": "77673.6",
        "ap": "77673.60909091",
        "n": "0.31069480",
        "N": "USDC",
        "rp": "-0.59137838",
    })
    stream._sync_close_tpsl_quantity(
        user_id=5,
        symbol="BTCUSDC",
        position_side="LONG",
        quantity=0.026,
        entry_price=77732.83783784,
    )

    assert placement_attempts == []
    assert len(history_rows) == 2
    assert stream._has_inflight_close_fill(user_id=5, symbol="BTCUSDC", position_side="LONG") is True

    stream._handle_close_fill({
        "x": "TRADE",
        "X": "FILLED",
        "i": "58819962882",
        "s": "BTCUSDC",
        "ps": "LONG",
        "l": "0.026",
        "z": "0.037",
        "L": "77673.7",
        "ap": "77673.68108108",
        "n": "0.80780648",
        "N": "USDC",
        "rp": "-1.53758379",
        "T": 1747820000000,
    })

    assert stream._has_inflight_close_fill(user_id=5, symbol="BTCUSDC", position_side="LONG") is False

    stream._sync_close_tpsl_quantity(
        user_id=5,
        symbol="BTCUSDC",
        position_side="LONG",
        quantity=0.02,
        entry_price=77732.83783784,
    )

    assert len(history_rows) == 3
    assert [row["quantity"] for row in history_rows] == [0.001, 0.01, 0.026]
    assert len(placement_attempts) == 1
    assert placement_attempts[0]["quantity"] == 0.02


def test_order_status_stream_treats_partial_close_order_row_as_inflight_even_without_retry_flag(monkeypatch):
    from trade_relay.trading import order_status_stream
    from trade_relay.trading import close_tpsl_sync

    stream = order_status_stream.UserOrderStatusStream("Will", "key", "secret", False)
    placement_attempts = []

    monkeypatch.setattr(
        order_status_stream.db,
        "get_position",
        lambda user_id, symbol, side: {"id": 509, "avg_entry_price": 77732.83783784, "position_mode": "DUAL"},
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "has_pending_close_tpsl_refresh",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "query_orders",
        lambda **kwargs: [{
            "id": 255,
            "user_id": 5,
            "symbol": "BTCUSDC",
            "side": "SELL",
            "trade_direction": "CLOSE",
            "order_type": "STOP_MARKET",
            "status": "PARTIALLY_FILLED",
            "quantity": 0.043,
            "stop_price": 77700.0,
            "position_id": 509,
        }],
    )
    monkeypatch.setattr(
        close_tpsl_sync,
        "sync_close_tpsl_quantity",
        lambda **kwargs: placement_attempts.append(kwargs) or [],
    )

    stream._sync_close_tpsl_quantity(
        user_id=5,
        symbol="BTCUSDC",
        position_side="LONG",
        quantity=0.04,
        entry_price=77732.83783784,
    )

    assert placement_attempts == []


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
        {"filled_qty": 0.012, "avg_price": 78391.7, "filled_at": None},
    )]
    assert history_creations == [{
        "symbol": "BTCUSDC",
        "position_side": "LONG",
        "position_mode": "UNKNOWN",
        "fill_qty": 0.012,
        "fill_price": 78391.7,
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


def test_public_ticker_stream_transforms_combined_stream_payload(monkeypatch):
    from trade_relay.exchange import public_ticker_stream

    monkeypatch.setattr(public_ticker_stream, 'get_proxy_config', lambda: (False, None, None))

    stream = public_ticker_stream.PublicTicker24hStream('BTCUSDC')
    captured: list[dict] = []

    stream.add_listener(captured.append)
    stream._on_message(None, json.dumps({
        'stream': 'btcusdc@ticker',
        'data': {
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
        },
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


def test_order_status_stream_persists_filled_open_order_without_explicit_trade_details_enqueue(monkeypatch):
    from trade_relay.trading import order_status_stream

    stream = order_status_stream.UserOrderStatusStream("Will", "key", "secret", False)
    status_updates = []

    monkeypatch.setattr(
        order_status_stream.db,
        "update_order_status_by_exchange_id",
        lambda username, exchange_order_id, status, **kwargs: status_updates.append((username, exchange_order_id, status, kwargs)) or True,
    )

    stream._persist_status({
        "i": "58833049838",
        "X": "FILLED",
        "z": "0.001",
        "ap": "77890",
        "n": "0",
        "N": "USDC",
        "T": 1779343000000,
    })

    assert status_updates == [(
        "Will",
        "58833049838",
        "FILLED",
        {
            "filled_qty": 0.001,
            "avg_price": 77890.0,
            "filled_at": 1779343000000,
            "commission": 0.0,
            "commission_asset": "USDC",
            "error_message": None,
        },
    )]


def test_poll_open_orders_backfills_finished_conditional_algo_after_local_cancel(monkeypatch):
    from trade_relay.trading import order_status_stream

    stream = order_status_stream.UserOrderStatusStream("Will", "key", "secret", False)
    metadata_updates = []
    status_updates = []
    history_creations = []
    trade_sync_calls = []

    monkeypatch.setattr(order_status_stream.db, "get_active_orders_for_user", lambda username: [])
    monkeypatch.setattr(
        order_status_stream.db,
        "query_orders",
        lambda **kwargs: [{
            "id": 227,
            "username": "Will",
            "user_id": 5,
            "symbol": "BTCUSDC",
            "side": "SELL",
            "trade_direction": "CLOSE",
            "order_type": "STOP_MARKET",
            "order_category": "Conditional",
            "status": "CANCELED",
            "algo_id": "1000001711252489",
            "exchange_order_id": None,
            "quantity": 0.037,
        }],
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "update_order_metadata",
        lambda order_id, **kwargs: metadata_updates.append((order_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        order_status_stream.db,
        "update_order_status",
        lambda order_id, status, **kwargs: status_updates.append((order_id, status, kwargs)) or True,
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
            assert algo_id == 1000001711252489
            return {
                "algoId": 1000001711252489,
                "actualOrderId": "58819962882",
                "algoStatus": "FINISHED",
                "actualPrice": "77673.7",
                "actualQty": "0.037",
                "triggerTime": 1779342518986,
            }

        def get_order_status(self, symbol, order_id):
            assert symbol == "BTCUSDC"
            assert order_id == "58819962882"
            return {
                "status": "FILLED",
                "executedQty": "0.037",
                "avgPrice": "77673.7",
                "updateTime": 1779342519111,
            }

    stream.client = _StubClient()

    stream._poll_open_orders_once()

    assert metadata_updates == [(227, {"exchange_order_id": "58819962882"})]
    assert status_updates == [(
        227,
        "FILLED",
        {"filled_qty": 0.037, "avg_price": 77673.7, "filled_at": 1779342519111},
    )]
    assert history_creations == [{
        "symbol": "BTCUSDC",
        "position_side": "LONG",
        "position_mode": "UNKNOWN",
        "fill_qty": 0.037,
        "fill_price": 77673.7,
        "order_row": {
            "id": 227,
            "username": "Will",
            "user_id": 5,
            "symbol": "BTCUSDC",
            "side": "SELL",
            "trade_direction": "CLOSE",
            "order_type": "STOP_MARKET",
            "order_category": "Conditional",
            "status": "CANCELED",
            "algo_id": "1000001711252489",
            "exchange_order_id": "58819962882",
            "quantity": 0.037,
        },
    }]
    assert trade_sync_calls == [{
        "username": "Will",
        "client": stream.client,
        "order_row": {
            "id": 227,
            "username": "Will",
            "user_id": 5,
            "symbol": "BTCUSDC",
            "side": "SELL",
            "trade_direction": "CLOSE",
            "order_type": "STOP_MARKET",
            "order_category": "Conditional",
            "status": "CANCELED",
            "algo_id": "1000001711252489",
            "exchange_order_id": "58819962882",
            "quantity": 0.037,
        },
    }]


def test_create_position_history_from_poll_uses_order_realized_pnl_when_entry_price_missing(monkeypatch):
    from trade_relay.trading import order_status_stream

    stream = order_status_stream.UserOrderStatusStream("Simba", "key", "secret", False)
    captured = {}

    monkeypatch.setattr(order_status_stream.db, "get_user_by_username", lambda username: {"id": 3, "username": username})
    monkeypatch.setattr(order_status_stream.db, "get_position", lambda user_id, symbol, side: None)
    monkeypatch.setattr(
        order_status_stream.db,
        "add_position_history",
        lambda **kwargs: captured.update(kwargs) or 56,
    )

    class _StubClient:
        def get_position_information(self, symbol=None):
            return []

    stream.client = _StubClient()

    stream._create_position_history_from_poll(
        symbol="BTCUSDC",
        position_side="SHORT",
        position_mode="DUAL",
        fill_qty=0.012,
        fill_price=77712.0,
        order_row={"realized_pnl": -0.86639999},
    )

    assert captured["entry_price"] == 0.0
    assert captured["realized_pnl"] == -0.86639999


def test_poll_open_orders_skips_duplicate_close_history_when_order_already_handled(monkeypatch):
    from trade_relay.trading import order_status_stream

    stream = order_status_stream.UserOrderStatusStream("Will", "key", "secret", False)
    history_creations = []
    trade_sync_calls = []

    stream._handled_close_fills.add("4000001327195551")

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
    monkeypatch.setattr(order_status_stream.db, "query_orders", lambda **kwargs: [])
    monkeypatch.setattr(order_status_stream.db, "update_order_metadata", lambda order_id, **kwargs: True)
    monkeypatch.setattr(order_status_stream.db, "update_order_status_by_exchange_id", lambda username, exchange_order_id, status, **kwargs: True)
    monkeypatch.setattr(stream, "_create_position_history_from_poll", lambda **kwargs: history_creations.append(kwargs))
    monkeypatch.setattr(order_status_stream, "sync_filled_order_trade_details", lambda **kwargs: trade_sync_calls.append(kwargs))
    monkeypatch.setattr(stream, "_sync_position_from_rest", lambda symbol: None)
    monkeypatch.setattr(stream, "_notify_listeners", lambda event, force=False: None)

    class _StubClient:
        def get_order_status(self, symbol, order_id):
            return {
                "status": "FILLED",
                "executedQty": "0.012",
                "avgPrice": "78391.7",
            }

        def get_algo_order(self, algo_id=None, client_algo_id=None):
            return {
                "algoId": 4000001326609744,
                "actualOrderId": "4000001327195551",
                "algoStatus": "TRIGGERED",
            }

    stream.client = _StubClient()

    stream._poll_open_orders_once()

    assert history_creations == []
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


def test_sync_positions_marks_flat_position_closed(monkeypatch):
    from trade_relay.trading import order_status_stream

    stream = order_status_stream.UserOrderStatusStream("Will", "key", "secret", False)
    close_calls = []
    cancel_calls = []
    upsert_calls = []

    monkeypatch.setattr(order_status_stream.db, "get_user_by_username", lambda username: {"id": 5, "username": username})
    monkeypatch.setattr(
        order_status_stream.db,
        "get_positions",
        lambda user_id=None: [{"id": 525, "symbol": "BTCUSDC", "position_side": "LONG", "status": "OPEN"}],
    )
    monkeypatch.setattr(order_status_stream.db, "close_position", lambda user_id, symbol, position_side, exchange="binance": close_calls.append((user_id, symbol, position_side, exchange)) or True)
    monkeypatch.setattr(order_status_stream.db, "upsert_position", lambda **kwargs: upsert_calls.append(kwargs))
    monkeypatch.setattr(
        order_status_stream,
        "cancel_close_tp_sl_orders",
        lambda **kwargs: cancel_calls.append(kwargs) or [],
    )

    stream._sync_positions([{
        "s": "BTCUSDC",
        "ps": "LONG",
        "pa": "0",
        "ep": "77843.05",
        "up": "0",
        "cr": "0",
        "mt": "cross",
    }])

    assert close_calls == [(5, "BTCUSDC", "LONG", "binance")]
    assert len(cancel_calls) == 1
    assert upsert_calls == []


def test_sync_positions_reopens_position_with_open_status(monkeypatch):
    from trade_relay.trading import order_status_stream

    stream = order_status_stream.UserOrderStatusStream("Will", "key", "secret", False)
    upsert_calls = []

    monkeypatch.setattr(order_status_stream.db, "get_user_by_username", lambda username: {"id": 5, "username": username})
    monkeypatch.setattr(
        order_status_stream.db,
        "get_positions",
        lambda user_id=None: [{"id": 525, "symbol": "BTCUSDC", "position_side": "LONG", "status": "CLOSE", "leverage": 20}],
    )
    monkeypatch.setattr(order_status_stream.db, "upsert_position", lambda **kwargs: upsert_calls.append(kwargs))

    stream._sync_positions([{
        "s": "BTCUSDC",
        "ps": "LONG",
        "pa": "0.037",
        "ep": "77843.05",
        "lp": "76000",
        "up": "1.23",
        "cr": "0.45",
        "mt": "cross",
        "l": "20",
    }])

    assert len(upsert_calls) == 1
    assert upsert_calls[0]["status"] == "OPEN"
    assert upsert_calls[0]["quantity"] == 0.037


def test_initial_position_sync_marks_missing_positions_closed(monkeypatch):
    from trade_relay.trading import order_status_stream

    close_calls = []

    class _StubClient:
        def __init__(self, api_key=None, secret_key=None, testnet=False):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def get_position_information(self):
            return [{
                "symbol": "ETHUSDC",
                "positionSide": "LONG",
                "positionAmt": "0.1",
                "entryPrice": "2500",
                "liquidationPrice": "2000",
                "unRealizedProfit": "10",
                "marginType": "cross",
                "leverage": "10",
            }]

    class _StubStream:
        def __init__(self, username, api_key, api_secret, testnet):
            self.username = username

        def _sync_positions(self, payload):
            return None

    monkeypatch.setattr(order_status_stream, "BinanceClient", _StubClient)
    monkeypatch.setattr(order_status_stream, "UserOrderStatusStream", _StubStream)
    monkeypatch.setattr(order_status_stream.db, "get_user_by_username", lambda username: {"id": 5, "username": username})
    monkeypatch.setattr(
        order_status_stream.db,
        "get_positions",
        lambda user_id=None: [
            {"symbol": "BTCUSDC", "position_side": "LONG", "status": "OPEN"},
            {"symbol": "ETHUSDC", "position_side": "LONG", "status": "OPEN"},
        ],
    )
    monkeypatch.setattr(order_status_stream.db, "close_position", lambda user_id, symbol, position_side, exchange="binance": close_calls.append((user_id, symbol, position_side, exchange)) or True)

    order_status_stream.sync_initial_positions_for_user("Will", "key", "secret", False)

    assert close_calls == [(5, "BTCUSDC", "LONG", "binance")]


def test_public_ticker_stream_replays_last_payload_to_new_listener(monkeypatch):
    from trade_relay.exchange import public_ticker_stream

    monkeypatch.setattr(public_ticker_stream, 'get_proxy_config', lambda: (False, None, None))

    stream = public_ticker_stream.PublicTicker24hStream('BTCUSDC')
    stream.last_payload = {'type': 'ticker24h', 'symbol': 'BTCUSDC', 'lastPrice': 1.0}
    captured: list[dict] = []

    stream.add_listener(captured.append)

    assert captured == [{'type': 'ticker24h', 'symbol': 'BTCUSDC', 'lastPrice': 1.0}]


def test_submit_order_persists_post_only_flag(monkeypatch):
    from trade_relay.auth.manager import Session
    from trade_relay.trading import order_manager

    captured: dict = {}

    async def fake_place_order(**kwargs):
        captured['place_order'] = kwargs
        return trading_binance_client.BinanceOrderResult(
            success=True,
            order_id="12345",
            client_order_id="client-12345",
            status="NEW",
        )

    def fake_create_order(**kwargs):
        captured['create_order'] = kwargs
        return 44

    monkeypatch.setattr(order_manager.cfg, "is_mock_mode", lambda username: False)
    monkeypatch.setattr(order_manager.cfg, "get_api_key", lambda username: "key")
    monkeypatch.setattr(order_manager.cfg, "get_api_secret", lambda username: "secret")
    monkeypatch.setattr(order_manager.cfg, "is_testnet", lambda username: False)
    monkeypatch.setattr(order_manager, "place_order", fake_place_order)
    monkeypatch.setattr(order_manager.db, "create_order", fake_create_order)
    monkeypatch.setattr(order_manager.db, "log_operation", lambda *args, **kwargs: None)
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
            None,
            None,
            True,
            20,
            "OPEN",
        )
    )

    assert result.success is True
    assert captured['place_order']['post_only'] is True
    assert captured['create_order']['post_only'] is True


def test_binance_place_order_forwards_post_only_to_limit_client(monkeypatch):
    from trade_relay.trading import binance_client as trading_binance

    captured: dict = {}

    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def set_leverage(self, symbol, leverage):
            return None

        def place_limit_order(self, symbol, side, quantity, price, position_side, post_only, expire_seconds=None, reduce_only=False):
            captured.update({
                'symbol': symbol,
                'side': side,
                'quantity': quantity,
                'price': price,
                'position_side': position_side,
                'post_only': post_only,
                'expire_seconds': expire_seconds,
                'reduce_only': reduce_only,
            })
            return {'orderId': '999', 'status': 'NEW'}

    monkeypatch.setattr(trading_binance, 'FuturesBinanceClient', StubClient)

    result = asyncio.run(
        trading_binance.place_order(
            api_key='key',
            api_secret='secret',
            symbol='BTCUSDC',
            side='BUY',
            order_type='LIMIT',
            quantity=0.01,
            price=80000.0,
            post_only=True,
            leverage=20,
            testnet=False,
            position_direction='OPEN',
        )
    )

    assert result.success is True
    assert captured['post_only'] is True


def test_place_order_omits_position_side_for_single_mode_limit(monkeypatch):
    captured: dict = {}

    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def get_position_mode(self):
            return False

        def set_leverage(self, symbol, leverage):
            return None

        def place_limit_order(self, symbol, side, quantity, price, position_side, post_only, expire_seconds=None, reduce_only=False):
            captured.update({
                'symbol': symbol,
                'side': side,
                'quantity': quantity,
                'price': price,
                'position_side': position_side,
                'post_only': post_only,
                'expire_seconds': expire_seconds,
                'reduce_only': reduce_only,
            })
            return {'orderId': '1001', 'status': 'NEW'}

    monkeypatch.setattr(trading_binance_client, 'FuturesBinanceClient', StubClient)

    result = asyncio.run(
        trading_binance_client.place_order(
            api_key='key',
            api_secret='secret',
            symbol='BTCUSDC',
            side='BUY',
            order_type='LIMIT',
            quantity=0.01,
            price=77000.0,
            leverage=20,
            testnet=False,
            position_direction='OPEN',
            position_mode='SINGLE',
        )
    )

    assert result.success is True
    assert captured['position_side'] is None
    assert captured['reduce_only'] is False


def test_place_order_uses_reduce_only_for_single_mode_market_close(monkeypatch):
    captured: dict = {}

    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def get_position_mode(self):
            return False

        def set_leverage(self, symbol, leverage):
            return None

        def place_market_order(self, symbol, side, quantity, position_side=None, reduce_only=False):
            captured.update({
                'symbol': symbol,
                'side': side,
                'quantity': quantity,
                'position_side': position_side,
                'reduce_only': reduce_only,
            })
            return {'orderId': '1002', 'status': 'FILLED'}

    monkeypatch.setattr(trading_binance_client, 'FuturesBinanceClient', StubClient)

    result = asyncio.run(
        trading_binance_client.place_order(
            api_key='key',
            api_secret='secret',
            symbol='BTCUSDC',
            side='SELL',
            order_type='MARKET',
            quantity=0.01,
            leverage=20,
            testnet=False,
            position_direction='CLOSE',
            position_mode='SINGLE',
        )
    )

    assert result.success is True
    assert captured['position_side'] is None
    assert captured['reduce_only'] is True


def test_place_order_uses_reduce_only_for_single_mode_stop_close(monkeypatch):
    captured: dict = {}

    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def get_position_mode(self):
            return False

        def set_leverage(self, symbol, leverage):
            return None

        def place_conditional_order(self, symbol, side, quantity, stop_price, price=None, position_side=None, reduce_only=False):
            captured.update({
                'symbol': symbol,
                'side': side,
                'quantity': quantity,
                'stop_price': stop_price,
                'price': price,
                'position_side': position_side,
                'reduce_only': reduce_only,
            })
            return {'orderId': '1003', 'status': 'NEW'}

    monkeypatch.setattr(trading_binance_client, 'FuturesBinanceClient', StubClient)

    result = asyncio.run(
        trading_binance_client.place_order(
            api_key='key',
            api_secret='secret',
            symbol='BTCUSDC',
            side='SELL',
            order_type='STOP',
            quantity=0.01,
            price=76950.0,
            stop_price=77000.0,
            leverage=20,
            testnet=False,
            position_direction='CLOSE',
            position_mode='SINGLE',
        )
    )

    assert result.success is True
    assert captured['position_side'] is None
    assert captured['reduce_only'] is True


def test_place_order_omits_reduce_only_for_single_mode_stop_market_open(monkeypatch):
    captured: dict = {}

    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def get_position_mode(self):
            return False

        def set_leverage(self, symbol, leverage):
            return None

        def place_stop_loss_order(self, symbol, side, stop_price, quantity, position_side=None, reduce_only=False):
            captured.update({
                'symbol': symbol,
                'side': side,
                'quantity': quantity,
                'stop_price': stop_price,
                'position_side': position_side,
                'reduce_only': reduce_only,
            })
            return {'algoId': '1004', 'algoStatus': 'NEW'}

    monkeypatch.setattr(trading_binance_client, 'FuturesBinanceClient', StubClient)

    result = asyncio.run(
        trading_binance_client.place_order(
            api_key='key',
            api_secret='secret',
            symbol='BTCUSDC',
            side='BUY',
            order_type='STOP_MARKET',
            quantity=0.01,
            stop_price=77000.0,
            leverage=20,
            testnet=False,
            position_direction='OPEN',
            position_mode='SINGLE',
        )
    )

    assert result.success is True
    assert captured['position_side'] is None
    assert captured['reduce_only'] is False


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


def test_place_tp_sl_orders_persists_failed_stop_loss_as_failed(monkeypatch):
    from trade_relay.trading import tpsl_service

    created_orders = []

    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def place_stop_loss_order(self, symbol, side, stop_price, quantity, position_side):
            return {
                "error": True,
                "error_message": 'HTTP 400: {"code":-2021,"msg":"Order would immediately trigger."}',
            }

    monkeypatch.setattr(tpsl_service.cfg, "get_api_key", lambda username: "key")
    monkeypatch.setattr(tpsl_service.cfg, "get_api_secret", lambda username: "secret")
    monkeypatch.setattr(tpsl_service.cfg, "is_testnet", lambda username: False)
    monkeypatch.setattr(tpsl_service, "BinanceClient", StubClient)
    monkeypatch.setattr(tpsl_service.db, "query_orders", lambda **kwargs: [])
    monkeypatch.setattr(tpsl_service.db, "create_order", lambda **kwargs: created_orders.append(kwargs) or 100)

    errors = tpsl_service.place_tp_sl_orders(
        username="Will",
        user_id=5,
        symbol="BTCUSDC",
        position_side="LONG",
        quantity=0.043,
        entry_price=77843.05306122,
        tp_price=None,
        sl_price=77700.0,
        position_id=525,
        position_mode="DUAL",
    )

    assert errors == ['SL: HTTP 400: {"code":-2021,"msg":"Order would immediately trigger."}']
    assert len(created_orders) == 1
    assert created_orders[0]["order_type"] == "STOP_MARKET"
    assert created_orders[0]["status"] == "FAILED"
    assert 'Order would immediately trigger.' in created_orders[0]["error_message"]


def test_place_tp_sl_orders_uses_close_all_orders_for_single_mode(monkeypatch):
    from trade_relay.trading import tpsl_service

    created_orders = []
    client_calls = []

    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            self.api_key = api_key
            self.secret_key = secret_key
            self.testnet = testnet

        def place_close_all_take_profit_order(self, symbol, side, trigger_price):
            client_calls.append(("tp", symbol, side, trigger_price))
            return {"algoId": 3001, "clientAlgoId": "tp-close-all", "status": "NEW"}

        def place_close_all_stop_loss_order(self, symbol, side, stop_price):
            client_calls.append(("sl", symbol, side, stop_price))
            return {"algoId": 3002, "clientAlgoId": "sl-close-all", "status": "NEW"}

    monkeypatch.setattr(tpsl_service.cfg, "get_api_key", lambda username: "key")
    monkeypatch.setattr(tpsl_service.cfg, "get_api_secret", lambda username: "secret")
    monkeypatch.setattr(tpsl_service.cfg, "is_testnet", lambda username: False)
    monkeypatch.setattr(tpsl_service, "BinanceClient", StubClient)
    monkeypatch.setattr(tpsl_service.db, "query_orders", lambda **kwargs: [])
    monkeypatch.setattr(tpsl_service.db, "create_order", lambda **kwargs: created_orders.append(kwargs) or 101)

    errors = tpsl_service.place_tp_sl_orders(
        username="Will",
        user_id=1,
        symbol="BTCUSDC",
        position_side="LONG",
        quantity=0.005,
        entry_price=78000.0,
        tp_price=78500.0,
        sl_price=77600.0,
        position_id=99,
        position_mode="SINGLE",
    )

    assert errors == []
    assert client_calls == [
        ("tp", "BTCUSDC", "SELL", 78500.0),
        ("sl", "BTCUSDC", "SELL", 77600.0),
    ]
    assert len(created_orders) == 2
    assert all(order["position_mode"] == "SINGLE" for order in created_orders)
    assert created_orders[0]["quantity"] == 0.005
    assert created_orders[1]["quantity"] == 0.005


def test_production_binance_client_close_all_conditional_orders(monkeypatch):
    import requests
    from trade_relay.exchange import binance_client as exchange_binance_client

    post_calls = []

    class StubSdkClient:
        def __init__(self, api_key=None, api_secret=None, testnet=False):
            self.api_key = api_key
            self.api_secret = api_secret
            self.testnet = testnet

        def get_server_time(self):
            return {"serverTime": 1710000000000}

    class StubResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.text = "ok"

        def json(self):
            return self._payload

    def fake_post(url, headers=None, data=None, proxies=None, timeout=None):
        post_calls.append({
            "url": url,
            "headers": headers,
            "data": data,
            "proxies": proxies,
            "timeout": timeout,
        })
        if "TAKE_PROFIT_MARKET" in str(data):
            return StubResponse({"algoId": 7002, "type": "TAKE_PROFIT_MARKET"})
        return StubResponse({"algoId": 7001, "type": "STOP_MARKET"})

    monkeypatch.setattr(exchange_binance_client, "BinanceClientBase", StubSdkClient)
    monkeypatch.setattr(exchange_binance_client, "load_env", lambda override=True: None)
    monkeypatch.setattr(requests, "post", fake_post)

    client = exchange_binance_client.BinanceClient(api_key="key", secret_key="secret", testnet=False)
    monkeypatch.setattr(client, "get_position_mode", lambda: False)
    monkeypatch.setattr(client, "format_price_by_precision", lambda price, symbol: f"{price:.1f}")
    monkeypatch.setattr(
        client,
        "_generate_signed_request_body",
        lambda params, debug=False: ("sig", "&".join(f"{k}={v}" for k, v in sorted(params.items())) + "&signature=sig"),
    )

    stop_result = client.place_close_all_stop_loss_order("BTCUSDC", "SELL", 77600.0)
    tp_result = client.place_close_all_take_profit_order("BTCUSDC", "SELL", 78500.0)

    assert stop_result == {"algoId": 7001, "type": "STOP_MARKET"}
    assert tp_result == {"algoId": 7002, "type": "TAKE_PROFIT_MARKET"}
    assert len(post_calls) == 2
    assert all(call["url"].endswith("/fapi/v1/algoOrder") for call in post_calls)
    assert "closePosition=true" in post_calls[0]["data"]
    assert "positionSide=BOTH" in post_calls[0]["data"]
    assert "type=STOP_MARKET" in post_calls[0]["data"]
    assert "closePosition=true" in post_calls[1]["data"]
    assert "positionSide=BOTH" in post_calls[1]["data"]
    assert "type=TAKE_PROFIT_MARKET" in post_calls[1]["data"]


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
    monkeypatch.setattr(close_trade_sync.db, "clear_order_trade_details_sync_state", lambda order_id: True)
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
            "filled_at": None,
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
    monkeypatch.setattr(close_trade_sync.db, "clear_order_trade_details_sync_state", lambda order_id: True)
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
            "filled_at": None,
        },
    )]
    assert history_updates == []


def test_sync_filled_order_trade_details_retries_when_trade_fills_lag(monkeypatch):
    from trade_relay.trading import close_trade_sync

    order_updates = []
    sleep_calls = []
    trade_fill_calls = []

    class StubClient:
        def get_trade_fills(self, symbol, order_id):
            trade_fill_calls.append((symbol, order_id))
            if len(trade_fill_calls) == 1:
                return []
            return [
                {
                    "qty": "0.036",
                    "commission": "0.4446",
                    "commissionAsset": "USDC",
                    "realizedPnl": "-22.248",
                    "time": 1747751567000,
                },
            ]

    latest_order = {
        "id": 77,
        "user_id": 1,
        "username": "Will",
        "symbol": "BTCUSDC",
        "side": "SELL",
        "trade_direction": "CLOSE",
        "position_id": 9,
        "exchange_order_id": "58727847036",
        "status": "FILLED",
    }

    monkeypatch.setattr(close_trade_sync.db, "get_order_by_id", lambda order_id: latest_order if order_id == 77 else None)
    monkeypatch.setattr(
        close_trade_sync.db,
        "update_order_status",
        lambda order_id, status, **kwargs: order_updates.append((order_id, status, kwargs)) or True,
    )
    monkeypatch.setattr(close_trade_sync.db, "clear_order_trade_details_sync_state", lambda order_id: True)
    monkeypatch.setattr(close_trade_sync.db, "get_position_history", lambda user_id=None, limit=200: [])
    monkeypatch.setattr(close_trade_sync.db, "add_position_history", lambda **kwargs: 0)
    monkeypatch.setattr(close_trade_sync.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    close_trade_sync.sync_filled_order_trade_details(
        username="Will",
        client=StubClient(),
        order_row={
            "id": 77,
            "trade_direction": "CLOSE",
            "exchange_order_id": "58727847036",
            "symbol": "BTCUSDC",
        },
    )

    assert trade_fill_calls == [("BTCUSDC", "58727847036"), ("BTCUSDC", "58727847036")]
    assert sleep_calls == [0.2]
    assert order_updates == [(
        77,
        "FILLED",
        {
            "filled_qty": 0.036,
            "commission": 0.4446,
            "commission_asset": "USDC",
            "filled_at": 1747751567000,
            "realized_pnl": -22.248,
        },
    )]


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
    enqueue_calls = []

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
    monkeypatch.setattr(
        order_status_stream.db,
        "enqueue_order_close_tpsl_refresh",
        lambda order_id, **kwargs: enqueue_calls.append((order_id, kwargs)) or True,
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
    assert enqueue_calls == [(75, {"delay_seconds": 1.0, "error_message": "close_fill_inflight"})]
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
            "filled_at": None,
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
        "position_mode": "UNKNOWN",
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


def test_get_position_history_orders_by_latest_updated_at(monkeypatch):
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
    assert "ORDER BY COALESCE(updated_at, created_at) DESC, id DESC LIMIT %s" in sql
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