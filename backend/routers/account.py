"""
Account router: current-user account summary for the order form Account section.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import re
import time
from threading import Lock

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel

from backend.logger import get_logger
from backend.routers.auth import get_current_user
from trade_relay import config as cfg_module
from trade_relay.exchange.binance_client import BinanceClient

router = APIRouter(prefix="/api/account", tags=["account"])
_log = get_logger(__name__)

QUOTE_ASSETS = ("USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH")
ACCOUNT_SUMMARY_CACHE_TTL_SECONDS = 20.0
ACCOUNT_SUMMARY_RATE_LIMIT_TTL_SECONDS = 60.0
_account_summary_cache: dict[tuple[str, str | None], tuple[float, dict]] = {}
_account_summary_cache_lock = Lock()


def split_trading_symbol(symbol: str | None) -> tuple[str | None, str | None]:
    if not symbol:
        return None, None
    upper_symbol = symbol.upper()
    for quote_asset in QUOTE_ASSETS:
        if upper_symbol.endswith(quote_asset) and len(upper_symbol) > len(quote_asset):
            return upper_symbol[: -len(quote_asset)], quote_asset
    return upper_symbol, None


def _account_summary_cache_key(username: str, symbol: str | None) -> tuple[str, str | None]:
    return username, symbol.upper() if symbol else None


def _model_to_dict(model: BaseModel) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _get_cached_account_summary(username: str, symbol: str | None) -> 'AccountSummaryOut | None':
    cache_key = _account_summary_cache_key(username, symbol)
    with _account_summary_cache_lock:
        cached = _account_summary_cache.get(cache_key)
        if not cached:
            return None
        expires_at, payload = cached
        if expires_at <= time.monotonic():
            _account_summary_cache.pop(cache_key, None)
            return None
    return AccountSummaryOut(**payload)


def _account_summary_cache_ttl(summary: 'AccountSummaryOut') -> float:
    message = (summary.message or '').strip()
    if not message:
        return ACCOUNT_SUMMARY_CACHE_TTL_SECONDS

    if '-1003' not in message:
        return ACCOUNT_SUMMARY_CACHE_TTL_SECONDS

    banned_until_match = re.search(r'banned until (\d+)', message)
    if banned_until_match:
        try:
            banned_until_ms = int(banned_until_match.group(1))
            remaining_seconds = (banned_until_ms - int(time.time() * 1000)) / 1000
            if remaining_seconds > 0:
                return max(remaining_seconds, ACCOUNT_SUMMARY_RATE_LIMIT_TTL_SECONDS)
        except ValueError:
            pass

    return ACCOUNT_SUMMARY_RATE_LIMIT_TTL_SECONDS


def _set_cached_account_summary(username: str, symbol: str | None, summary: 'AccountSummaryOut') -> None:
    cache_key = _account_summary_cache_key(username, symbol)
    payload = _model_to_dict(summary)
    ttl_seconds = _account_summary_cache_ttl(summary)
    with _account_summary_cache_lock:
        _account_summary_cache[cache_key] = (time.monotonic() + ttl_seconds, payload)


def _invalidate_account_summary_cache(username: str, symbol: str | None = None) -> None:
    with _account_summary_cache_lock:
        if symbol is None:
            stale_keys = [key for key in _account_summary_cache if key[0] == username]
            for key in stale_keys:
                _account_summary_cache.pop(key, None)
            return
        _account_summary_cache.pop(_account_summary_cache_key(username, symbol), None)


class AccountSummaryOut(BaseModel):
    symbol: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None
    configured_leverage: int | None = None
    long_position_qty: float | None = None
    short_position_qty: float | None = None
    long_position_value: float | None = None
    short_position_value: float | None = None
    rest_mark_price: float | None = None
    available_balance: float | None = None
    margin_ratio: float | None = None
    risk_rate: float | None = None
    maint_margin: float | None = None
    total_equity: float | None = None
    position_value: float | None = None
    actual_leverage: float | None = None
    unrealized_pnl: float | None = None
    wallet_balance: float | None = None
    has_api_credentials: bool = False
    message: str | None = None


class LeverageUpdateIn(BaseModel):
    symbol: str
    leverage: int


@router.get("/summary", response_model=AccountSummaryOut)
def get_account_summary(
    symbol: str | None = Query(default=None),
    user: dict = Depends(get_current_user),
):
    username = user["username"]
    normalized_symbol = symbol.upper() if symbol else None
    base_asset, quote_asset = split_trading_symbol(normalized_symbol)
    api_key = cfg_module.get_api_key(username)
    api_secret = cfg_module.get_api_secret(username)
    testnet = cfg_module.is_testnet(username)

    cached_summary = _get_cached_account_summary(username, normalized_symbol)
    if cached_summary is not None:
        return cached_summary

    if not api_key or not api_secret:
        summary = AccountSummaryOut(
            symbol=normalized_symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            has_api_credentials=False,
            message="No API key configured for this account",
        )
        _set_cached_account_summary(username, normalized_symbol, summary)
        return summary

    try:
        client = BinanceClient(
            api_key=api_key,
            secret_key=api_secret,
            testnet=testnet,
        )
        account = client.get_account_info() or {}
        positions = client.get_position_information(symbol=normalized_symbol, recv_window=5000) or []

        assets = account.get("assets", []) or []
        selected_asset = None
        if quote_asset:
            selected_asset = next(
                (entry for entry in assets if str(entry.get("asset", "")).upper() == quote_asset),
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
        for position in positions:
            position_amt = float(position.get("positionAmt", 0) or 0)
            mark_price = float(position.get("markPrice", 0) or 0)
            if mark_price > 0 and rest_mark_price is None:
                rest_mark_price = mark_price
            notional = abs(float(position.get("notional", 0) or 0))
            if notional == 0.0:
                notional = abs(position_amt * mark_price)
            position_value += notional
            symbol_maint_margin += float(position.get("maintMargin", 0) or 0)
            symbol_unrealized_pnl += float(position.get("unRealizedProfit", 0) or 0)

            position_side = str(position.get("positionSide", "BOTH") or "BOTH").upper()
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

            leverage = float(position.get("leverage", 0) or 0)
            if configured_leverage is None and leverage > 0:
                configured_leverage = int(leverage)
            if leverage > 0 and notional > 0:
                leverage_notional += leverage * notional

        if normalized_symbol:
            total_maint_margin = symbol_maint_margin
            unrealized_pnl = symbol_unrealized_pnl

        margin_ratio = total_maint_margin / total_equity if total_equity > 0 else None
        actual_leverage = leverage_notional / position_value if position_value > 0 else None
        risk_rate = total_maint_margin / wallet_balance if wallet_balance > 0 else None

        summary = AccountSummaryOut(
            symbol=normalized_symbol,
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
        _set_cached_account_summary(username, normalized_symbol, summary)
        return summary
    except Exception as exc:
        _log.exception(
            "Account summary failed for user=%s symbol=%s testnet=%s",
            username,
            normalized_symbol,
            testnet,
        )
        summary = AccountSummaryOut(
            symbol=normalized_symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            has_api_credentials=True,
            message=str(exc),
        )
        _set_cached_account_summary(username, normalized_symbol, summary)
        return summary


@router.post("/leverage")
def update_account_leverage(
    body: LeverageUpdateIn,
    user: dict = Depends(get_current_user),
):
    username = user["username"]
    symbol = body.symbol.strip().upper()
    leverage = int(body.leverage)

    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol is required")
    if leverage < 1 or leverage > 125:
        raise HTTPException(status_code=400, detail="Leverage must be between 1 and 125")

    api_key = cfg_module.get_api_key(username)
    api_secret = cfg_module.get_api_secret(username)
    if not api_key or not api_secret:
        raise HTTPException(status_code=400, detail="No API key configured for this account")

    try:
        client = BinanceClient(
            api_key=api_key,
            secret_key=api_secret,
            testnet=cfg_module.is_testnet(username),
        )
        result = client.set_leverage(symbol, leverage)
        _invalidate_account_summary_cache(username, symbol)
        _log.info("Updated leverage: user=%s symbol=%s leverage=%s", username, symbol, leverage)
        return {"ok": True, "symbol": symbol, "leverage": leverage, "exchange": result}
    except HTTPException:
        raise
    except Exception as exc:
        _log.exception("Set leverage failed for user=%s symbol=%s leverage=%s", username, symbol, leverage)
        raise HTTPException(status_code=400, detail=str(exc))