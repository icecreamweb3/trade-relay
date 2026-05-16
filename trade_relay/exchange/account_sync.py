"""
account_sync.py — 后台账户快照同步服务

每 SYNC_INTERVAL_SECONDS 秒对所有有 API Key 的活跃用户拉取一次 Binance
账户摘要，结果写入 account_summary 表。
同时更新内存缓存（account.py 中的 _set_cached_account_summary），
使 GET /api/account/summary 可以立即从 DB 或缓存中返回，不再阻塞。
"""
import os
import sys
import time
import threading
from typing import TYPE_CHECKING

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from trade_relay import config as cfg_module
from trade_relay import database as db_module
from trade_relay.exchange.binance_client import BinanceClient

import logging
_log = logging.getLogger(__name__)

SYNC_INTERVAL_SECONDS = 15

# 启动时立即同步一次用到的 symbol 白名单，来自环境变量（逗号分隔）
# 例如 BINANCE_SYMBOL=BTCUSDC  or  ACCOUNT_SYNC_SYMBOLS=BTCUSDC,ETHUSDC
_ENV_SYMBOL = os.environ.get("BINANCE_SYMBOL", "BTCUSDC").upper()

QUOTE_ASSETS = ("USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH")


def _split_symbol(symbol: str | None) -> tuple[str | None, str | None]:
    if not symbol:
        return None, None
    upper = symbol.upper()
    for q in QUOTE_ASSETS:
        if upper.endswith(q) and len(upper) > len(q):
            return upper[: -len(q)], q
    return upper, None


def _fetch_and_store(user_id: int, username: str, symbol: str | None) -> None:
    """拉取账户摘要并写入 DB + 内存缓存。"""
    # 延迟导入避免循环依赖
    from backend.routers.account import (
        AccountSummaryOut,
        _set_cached_account_summary,
        split_trading_symbol,
    )

    normalized = symbol.upper() if symbol else None
    base_asset, quote_asset = split_trading_symbol(normalized)
    _log.info("[ACCOUNT_SYNC] phase=fetch_start user_id=%s username=%s symbol=%s", user_id, username, normalized)

    api_key = cfg_module.get_api_key(username)
    api_secret = cfg_module.get_api_secret(username)
    testnet = cfg_module.is_testnet(username)

    if not api_key or not api_secret:
        _log.warning("[ACCOUNT_SYNC] phase=missing_credentials user_id=%s username=%s symbol=%s", user_id, username, normalized)
        summary = AccountSummaryOut(
            symbol=normalized,
            base_asset=base_asset,
            quote_asset=quote_asset,
            has_api_credentials=False,
            message="No API key configured for this account",
        )
        _persist(user_id, username, normalized, summary)
        return

    try:
        client = BinanceClient(api_key=api_key, secret_key=api_secret, testnet=testnet)
        account = client.get_account_info() or {}
        positions = client.get_position_information(symbol=normalized, recv_window=5000) or []

        assets = account.get("assets", []) or []
        selected_asset = None
        if quote_asset:
            selected_asset = next(
                (e for e in assets if str(e.get("asset", "")).upper() == quote_asset),
                None,
            )

        total_maint_margin = float(account.get("totalMaintMargin", 0) or 0)
        total_equity = float(account.get("totalMarginBalance", 0) or 0)
        wallet_balance = float(account.get("totalWalletBalance", 0) or 0)
        unrealized_pnl = float(account.get("totalUnrealizedProfit", 0) or 0)
        available_balance = float(account.get("availableBalance", 0) or 0)

        if selected_asset is not None:
            total_equity = float(selected_asset.get("marginBalance", 0) or 0)
            wallet_balance = float(selected_asset.get("walletBalance", 0) or 0)
            available_balance = float(selected_asset.get("availableBalance", 0) or 0)

        position_value = 0.0
        symbol_maint_margin = 0.0
        symbol_unrealized_pnl = 0.0
        leverage_notional = 0.0
        configured_leverage = None
        long_position_qty = 0.0
        short_position_qty = 0.0
        long_position_value = 0.0
        short_position_value = 0.0
        rest_mark_price: float | None = None

        for pos in positions:
            position_amt = float(pos.get("positionAmt", 0) or 0)
            mark_price = float(pos.get("markPrice", 0) or 0)
            if mark_price > 0 and rest_mark_price is None:
                rest_mark_price = mark_price
            notional = abs(float(pos.get("notional", 0) or 0))
            if notional == 0.0:
                notional = abs(position_amt * mark_price)
            position_value += notional
            symbol_maint_margin += float(pos.get("maintMargin", 0) or 0)
            symbol_unrealized_pnl += float(pos.get("unRealizedProfit", 0) or 0)

            position_side = str(pos.get("positionSide", "BOTH") or "BOTH").upper()
            if position_side == "LONG":
                long_position_qty += abs(position_amt)
                long_position_value += notional
            elif position_side == "SHORT":
                short_position_qty += abs(position_amt)
                short_position_value += notional
            elif position_amt > 0:
                long_position_qty += position_amt
                long_position_value += notional
            elif position_amt < 0:
                short_position_qty += abs(position_amt)
                short_position_value += notional

            leverage = float(pos.get("leverage", 0) or 0)
            if configured_leverage is None and leverage > 0:
                configured_leverage = int(leverage)
            if leverage > 0 and notional > 0:
                leverage_notional += leverage * notional

        if normalized:
            total_maint_margin = symbol_maint_margin
            unrealized_pnl = symbol_unrealized_pnl

        margin_ratio = total_maint_margin / total_equity if total_equity > 0 else None
        actual_leverage = leverage_notional / position_value if position_value > 0 else None
        risk_rate = total_maint_margin / wallet_balance if wallet_balance > 0 else None

        summary = AccountSummaryOut(
            symbol=normalized,
            base_asset=base_asset,
            quote_asset=quote_asset,
            configured_leverage=configured_leverage,
            long_position_qty=long_position_qty,
            short_position_qty=short_position_qty,
            long_position_value=long_position_value if long_position_value > 0 else None,
            short_position_value=short_position_value if short_position_value > 0 else None,
            rest_mark_price=rest_mark_price,
            available_balance=available_balance,
            margin_ratio=margin_ratio,
            risk_rate=risk_rate,
            maint_margin=total_maint_margin,
            total_equity=total_equity,
            position_value=position_value,
            actual_leverage=actual_leverage,
            unrealized_pnl=unrealized_pnl,
            wallet_balance=wallet_balance,
            has_api_credentials=True,
        )
        _persist(user_id, username, normalized, summary)
        _log.info("[ACCOUNT_SYNC] phase=fetch_success user_id=%s username=%s symbol=%s", user_id, username, normalized)

    except Exception:
        _log.exception("[ACCOUNT_SYNC] phase=fetch_error user=%s symbol=%s", username, normalized)


