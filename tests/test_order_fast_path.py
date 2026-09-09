import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trade_relay.auth.manager import Session
from trade_relay.exchange import binance_client as exchange_client
from trade_relay.trading import binance_client as trading_client
from trade_relay.trading import order_manager


def test_submit_transport_failures_are_treated_as_ambiguous():
    assert exchange_client._is_ambiguous_submit_exception(TimeoutError("timed out")) is True
    assert exchange_client._is_ambiguous_submit_exception(Exception("HTTP 503: unavailable")) is True
    assert exchange_client._is_ambiguous_submit_exception(Exception("APIError(code=-1111): bad precision")) is False


def test_reuses_client_and_trusts_supplied_position_mode(monkeypatch):
    calls = {"init": 0, "mode_lookup": 0, "orders": []}

    class StubClient:
        def __init__(self, api_key, secret_key, testnet):
            calls["init"] += 1

        def remember_position_mode(self, hedge_mode):
            return None

        def get_position_mode(self):
            calls["mode_lookup"] += 1
            return True

        def set_leverage(self, symbol, leverage):
            return None

        def place_market_order(
            self,
            symbol,
            side,
            quantity,
            position_side=None,
            reduce_only=False,
            client_order_id=None,
        ):
            calls["orders"].append(client_order_id)
            return {"orderId": str(len(calls["orders"])), "status": "NEW"}

    monkeypatch.setattr(trading_client, "FuturesBinanceClient", StubClient)
    trading_client._client_cache.clear()

    async def submit_twice():
        for _ in range(2):
            result = await trading_client.place_order(
                api_key="key",
                api_secret="secret",
                symbol="BTCUSDC",
                side="BUY",
                order_type="MARKET",
                quantity=0.01,
                position_mode="DUAL",
            )
            assert result.success is True

    asyncio.run(submit_twice())

    assert calls["init"] == 1
    assert calls["mode_lookup"] == 0
    assert len(set(calls["orders"])) == 2
    assert all(value.startswith("tr_") for value in calls["orders"])


def test_symbol_precision_is_cached(monkeypatch):
    calls = []

    class RawClient:
        def futures_exchange_info(self):
            calls.append(True)
            return {
                "symbols": [{
                    "symbol": "ETHUSDC",
                    "baseAssetPrecision": 8,
                    "quotePrecision": 8,
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.01", "minPrice": "0.01", "maxPrice": "100000"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                    ],
                }],
            }

    client = exchange_client.BinanceClient.__new__(exchange_client.BinanceClient)
    client.client = RawClient()
    client.base_url = "https://fapi.binance.com"
    client.testnet = False
    exchange_client._symbol_precision_cache.clear()

    first = client.get_symbol_precision_info("ETHUSDC")
    second = client.get_symbol_precision_info("ETHUSDC")

    assert first == second
    assert len(calls) == 1


def _patch_order_manager_dependencies(monkeypatch, exchange_result, captured):
    async def fake_place_order(**kwargs):
        return exchange_result

    monkeypatch.setattr(order_manager.cfg, "is_mock_mode", lambda username: False)
    monkeypatch.setattr(order_manager.cfg, "get_api_key", lambda username: "key")
    monkeypatch.setattr(order_manager.cfg, "get_api_secret", lambda username: "secret")
    monkeypatch.setattr(order_manager.cfg, "is_testnet", lambda username: False)
    monkeypatch.setattr(order_manager, "place_order", fake_place_order)
    monkeypatch.setattr(order_manager.db, "get_position", lambda *args, **kwargs: None)
    monkeypatch.setattr(order_manager.db, "get_order_by_exchange_id", lambda *args, **kwargs: None)
    monkeypatch.setattr(order_manager.db, "get_all_orders_by_exchange_id", lambda *args, **kwargs: [])
    monkeypatch.setattr(order_manager.db, "log_operation", lambda *args, **kwargs: None)

    def create_order(**kwargs):
        captured["created"] = kwargs
        return 77

    monkeypatch.setattr(order_manager.db, "create_order", create_order)


def test_post_submit_sync_is_scheduled_off_response_path(monkeypatch):
    captured = {}
    result = trading_client.BinanceOrderResult(
        success=True,
        order_id="12345",
        client_order_id="tr_test",
        status="NEW",
    )
    _patch_order_manager_dependencies(monkeypatch, result, captured)
    monkeypatch.setattr(
        order_manager,
        "_schedule_post_submit_sync",
        lambda *args: captured.setdefault("post_sync", args),
    )

    response = asyncio.run(order_manager.submit_order(
        Session(1, "Will", "user"),
        "BTCUSDC",
        "BUY",
        "LIMIT",
        0.01,
        80000.0,
        leverage=20,
        position_mode="DUAL",
    ))

    assert response.success is True
    assert captured["post_sync"][-1] == "12345"


def test_ambiguous_timeout_is_persisted_as_pending(monkeypatch):
    captured = {}
    result = trading_client.BinanceOrderResult(
        success=False,
        client_order_id="tr_timeout",
        status="FAILED",
        error="Read timed out",
        uncertain=True,
    )
    _patch_order_manager_dependencies(monkeypatch, result, captured)
    monkeypatch.setattr(
        order_manager,
        "_schedule_uncertain_confirmation",
        lambda *args: captured.setdefault("confirmation", args),
    )

    response = asyncio.run(order_manager.submit_order(
        Session(1, "Will", "user"),
        "BTCUSDC",
        "BUY",
        "MARKET",
        0.01,
        leverage=20,
        position_mode="DUAL",
    ))

    assert response.success is True
    assert response.pending_confirmation is True
    assert captured["created"]["status"] == "PENDING"
    assert captured["created"]["client_order_id"] == "tr_timeout"
    assert captured["confirmation"][-1] == "tr_timeout"
