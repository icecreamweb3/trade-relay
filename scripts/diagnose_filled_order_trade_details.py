#!/usr/bin/env python3
"""Diagnose why a filled order trade-detail backfill produced no changes.

Usage:
    .venv/bin/python scripts/diagnose_filled_order_trade_details.py --order-id 37
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from trade_relay.env_loader import load_env


load_env(root=ROOT, override=False)


from trade_relay import config as cfg
from trade_relay import database as db
from trade_relay.exchange.binance_client import BinanceClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose a single filled order's trade-detail backfill eligibility.",
    )
    parser.add_argument("--order-id", type=int, required=True, help="orders.id to inspect")
    parser.add_argument(
        "--show-row",
        action="store_true",
        help="Print the full order row JSON for inspection.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        row = db.get_order_by_id(args.order_id)
    except Exception as exc:
        print(f"FAIL order_id={args.order_id} reason=db_error error={exc}", file=sys.stderr)
        return 2

    if not row:
        print(f"FAIL order_id={args.order_id} reason=order_not_found", file=sys.stderr)
        return 2

    username = str(row.get("username") or "").strip()
    symbol = str(row.get("symbol") or "").upper().strip()
    exchange_order_id = str(row.get("exchange_order_id") or "").strip()
    trade_direction = str(row.get("trade_direction") or "").upper().strip()
    status = str(row.get("status") or "").upper().strip()

    print(
        "ORDER"
        f" id={args.order_id}"
        f" username={username or '<empty>'}"
        f" symbol={symbol or '<empty>'}"
        f" exchange_order_id={exchange_order_id or '<empty>'}"
        f" status={status or '<empty>'}"
        f" trade_direction={trade_direction or '<empty>'}"
    )
    print(
        "FIELDS"
        f" filled_qty={row.get('filled_qty')}"
        f" avg_price={row.get('avg_price')}"
        f" realized_pnl={row.get('realized_pnl')}"
        f" commission={row.get('commission')}"
        f" commission_asset={row.get('commission_asset')}"
    )

    if args.show_row:
        print("ROW_JSON")
        print(json.dumps(row, ensure_ascii=False, default=str, indent=2, sort_keys=True))

    if status != "FILLED":
        print(f"RESULT reason=not_filled status={status}")
        return 0
    if trade_direction not in {"OPEN", "CLOSE"}:
        print(f"RESULT reason=unsupported_trade_direction trade_direction={trade_direction or '<empty>'}")
        return 0
    if not username:
        print("RESULT reason=missing_username")
        return 0
    if not symbol:
        print("RESULT reason=missing_symbol")
        return 0
    if not exchange_order_id:
        print("RESULT reason=missing_exchange_order_id")
        return 0

    api_key = cfg.get_api_key(username)
    api_secret = cfg.get_api_secret(username)
    testnet = cfg.is_testnet(username)
    print(
        "ACCOUNT"
        f" username={username}"
        f" has_api_key={bool(api_key)}"
        f" has_api_secret={bool(api_secret)}"
        f" testnet={testnet}"
    )

    if not api_key or not api_secret:
        print("RESULT reason=no_api_credentials")
        return 0

    try:
        client = BinanceClient(api_key=api_key, secret_key=api_secret, testnet=testnet)
    except Exception as exc:
        print(f"RESULT reason=client_init_failed error={exc}")
        return 0

    try:
        trades = client.get_trade_fills(symbol, exchange_order_id)
    except Exception as exc:
        print(f"RESULT reason=get_trade_fills_error error={exc}")
        return 0

    trade_count = len(trades or [])
    print(f"TRADE_FILLS count={trade_count}")

    if not trades:
        print(
            "RESULT"
            " reason=no_trade_fills"
            " hint=backfill_filled_order_trade_details will print reason=no_change when Binance returns no userTrades for this order"
        )
        return 0

    total_qty = sum(abs(float(trade.get("qty") or 0)) for trade in trades)
    total_commission = sum(abs(float(trade.get("commission") or 0)) for trade in trades)
    total_realized_pnl = sum(float(trade.get("realizedPnl") or 0) for trade in trades)
    assets = sorted({
        str(trade.get("commissionAsset") or "").strip()
        for trade in trades
        if str(trade.get("commissionAsset") or "").strip()
    })
    commission_asset = None
    if len(assets) == 1:
        commission_asset = assets[0]
    elif assets:
        commission_asset = ",".join(assets)

    print(
        "TRADE_TOTALS"
        f" qty={total_qty}"
        f" commission={total_commission}"
        f" commission_asset={commission_asset}"
        f" realized_pnl={total_realized_pnl if trade_direction == 'CLOSE' else '<not_applicable>'}"
    )
    print(
        "COMPARE"
        f" commission_changed={row.get('commission') != total_commission}"
        f" commission_asset_changed={row.get('commission_asset') != commission_asset}"
        f" realized_pnl_changed={row.get('realized_pnl') != total_realized_pnl if trade_direction == 'CLOSE' else False}"
    )
    print("RESULT reason=trade_fills_found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())