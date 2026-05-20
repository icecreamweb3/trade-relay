"""
Account router: current-user account summary for the order form Account section.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import asyncio
import re
import time
from threading import Lock

import requests

from fastapi import APIRouter, Depends, Query, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

from backend.logger import get_logger
from backend.routers.auth import decode_token, get_current_user
from trade_relay import config as cfg_module
from trade_relay import database as db_module
from trade_relay.exchange.binance_client import BinanceClient
from trade_relay.exchange.public_ticker_stream import register_public_ticker_listener, unregister_public_ticker_listener

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
    _log.info(
        "[ACCOUNT_SUMMARY] phase=cache_set username=%s symbol=%s ttl_seconds=%s has_message=%s has_api_credentials=%s",
        username,
        symbol,
        round(ttl_seconds, 2),
        bool(summary.message),
        summary.has_api_credentials,
    )


def _invalidate_account_summary_cache(username: str, symbol: str | None = None) -> None:
    with _account_summary_cache_lock:
        if symbol is None:
            stale_keys = [key for key in _account_summary_cache if key[0] == username]
            for key in stale_keys:
                _account_summary_cache.pop(key, None)
            return
        _account_summary_cache.pop(_account_summary_cache_key(username, symbol), None)


def _refresh_account_summary_from_exchange(user_id: int, username: str, symbol: str | None) -> dict:
    from trade_relay.exchange.account_sync import _fetch_and_store

    _fetch_and_store(user_id, username, symbol)
    return db_module.get_account_summary_from_db(user_id, symbol) or {}


class AccountSummaryOut(BaseModel):
    symbol: str | None = None
    base_asset: str | None = None
    quote_asset: str | None = None
    position_mode: str | None = None
    leverage: int | None = None
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


@router.websocket("/ticker24h/ws")
async def ticker24h_ws(
    websocket: WebSocket,
    symbol: str = Query(..., min_length=1),
    token: str | None = Query(default=None),
):
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")
        return

    user = decode_token(token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or expired token")
        return

    normalized_symbol = symbol.upper()
    await websocket.accept()

    queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def listener(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    register_public_ticker_listener(normalized_symbol, listener)

    try:
        await websocket.send_json({"type": "connected", "symbol": normalized_symbol})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping", "symbol": normalized_symbol})
                continue
            await websocket.send_json(event)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        unregister_public_ticker_listener(normalized_symbol, listener)


class LeverageUpdateIn(BaseModel):
    symbol: str
    leverage: int


class PositionModeUpdateIn(BaseModel):
    symbol: str
    position_mode: str


def _normalize_position_mode(value: str | None) -> str:
    normalized = str(value or '').strip().upper()
    if normalized in {'DUAL', 'HEDGE'}:
        return 'DUAL'
    if normalized in {'SINGLE', 'ONE_WAY', 'ONEWAY'}:
        return 'SINGLE'
    raise HTTPException(status_code=400, detail='Position mode must be SINGLE or DUAL')


class OrderBookDepthOut(BaseModel):
    lastUpdateId: int | None = None
    bids: list[list[str]] = []
    asks: list[list[str]] = []


def _admin_account_summary(symbol: str | None) -> AccountSummaryOut:
    base_asset, quote_asset = split_trading_symbol(symbol)
    return AccountSummaryOut(
        symbol=symbol,
        base_asset=base_asset,
        quote_asset=quote_asset,
        position_mode=None,
        leverage=None,
        has_api_credentials=False,
        message=None,
    )


@router.get("/order-book-depth", response_model=OrderBookDepthOut)
def get_order_book_depth(
    symbol: str = Query(..., min_length=1),
    limit: int = Query(default=1000, ge=5, le=1000),
):
    normalized_symbol = symbol.upper()
    try:
        response = requests.get(
            "https://fapi.binance.com/fapi/v1/depth",
            params={"symbol": normalized_symbol, "limit": limit},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            },
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch Binance order book depth for {normalized_symbol}: {exc}") from exc

    return OrderBookDepthOut(
        lastUpdateId=payload.get("lastUpdateId"),
        bids=payload.get("bids") or [],
        asks=payload.get("asks") or [],
    )


@router.get("/summary", response_model=AccountSummaryOut)
def get_account_summary(
    symbol: str | None = Query(default=None),
    force: bool = Query(default=False),
    user: dict = Depends(get_current_user),
):
    username = user["username"]
    user_id = int(user["sub"])
    normalized_symbol = symbol.upper() if symbol else None
    _log.info("[ACCOUNT_SUMMARY] phase=request username=%s user_id=%s symbol=%s force=%s", username, user_id, normalized_symbol, force)
    if user.get("role") == "admin":
        _invalidate_account_summary_cache(username, normalized_symbol)
        _log.info("[ACCOUNT_SUMMARY] phase=admin_bypass username=%s symbol=%s", username, normalized_symbol)
        return _admin_account_summary(normalized_symbol)

    base_asset, quote_asset = split_trading_symbol(normalized_symbol)
    api_key = cfg_module.get_api_key(username)
    api_secret = cfg_module.get_api_secret(username)

    if force:
        _invalidate_account_summary_cache(username, normalized_symbol)
        _log.info("[ACCOUNT_SUMMARY] phase=cache_invalidate username=%s symbol=%s reason=force", username, normalized_symbol)
    else:
        # 1. 先查内存缓存（最快）
        cached = _get_cached_account_summary(username, normalized_symbol)
        if cached is not None:
            _log.info("[ACCOUNT_SUMMARY] phase=cache_hit username=%s symbol=%s", username, normalized_symbol)
            return cached
        _log.info("[ACCOUNT_SUMMARY] phase=cache_miss username=%s symbol=%s", username, normalized_symbol)

        # 2. 再查 DB 快照（毫秒级，后台同步服务负责刷新）
        db_row = db_module.get_account_summary_from_db(user_id, normalized_symbol)
        if db_row is not None:
            _log.info("[ACCOUNT_SUMMARY] phase=db_hit username=%s user_id=%s symbol=%s", username, user_id, normalized_symbol)
            # 回填内存缓存，ttl 设为 SYNC_INTERVAL_SECONDS 对齐同步周期
            summary = AccountSummaryOut(**{
                k: db_row[k]
                for k in AccountSummaryOut.model_fields
                if k in db_row
            })
            with _account_summary_cache_lock:
                from trade_relay.exchange.account_sync import SYNC_INTERVAL_SECONDS
                _account_summary_cache[
                    _account_summary_cache_key(username, normalized_symbol)
                ] = (time.monotonic() + SYNC_INTERVAL_SECONDS, _model_to_dict(summary))
            return summary
        _log.info("[ACCOUNT_SUMMARY] phase=db_miss username=%s user_id=%s symbol=%s", username, user_id, normalized_symbol)

    # 3. DB 无数据（首次）或 force=True：同步调用 Binance，并写入 DB + 缓存
    if not api_key or not api_secret:
        _log.warning("[ACCOUNT_SUMMARY] phase=missing_credentials username=%s symbol=%s", username, normalized_symbol)
        summary = AccountSummaryOut(
            symbol=normalized_symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            has_api_credentials=False,
            message="No API key configured for this account",
        )
        _set_cached_account_summary(username, normalized_symbol, summary)
        return summary

    testnet = cfg_module.is_testnet(username)
    try:
        _log.info("[ACCOUNT_SUMMARY] phase=binance_fetch username=%s symbol=%s testnet=%s", username, normalized_symbol, testnet)
        persisted_summary = db_module.get_account_summary_from_db(user_id, normalized_symbol) or {}
        client = BinanceClient(
            api_key=api_key,
            secret_key=api_secret,
            testnet=testnet,
        )
        account = client.get_account_info() or {}
        positions = client.get_position_information(symbol=normalized_symbol, recv_window=5000) or []
        dual_side_position = client.get_position_mode()
        position_mode = 'DUAL' if dual_side_position is True else 'SINGLE'

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
            position_mode=position_mode,
            leverage=int(persisted_summary["leverage"]) if persisted_summary.get("leverage") is not None else configured_leverage,
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
        db_module.upsert_account_summary(user_id, normalized_symbol, _model_to_dict(summary))
        _log.info("[ACCOUNT_SUMMARY] phase=binance_fetch_success username=%s user_id=%s symbol=%s", username, user_id, normalized_symbol)
        return summary
    except Exception as exc:
        _log.exception(
            "[ACCOUNT_SUMMARY] phase=binance_fetch_error user=%s symbol=%s testnet=%s",
            username, normalized_symbol, testnet,
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
        existing_summary = db_module.get_account_summary_from_db(int(user["sub"]), symbol) or {}
        merged_summary = {
            **existing_summary,
            "symbol": symbol,
            "leverage": leverage,
        }
        user_id = int(user["sub"])
        db_module.upsert_account_summary(user_id, symbol, merged_summary)
        refreshed_summary = _refresh_account_summary_from_exchange(user_id, username, symbol)
        _invalidate_account_summary_cache(username, None)
        _log.info("[ACCOUNT_SUMMARY] phase=leverage_update user=%s symbol=%s leverage=%s", username, symbol, leverage)
        return {
            "ok": True,
            "symbol": symbol,
            "leverage": refreshed_summary.get("leverage", leverage),
            "configured_leverage": refreshed_summary.get("configured_leverage"),
            "position_mode": refreshed_summary.get("position_mode"),
            "exchange": result,
        }
    except HTTPException:
        raise
    except Exception as exc:
        _log.exception("[ACCOUNT_SUMMARY] phase=leverage_update_error user=%s symbol=%s leverage=%s", username, symbol, leverage)
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/position-mode")
def update_account_position_mode(
    body: PositionModeUpdateIn,
    user: dict = Depends(get_current_user),
):
    username = user["username"]
    symbol = body.symbol.strip().upper()
    position_mode = _normalize_position_mode(body.position_mode)

    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol is required")

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
        hedge_mode = position_mode == 'DUAL'
        result = client.set_position_mode(hedge_mode)
        user_id = int(user["sub"])
        refreshed_summary = _refresh_account_summary_from_exchange(user_id, username, symbol)
        _invalidate_account_summary_cache(username, None)
        _log.info("[ACCOUNT_SUMMARY] phase=position_mode_update user=%s symbol=%s position_mode=%s", username, symbol, position_mode)
        return {
            "ok": True,
            "symbol": symbol,
            "position_mode": refreshed_summary.get("position_mode", position_mode),
            "leverage": refreshed_summary.get("leverage"),
            "configured_leverage": refreshed_summary.get("configured_leverage"),
            "exchange": result,
        }
    except HTTPException:
        raise
    except Exception as exc:
        _log.exception("[ACCOUNT_SUMMARY] phase=position_mode_update_error user=%s symbol=%s position_mode=%s", username, symbol, position_mode)
        raise HTTPException(status_code=400, detail=str(exc))


_mark_price_cache: dict[str, tuple[float, float]] = {}  # symbol → (timestamp, price)
_MARK_PRICE_CACHE_TTL = 3.0  # seconds


@router.get("/mark-price")
def get_mark_price(symbol: str = Query(), user: dict = Depends(get_current_user)):
    """
    Return the current mark price for a symbol using Binance public API (no API key required).
    Cached for a few seconds to avoid hammering the exchange.
    """
    symbol = symbol.strip().upper()
    now = time.time()
    cached = _mark_price_cache.get(symbol)
    if cached and now - cached[0] < _MARK_PRICE_CACHE_TTL:
        return {"symbol": symbol, "mark_price": cached[1]}

    testnet = cfg_module.is_testnet(user["username"])
    base_url = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
    try:
        import requests as _requests
        proxy_cfg = None
        proxy_url = os.environ.get("ALL_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        if proxy_url:
            proxy_cfg = {"http": proxy_url, "https": proxy_url}
        resp = _requests.get(
            f"{base_url}/fapi/v1/premiumIndex",
            params={"symbol": symbol},
            proxies=proxy_cfg,
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        price = float(data.get("markPrice") or data.get("price") or 0)
        _mark_price_cache[symbol] = (now, price)
        return {"symbol": symbol, "mark_price": price}
    except Exception as exc:
        _log.warning("get_mark_price failed for symbol=%s: %s", symbol, exc)
        raise HTTPException(status_code=502, detail=f"Could not fetch mark price: {exc}")