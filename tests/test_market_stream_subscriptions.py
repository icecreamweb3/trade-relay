"""Integration tests for backend-managed Binance public market streams.

Usage
-----
    pytest -s -m integration tests/test_market_stream_subscriptions.py
"""

import sys
import threading
from pathlib import Path

import pytest

# Ensure local packages are importable when invoked via plain `pytest`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trade_relay.exchange.public_mark_price_stream import PublicMarkPriceStream
from trade_relay.exchange.public_ticker_stream import PublicTicker24hStream


def _recv_first_payload(stream_factory, timeout_seconds: float = 12.0) -> tuple[dict, object]:
    stream = stream_factory()

    payload_box: dict[str, object] = {}
    done = threading.Event()

    def on_payload(payload: dict) -> None:
        payload_box["payload"] = payload
        done.set()

    stream.add_listener(on_payload)
    stream.start()

    try:
        if not done.wait(timeout_seconds):
            pytest.fail(
                f"Timed out waiting for first websocket payload from {stream.__class__.__name__}; "
                f"connected={getattr(stream, 'connected', None)} "
                f"reconnecting={getattr(stream, 'reconnecting', None)} "
                f"reconnect_count={getattr(stream, 'reconnect_count', None)}"
            )
    finally:
        stream.remove_listener(on_payload)
        stream.stop()

    payload = payload_box.get("payload")
    assert isinstance(payload, dict), f"Expected dict payload, got: {type(payload)!r}"
    return payload, stream


@pytest.mark.integration
@pytest.mark.parametrize(
    ("stream_factory", "expected_type", "expected_symbol"),
    [
        (lambda: PublicTicker24hStream("BTCUSDC"), "ticker24h", "BTCUSDC"),
        (lambda: PublicMarkPriceStream("BTCUSDC"), "markPrice", "BTCUSDC"),
    ],
)
def test_backend_can_subscribe_public_market_streams(
    stream_factory,
    expected_type: str,
    expected_symbol: str,
):
    payload, _stream = _recv_first_payload(stream_factory)

    assert payload.get("type") == expected_type, payload
    assert payload.get("symbol") == expected_symbol, payload

    if expected_type == "ticker24h":
        assert float(payload["lastPrice"]) > 0, payload
        assert payload.get("priceChangePercent") is not None, payload
        assert payload.get("openPrice") is not None, payload
    else:
        assert float(payload["markPrice"]) > 0, payload
        assert payload.get("indexPrice") is not None, payload
        assert payload.get("nextFundingTime") is not None, payload