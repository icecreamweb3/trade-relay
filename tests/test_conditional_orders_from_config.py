"""
Integration test: query account conditional orders via API key/secret
loaded from configs/users/{username}.yaml (or .env fallback).

Usage
-----
# Run for all configured users:
    pytest -s tests/test_conditional_orders_from_config.py

# Run for a specific user:
    pytest -s tests/test_conditional_orders_from_config.py --username alice

# Override credentials on the command line:
    pytest -s tests/test_conditional_orders_from_config.py \
        --api-key YOUR_KEY --api-secret YOUR_SECRET [--testnet]
"""

import sys
from pathlib import Path

import pytest

# Ensure local packages are importable when invoked via plain `pytest`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trade_relay import config as cfg
from trade_relay.exchange.binance_client import BinanceClient

# CLI options are registered in conftest.py.

# ── Fixture: resolve credentials ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def binance_credentials(request):
    """Return (api_key, api_secret, testnet) from CLI args → config file → .env."""
    cli_key    = request.config.getoption("--api-key")
    cli_secret = request.config.getoption("--api-secret")
    cli_user   = request.config.getoption("--username")
    cli_testnet = request.config.getoption("--testnet")

    # 1) CLI flags take top priority
    if cli_key and cli_secret:
        return cli_key, cli_secret, cli_testnet

    # 2) Load from config file for the named user
    if cli_user:
        usernames = [cli_user]
    else:
        # Auto-discover all user YAML files (skip admin)
        configs_dir = Path(__file__).resolve().parents[1] / "configs" / "users"
        usernames = [
            p.stem
            for p in sorted(configs_dir.glob("*.yaml"))
            if p.stem != "admin"
        ]

    for username in usernames:
        # Read directly from YAML to avoid DB connection during testing
        user_cfg   = cfg.load_user_config(username)
        api_key    = user_cfg.get("binance", {}).get("api_key") or None
        api_secret = user_cfg.get("binance", {}).get("api_secret") or None
        if api_key and api_secret:
            testnet = bool(user_cfg.get("binance", {}).get("testnet")) or cli_testnet
            print(f"\n[binance_credentials] Using config for user={username!r}, testnet={testnet}")
            return api_key, api_secret, testnet

    pytest.skip(
        "No API key/secret found. Set keys in configs/users/{username}.yaml, "
        ".env (TRADE_RELAY_BINANCE_API_KEY / TRADE_RELAY_BINANCE_API_SECRET), "
        "or pass --api-key / --api-secret on the command line."
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_query_conditional_orders_from_config(binance_credentials):
    """
    Use credentials loaded from the config file to query all
    open conditional (algo) orders for the account and print them.
    """
    api_key, api_secret, testnet = binance_credentials

    client = BinanceClient(
        api_key=api_key,
        secret_key=api_secret,
        testnet=testnet,
    )

    print(f"\n[Binance] Querying conditional orders (testnet={testnet}) …")
    result = client.get_open_algo_orders()

    print(f"[Binance] Response: {len(result)} conditional order(s) found")
    for i, order in enumerate(result, 1):
        print(
            f"  [{i}] algoId={order.get('algoId')} "
            f"symbol={order.get('symbol')} "
            f"side={order.get('side')} "
            f"type={order.get('type')} "
            f"triggerPrice={order.get('triggerPrice')} "
            f"status={order.get('algoStatus') or order.get('status')}"
        )

    # Basic structural assertions — accounts with no orders also pass
    assert isinstance(result, list)
    for order in result:
        assert "symbol"    in order, f"Missing 'symbol' in order: {order}"
        assert "side"      in order, f"Missing 'side' in order: {order}"