def _persist(user_id: int, username: str, symbol: str | None, summary: "AccountSummaryOut") -> None:
    """写入内存缓存和 DB。"""
    from backend.routers.account import _set_cached_account_summary
    _set_cached_account_summary(username, symbol, summary)
    try:
        if hasattr(summary, "model_dump"):
            data = summary.model_dump()
        else:
            data = summary.dict()
        db_module.upsert_account_summary(user_id, symbol, data)
        _log.info("[ACCOUNT_SYNC] phase=persist_success user_id=%s username=%s symbol=%s has_message=%s", user_id, username, symbol, bool(summary.message))
    except Exception:
        _log.exception("[ACCOUNT_SYNC] phase=persist_error user=%s symbol=%s", username, symbol)


# ── 后台线程 ──────────────────────────────────────────────────────────────────

_stop_event = threading.Event()
_sync_thread: threading.Thread | None = None


def _sync_loop() -> None:
    _log.info("[ACCOUNT_SYNC] phase=thread_start interval_seconds=%s", SYNC_INTERVAL_SECONDS)
    # 第一次立即执行
    _run_once()
    while not _stop_event.wait(timeout=SYNC_INTERVAL_SECONDS):
        _run_once()
    _log.info("[ACCOUNT_SYNC] phase=thread_stop")


def _run_once() -> None:
    """对所有有 API key 的活跃用户同步账户摘要。"""
    try:
        users = db_module.get_all_active_users_with_api_keys()
    except Exception:
        _log.exception("[ACCOUNT_SYNC] phase=query_users_error")
        return

    # 当前默认同步主 symbol（BTCUSDC 等），后续可扩展为每个用户最近使用的 symbol
    symbol = _ENV_SYMBOL
    _log.info("[ACCOUNT_SYNC] phase=run_once users=%s symbol=%s", len(users), symbol)
    for row in users:
        user_id = row["id"]
        username = row["username"]
        try:
            _fetch_and_store(user_id, username, symbol)
        except Exception:
            _log.exception("[ACCOUNT_SYNC] phase=unhandled_error user=%s", username)


def start_account_sync() -> None:
    """启动后台同步线程（幂等，重复调用无影响）。"""
    global _sync_thread
    if _sync_thread and _sync_thread.is_alive():
        return
    _stop_event.clear()
    _sync_thread = threading.Thread(target=_sync_loop, name="account-sync", daemon=True)
    _sync_thread.start()


def stop_account_sync() -> None:
    """通知后台线程退出。"""
    _stop_event.set()
    if _sync_thread:
        _sync_thread.join(timeout=5)
