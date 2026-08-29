import requests
import pytest
from fastapi import HTTPException

from backend.routers import orders as orders_router


class _Response:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return self._rows


def test_get_historical_klines_normalizes_binance_rows(monkeypatch):
    calls = []
    rows = [[1_000, "10", "12", "9", "11", "123.4", 60_999]]

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(rows)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(orders_router.cfg, "is_testnet", lambda username: False)

    result = orders_router.get_historical_klines(
        symbol="btcusdc",
        interval="1m",
        start_time=1_000,
        end_time=61_000,
        user={"username": "Will"},
    )

    assert result[0].model_dump() == {
        "open_time": 1_000,
        "close_time": 60_999,
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": 11.0,
        "volume": 123.4,
    }
    assert calls[0][0] == "https://fapi.binance.com/fapi/v1/klines"
    assert calls[0][1]["params"]["symbol"] == "BTCUSDC"


def test_get_historical_klines_rejects_oversized_range():
    with pytest.raises(HTTPException) as exc_info:
        orders_router.get_historical_klines(
            symbol="BTCUSDC",
            interval="1m",
            start_time=1_000,
            end_time=1_000 + 60_000 * 5_001,
            user={"username": "Will"},
        )

    assert exc_info.value.status_code == 400
