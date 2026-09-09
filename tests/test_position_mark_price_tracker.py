import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trade_relay.exchange import position_mark_price_tracker as tracker


def _reset_tracker_state():
    with tracker._lock:
        tracker._positions_by_user.clear()
        tracker._listeners_by_symbol.clear()


def test_tracks_each_open_position_symbol_and_persists_new_extrema(monkeypatch):
    _reset_tracker_state()
    listeners = {}
    removed = []
    updates = []

    monkeypatch.setattr(
        tracker.db_module,
        "get_positions",
        lambda **kwargs: [
            {"symbol": "BTCUSDC", "position_side": "LONG", "live_mfe_usdc": 5, "live_mae_usdc": 2},
            {"symbol": "ETHUSDC", "position_side": "SHORT", "live_mfe_usdc": 4, "live_mae_usdc": 3},
        ],
    )
    monkeypatch.setattr(tracker, "register_public_mark_price_listener", lambda symbol, listener: listeners.setdefault(symbol, listener))
    monkeypatch.setattr(tracker, "unregister_public_mark_price_listener", lambda symbol, listener: removed.append(symbol))
    monkeypatch.setattr(
        tracker.db_module,
        "update_open_position_live_excursion",
        lambda **kwargs: updates.append(kwargs) or True,
    )

    tracker.replace_user_positions(7, [
        {"symbol": "BTCUSDC", "positionSide": "LONG", "positionAmt": "2", "entryPrice": "100"},
        {"symbol": "ETHUSDC", "positionSide": "SHORT", "positionAmt": "-3", "entryPrice": "200"},
    ])

    assert set(listeners) == {"BTCUSDC", "ETHUSDC"}
    listeners["BTCUSDC"]({"markPrice": 110})
    listeners["ETHUSDC"]({"markPrice": 190})
    assert [(row["symbol"], row["position_side"], row["unrealized_pnl"]) for row in updates] == [
        ("BTCUSDC", "LONG", 20),
        ("ETHUSDC", "SHORT", 30),
    ]

    # A price inside the already persisted extrema should not produce a DB write.
    listeners["BTCUSDC"]({"markPrice": 101})
    assert len(updates) == 2

    tracker.replace_user_positions(7, [
        {"symbol": "BTCUSDC", "positionSide": "LONG", "positionAmt": "2", "entryPrice": "100"},
    ])
    assert removed == ["ETHUSDC"]

    tracker.stop_position_mark_price_tracking()


def test_one_symbol_stream_is_shared_across_users(monkeypatch):
    _reset_tracker_state()
    registered = []
    monkeypatch.setattr(tracker.db_module, "get_positions", lambda **kwargs: [])
    monkeypatch.setattr(tracker, "register_public_mark_price_listener", lambda symbol, listener: registered.append(symbol))
    monkeypatch.setattr(tracker, "unregister_public_mark_price_listener", lambda *args: None)

    payload = [{"symbol": "BTCUSDC", "positionSide": "LONG", "positionAmt": "1", "entryPrice": "100"}]
    tracker.replace_user_positions(1, payload)
    tracker.replace_user_positions(2, payload)

    assert registered == ["BTCUSDC"]
    tracker.stop_position_mark_price_tracking()
