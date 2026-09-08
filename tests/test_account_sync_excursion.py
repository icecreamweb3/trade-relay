from backend.routers import account as account_router
from trade_relay.exchange import account_sync


def test_account_sync_samples_every_open_symbol_while_summarizing_selected_symbol(monkeypatch):
    requested_symbols = []
    excursion_updates = []
    persisted_summaries = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def get_account_info(self):
            return {
                "assets": [{"asset": "USDC", "marginBalance": "1000", "walletBalance": "1000", "availableBalance": "900"}],
                "totalMaintMargin": "3",
                "totalMarginBalance": "1000",
                "totalWalletBalance": "1000",
                "totalUnrealizedProfit": "5",
                "availableBalance": "900",
            }

        def get_position_information(self, symbol=None, recv_window=None):
            requested_symbols.append(symbol)
            return [
                {"symbol": "BTCUSDC", "positionAmt": "0.01", "positionSide": "LONG", "markPrice": "50000", "notional": "500", "maintMargin": "2", "unRealizedProfit": "4", "leverage": "10"},
                {"symbol": "ETHUSDC", "positionAmt": "0.5", "positionSide": "LONG", "markPrice": "2500", "notional": "1250", "maintMargin": "1", "unRealizedProfit": "-3", "leverage": "5"},
            ]

        def get_position_mode(self):
            return True

    monkeypatch.setattr(account_sync, "BinanceClient", FakeClient)
    monkeypatch.setattr(account_sync.cfg_module, "get_api_key", lambda username: "key")
    monkeypatch.setattr(account_sync.cfg_module, "get_api_secret", lambda username: "secret")
    monkeypatch.setattr(account_sync.cfg_module, "is_testnet", lambda username: False)
    monkeypatch.setattr(account_sync.db_module, "get_account_summary_from_db", lambda user_id, symbol: None)
    monkeypatch.setattr(
        account_sync.db_module,
        "update_open_position_live_excursion",
        lambda **kwargs: excursion_updates.append(kwargs),
    )
    monkeypatch.setattr(
        account_sync.db_module,
        "upsert_account_summary",
        lambda user_id, symbol, summary: persisted_summaries.append((user_id, symbol, summary)),
    )
    monkeypatch.setattr(account_router, "_set_cached_account_summary", lambda *args: None)

    account_sync._fetch_and_store(5, "Will", "BTCUSDC")

    assert requested_symbols == [None]
    assert [(row["symbol"], row["unrealized_pnl"]) for row in excursion_updates] == [
        ("BTCUSDC", 4.0),
        ("ETHUSDC", -3.0),
    ]
    assert persisted_summaries[0][1] == "BTCUSDC"
    assert persisted_summaries[0][2]["long_position_qty"] == 0.01
    assert persisted_summaries[0][2]["unrealized_pnl"] == 4.0
