"""
Database management - MySQL via PyMySQL.

Connection parameters are read from environment variables (prefer .env.production, fallback .env):
  TRADE_RELAY_MYSQL_HOST      default: 127.0.0.1
  TRADE_RELAY_MYSQL_PORT      default: 3306
  TRADE_RELAY_MYSQL_USER      default: trade_relay
  TRADE_RELAY_MYSQL_PASSWORD  default: (empty)
  TRADE_RELAY_MYSQL_DATABASE  default: trade_relay

Tables:
  users           – 用户信息
  orders          – 委托记录（当前委托 + 历史订单，通过 status 区分）
  positions       – 头寸信息
  operation_logs  – 操作日志
"""
import base64
import logging
import os
from decimal import Decimal
from queue import Empty, Full, Queue
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from threading import Condition, Event, RLock, Thread
from typing import Optional
from urllib.parse import unquote, urlparse

import pymysql
import pymysql.cursors
import pymysql.err
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


logger = logging.getLogger(__name__)

_ACCOUNT_BALANCE_MISSING = object()

_MYSQL_SESSION_UTC_SQL = "SET time_zone = '+00:00'"

_PYMYSQL_SOCKET_PATCH_LOCK = RLock()
_MYSQL_PROXY_SCHEME_NAMES = frozenset({"socks5", "socks5h", "socks4", "socks4a", "http", "https"})

_INCOME_HISTORY_CORE_TYPES = frozenset({"REALIZED_PNL", "COMMISSION", "FUNDING_FEE"})

_DB_LOG_REDACT_KEYS = frozenset({
    "password_hash",
    "binance_api_key",
    "binance_api_secret",
    "enc_key",
    "enc_secret",
})

_OP_LOG_STOP = object()
_OP_LOG_WORKER_LOCK = RLock()
_op_log_queue: Optional[Queue] = None
_op_log_worker: Optional[Thread] = None
_op_log_stop_event = Event()


def _sanitize_db_log_value(key: str, value):
    if key in _DB_LOG_REDACT_KEYS:
        return "<redacted>" if value else None
    return value


def _sanitize_db_log_fields(fields: dict) -> dict:
    return {key: _sanitize_db_log_value(key, value) for key, value in fields.items()}


def _should_skip_db_log(db_action: str, table: str) -> bool:
    return table == "positions" and db_action == "delete"


def _log_db_write(db_action: str, table: str, fields: dict) -> None:
    if _should_skip_db_log(db_action, table):
        return
    logger.info(
        "[DB_WRITE] phase=request action=%s table=%s fields=%s",
        db_action,
        table,
        _sanitize_db_log_fields(fields),
    )


def _log_db_write_result(db_action: str, table: str, **result) -> None:
    if _should_skip_db_log(db_action, table):
        return
    logger.info(
        "[DB_WRITE] phase=result action=%s table=%s result=%s",
        db_action,
        table,
        _sanitize_db_log_fields(result),
    )


def _log_db_query(table: str, query_name: str, **details) -> None:
    logger.info(
        "[DB_QUERY] phase=request table=%s query=%s details=%s",
        table,
        query_name,
        _sanitize_db_log_fields(details),
    )


def _log_db_query_result(table: str, query_name: str, **details) -> None:
    logger.info(
        "[DB_QUERY] phase=result table=%s query=%s details=%s",
        table,
        query_name,
        _sanitize_db_log_fields(details),
    )


@lru_cache(maxsize=1)
def _operation_log_queue_size() -> int:
    raw_value = (os.environ.get("TRADE_RELAY_OPERATION_LOG_QUEUE_SIZE") or "1000").strip()
    try:
        return max(1, int(raw_value))
    except ValueError:
        logger.warning(
            "Invalid TRADE_RELAY_OPERATION_LOG_QUEUE_SIZE=%r, falling back to 1000",
            raw_value,
        )
        return 1000


# ──────────────────────────────────────────────
# 活跃订单状态集合（当前委托）
# ──────────────────────────────────────────────
ACTIVE_STATUSES = frozenset({"NEW", "PARTIALLY_FILLED", "PENDING_CANCEL", "PENDING"})


# ──────────────────────────────────────────────
# API 凭证加密 / 解密（Fernet / AES-128-CBC）
# ──────────────────────────────────────────────
_FERNET_SALT = b"trade_relay_v1_api_keys_salt_2024"


def _get_fernet() -> Fernet:
    """Derive a stable Fernet key from TRADE_RELAY_ENCRYPTION_KEY env var (preferred)
    or fall back to the DB password so the key is tied to the deployment."""
    key_material = (
        os.environ.get("TRADE_RELAY_ENCRYPTION_KEY")
        or os.environ.get("TRADE_RELAY_MYSQL_PASSWORD", "trade_relay_default_enc_key")
    )
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_FERNET_SALT,
        iterations=100_000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(key_material.encode("utf-8")))
    return Fernet(key)


def encrypt_api_credential(value: str) -> str:
    """Encrypt a plaintext API key/secret for database storage."""
    if not value:
        return ""
    return _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_api_credential(token: str) -> str:
    """Decrypt a stored API key/secret. Returns empty string on failure."""
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


def _mysql_cfg() -> dict:
    """Build PyMySQL connection kwargs from environment variables."""
    return {
        "host":        os.environ.get("TRADE_RELAY_MYSQL_HOST") or os.environ.get("DB_HOST", "127.0.0.1"),
        "port":        int(os.environ.get("TRADE_RELAY_MYSQL_PORT") or os.environ.get("DB_PORT", "3306")),
        "user":        os.environ.get("TRADE_RELAY_MYSQL_USER") or os.environ.get("DB_USER", "trade_relay"),
        "password":    os.environ.get("TRADE_RELAY_MYSQL_PASSWORD") or os.environ.get("DB_PASSWORD", ""),
        "database":    os.environ.get("TRADE_RELAY_MYSQL_DATABASE") or os.environ.get("DB_NAME", "trade_relay"),
        "charset":     "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit":  False,
        "connect_timeout": 10,
    }


def _mysql_proxy_url() -> str:
    return (
        os.environ.get("TRADE_RELAY_MYSQL_PROXY_URL")
        or os.environ.get("MYSQL_PROXY_URL")
        or ""
    ).strip()


def _mysql_proxy_cfg() -> Optional[dict]:
    proxy_url = _mysql_proxy_url()
    if not proxy_url:
        return None

    parsed = urlparse(proxy_url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _MYSQL_PROXY_SCHEME_NAMES:
        raise ValueError(
            "Unsupported MySQL proxy scheme "
            f"{scheme!r}. Use one of: {', '.join(sorted(_MYSQL_PROXY_SCHEME_NAMES))}."
        )
    if not parsed.hostname or not parsed.port:
        raise ValueError(
            "TRADE_RELAY_MYSQL_PROXY_URL must include host and port, "
            f"for example socks5://127.0.0.1:10808. Got: {proxy_url!r}"
        )

    return {
        "scheme": scheme,
        "host": parsed.hostname,
        "port": parsed.port,
        "username": unquote(parsed.username) if parsed.username else None,
        "password": unquote(parsed.password) if parsed.password else None,
    }


@lru_cache(maxsize=1)
def _mysql_pool_size() -> int:
    raw_value = (os.environ.get("TRADE_RELAY_MYSQL_POOL_SIZE") or "8").strip()
    try:
        return max(0, int(raw_value))
    except ValueError:
        logger.warning(
            "Invalid TRADE_RELAY_MYSQL_POOL_SIZE=%r, falling back to 8",
            raw_value,
        )
        return 8


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _set_mysql_session_utc(conn: pymysql.connections.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(_MYSQL_SESSION_UTC_SQL)


def _coerce_utc_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value).date()
    raise TypeError(f"Unsupported date value: {value!r}")


def _coerce_utc_naive_datetime(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if abs(timestamp) >= 1e11:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        normalized = trimmed[:-1] + "+00:00" if trimmed.endswith("Z") else trimmed
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    return None


_ORDER_DATETIME_FIELDS = {
    "filled_at",
    "trade_details_sync_next_retry_at",
    "close_tpsl_sync_next_retry_at",
}

_ORDER_NUMERIC_FIELDS = {
    "quantity",
    "filled_qty",
    "avg_price",
    "realized_pnl",
    "commission",
}


def _normalize_order_field_value(field: str, value):
    if field in _ORDER_DATETIME_FIELDS:
        return _coerce_utc_naive_datetime(value)
    if field in _ORDER_NUMERIC_FIELDS:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value


def _order_field_value_changed(current_row: Optional[dict], field: str, new_value) -> bool:
    if current_row is None:
        return True

    current_value = _normalize_order_field_value(field, current_row.get(field))
    next_value = _normalize_order_field_value(field, new_value)

    if isinstance(current_value, float) and isinstance(next_value, float):
        return abs(current_value - next_value) > 1e-12
    return current_value != next_value


def _filter_changed_order_updates(current_row: Optional[dict], updates: list[tuple[str, object]]) -> list[tuple[str, object]]:
    return [
        (field, value)
        for field, value in updates
        if _order_field_value_changed(current_row, field, value)
    ]


def _get_profile_day_bounds(profile_date: date) -> tuple[datetime, datetime]:
    start_at = datetime.combine(profile_date, time.min)
    return start_at, start_at + timedelta(days=1)


def _fetch_live_wallet_balance(username: str) -> float | None:
    normalized_username = str(username or "").strip()
    if not normalized_username:
        return None

    try:
        from trade_relay import config as cfg_module
        from trade_relay.exchange.binance_client import BinanceClient
    except Exception:
        logger.exception(
            "[DAILY_PROFILE] phase=wallet_balance_import_error username=%s",
            normalized_username,
        )
        return None

    api_key = cfg_module.get_api_key(normalized_username)
    api_secret = cfg_module.get_api_secret(normalized_username)
    if not api_key or not api_secret:
        return None

    env_symbol = str(os.environ.get("BINANCE_SYMBOL", "BTCUSDC") or "").upper()
    quote_asset = None
    for candidate in ("USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH"):
        if env_symbol.endswith(candidate) and len(env_symbol) > len(candidate):
            quote_asset = candidate
            break

    def _fallback_cached_wallet_balance() -> float | None:
        try:
            user_row = get_user_by_username(normalized_username)
            if not user_row:
                return None
            user_id = int(user_row.get("id") or 0)
            if user_id <= 0:
                return None
            summary = get_account_summary_from_db(user_id, env_symbol or None) or get_account_summary_from_db(user_id, None)
            if not summary:
                return None
            cached_wallet_balance = summary.get("wallet_balance")
            if cached_wallet_balance is None:
                return None
            return round(float(cached_wallet_balance), 4)
        except Exception:
            logger.exception(
                "[DAILY_PROFILE] phase=wallet_balance_cache_fallback_error username=%s",
                normalized_username,
            )
            return None

    try:
        client = BinanceClient(
            api_key=api_key,
            secret_key=api_secret,
            testnet=cfg_module.is_testnet(normalized_username),
        )
        account = client.get_account_info() or {}
        wallet_balance = account.get("totalWalletBalance")
        if quote_asset:
            assets = account.get("assets", []) or []
            selected_asset = next(
                (entry for entry in assets if str(entry.get("asset", "")).upper() == quote_asset),
                None,
            )
            if selected_asset is not None:
                wallet_balance = selected_asset.get("walletBalance")
        if wallet_balance is None:
            return _fallback_cached_wallet_balance()
        return round(float(wallet_balance), 4)
    except Exception:
        logger.exception(
            "[DAILY_PROFILE] phase=wallet_balance_fetch_error username=%s",
            normalized_username,
        )
        return _fallback_cached_wallet_balance()


_POSITION_HISTORY_TRADE_KEY_SQL = "COALESCE(NULLIF(position_id, 0), -id)"


def _safe_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _fetch_position_history_rows_for_daily_profile(
    cur,
    *,
    user_id: Optional[int] = None,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
) -> list[dict]:
    sql = [
        """
        SELECT id,
               user_id,
               username,
               symbol,
               side,
               quantity,
               realized_pnl,
               commission,
               position_id,
               close_order_id,
               close_price,
               created_at
        FROM position_history
        WHERE 1 = 1
        """
    ]
    params: list[object] = []
    if user_id is not None:
        sql.append("AND user_id = %s")
        params.append(user_id)
    if start_at is not None:
        sql.append("AND created_at >= %s")
        params.append(start_at)
    if end_at is not None:
        sql.append("AND created_at < %s")
        params.append(end_at)
    sql.append("ORDER BY created_at ASC, id ASC")
    cur.execute("\n".join(sql), params)
    return cur.fetchall() or []


def _aggregate_position_history_trade_groups(rows: list[dict]) -> list[dict]:
    position_id_by_close_order: dict[tuple[int, int], int] = {}
    for row in rows:
        user_id = int(row.get("user_id") or 0)
        close_order_id = int(row["close_order_id"]) if row.get("close_order_id") else None
        position_id = int(row["position_id"]) if row.get("position_id") else None
        if user_id > 0 and close_order_id is not None and position_id is not None:
            position_id_by_close_order[(user_id, close_order_id)] = position_id

    normalized_rows: list[dict] = []
    seen_exact_rows: set[tuple] = set()
    for row in rows:
        user_id = int(row.get("user_id") or 0)
        close_order_id = int(row["close_order_id"]) if row.get("close_order_id") else None
        position_id = int(row["position_id"]) if row.get("position_id") else None
        normalized_position_id = position_id or (
            position_id_by_close_order.get((user_id, close_order_id))
            if user_id > 0 and close_order_id is not None
            else None
        )

        # Drop unassigned shadow rows when the same close order already has a linked position.
        if position_id is None and normalized_position_id is not None and close_order_id is not None:
            continue

        exact_key = (
            user_id,
            normalized_position_id or 0,
            close_order_id or 0,
            str(row.get("symbol") or "").upper(),
            str(row.get("side") or "").upper(),
            round(_safe_float(row.get("quantity")), 12),
            round(_safe_float(row.get("realized_pnl")), 12),
            round(_safe_float(row.get("commission")), 12),
        )
        if exact_key in seen_exact_rows:
            continue
        seen_exact_rows.add(exact_key)
        normalized_rows.append({**row, "_normalized_position_id": normalized_position_id})

    grouped: dict[tuple[int, str], dict] = {}
    for row in normalized_rows:
        user_id = int(row.get("user_id") or 0)
        normalized_position_id = row.get("_normalized_position_id")
        close_order_id = int(row["close_order_id"]) if row.get("close_order_id") else None
        if normalized_position_id is not None:
            trade_key = f"position:{int(normalized_position_id)}"
        elif close_order_id is not None:
            trade_key = f"close_order:{close_order_id}"
        else:
            trade_key = f"history:{int(row.get('id') or 0)}"

        group = grouped.setdefault(
            (user_id, trade_key),
            {
                "user_id": user_id,
                "username": str(row.get("username") or ""),
                "trade_key": trade_key,
                "trade_date": _coerce_utc_date(row.get("created_at")),
                "trade_pnl": 0.0,
                "trade_commission": 0.0,
            },
        )
        group["trade_pnl"] += _safe_float(row.get("realized_pnl"))
        group["trade_commission"] += _safe_float(row.get("commission"))
        row_date = _coerce_utc_date(row.get("created_at"))
        if row_date >= group["trade_date"]:
            group["trade_date"] = row_date
            if row.get("username"):
                group["username"] = str(row.get("username") or "")

    return list(grouped.values())


def _position_history_trade_groups_subquery(*, row_where_sql: str = "WHERE 1 = 1") -> str:
    return f"""
        SELECT user_id,
               COALESCE(MAX(NULLIF(TRIM(COALESCE(username, '')), '')), '') AS username,
               {_POSITION_HISTORY_TRADE_KEY_SQL} AS trade_key,
               DATE(MAX(created_at)) AS trade_date,
               SUM(COALESCE(realized_pnl, 0)) AS trade_pnl,
               SUM(COALESCE(commission, 0)) AS trade_commission
        FROM position_history
        {row_where_sql}
        GROUP BY user_id, {_POSITION_HISTORY_TRADE_KEY_SQL}
    """


def _refresh_daily_profile_for_user_date(
    cur,
    user_id: int,
    username: str,
    profile_date: date,
    historical_account_balance=_ACCOUNT_BALANCE_MISSING,
) -> None:
    start_at, end_at = _get_profile_day_bounds(profile_date)
    raw_rows = _fetch_position_history_rows_for_daily_profile(
        cur,
        user_id=user_id,
        start_at=start_at,
        end_at=end_at,
    )
    trade_groups = [
        row for row in _aggregate_position_history_trade_groups(raw_rows)
        if row.get("trade_date") == profile_date
    ]
    row = {
        "trade_count": len(trade_groups),
        "win_count": sum(1 for trade in trade_groups if _safe_float(trade.get("trade_pnl")) > 0),
        "pnl": sum(_safe_float(trade.get("trade_pnl")) for trade in trade_groups),
        "commission": sum(_safe_float(trade.get("trade_commission")) for trade in trade_groups),
        "latest_username": next((str(trade.get("username") or "") for trade in reversed(trade_groups) if trade.get("username")), username),
    }
    trade_count = int(row.get("trade_count") or 0)
    if trade_count <= 0:
        cur.execute(
            "DELETE FROM daily_profile WHERE user_id = %s AND profile_date = %s",
            (user_id, profile_date),
        )
        return

    win_count = int(row.get("win_count") or 0)
    win_rate = (win_count / trade_count * 100.0) if trade_count > 0 else 0.0
    resolved_username = str(row.get("latest_username") or username or "")
    if profile_date == _utc_now_naive().date():
        account_balance = _fetch_live_wallet_balance(resolved_username)
    elif historical_account_balance is not _ACCOUNT_BALANCE_MISSING:
        account_balance = historical_account_balance
    else:
        cur.execute(
            "SELECT account_balance FROM daily_profile WHERE user_id = %s AND profile_date = %s LIMIT 1",
            (user_id, profile_date),
        )
        existing_row = cur.fetchone() or {}
        account_balance = existing_row.get("account_balance")
    cur.execute(
        """
        INSERT INTO daily_profile
            (user_id, username, profile_date, pnl, account_balance, trade_count, win_count, win_rate, commission, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            username = VALUES(username),
            pnl = VALUES(pnl),
            account_balance = VALUES(account_balance),
            trade_count = VALUES(trade_count),
            win_count = VALUES(win_count),
            win_rate = VALUES(win_rate),
            commission = VALUES(commission),
            updated_at = VALUES(updated_at)
        """,
        (
            user_id,
            resolved_username,
            profile_date,
            float(row.get("pnl") or 0),
            account_balance,
            trade_count,
            win_count,
            win_rate,
            float(row.get("commission") or 0),
            _utc_now_naive(),
        ),
    )


def _refresh_daily_profile_for_history_row(cur, history_id: int) -> None:
    cur.execute(
        "SELECT user_id, username, created_at FROM position_history WHERE id = %s LIMIT 1",
        (history_id,),
    )
    row = cur.fetchone()
    if not row:
        return
    _refresh_daily_profile_for_user_date(
        cur,
        int(row["user_id"]),
        str(row.get("username") or ""),
        _coerce_utc_date(row["created_at"]),
    )


def _rebuild_daily_profile_from_history(
    cur,
    *,
    user_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> dict[str, int]:
    existing_balance_sql = [
        "SELECT user_id, profile_date, account_balance FROM daily_profile WHERE 1 = 1",
    ]
    existing_balance_params: list[object] = []
    delete_sql = ["DELETE FROM daily_profile WHERE 1 = 1"]
    delete_params: list[object] = []
    trade_group_row_filters = ["WHERE 1 = 1"]
    trade_group_row_params: list[object] = []
    trade_date_filters = ["WHERE 1 = 1"]
    trade_date_params: list[object] = []

    if user_id is not None:
        existing_balance_sql.append("AND user_id = %s")
        existing_balance_params.append(user_id)
        delete_sql.append("AND user_id = %s")
        delete_params.append(user_id)
        trade_group_row_filters.append("AND user_id = %s")
        trade_group_row_params.append(user_id)

    if start_date is not None:
        existing_balance_sql.append("AND profile_date >= %s")
        existing_balance_params.append(start_date)
        delete_sql.append("AND profile_date >= %s")
        delete_params.append(start_date)
        trade_date_filters.append("AND trade_date >= %s")
        trade_date_params.append(start_date)

    if end_date is not None:
        existing_balance_sql.append("AND profile_date <= %s")
        existing_balance_params.append(end_date)
        delete_sql.append("AND profile_date <= %s")
        delete_params.append(end_date)
        trade_date_filters.append("AND trade_date <= %s")
        trade_date_params.append(end_date)

    cur.execute("\n".join(existing_balance_sql), existing_balance_params)
    existing_balances = {
        (int(row["user_id"]), _coerce_utc_date(row["profile_date"])): row.get("account_balance")
        for row in (cur.fetchall() or [])
    }

    cur.execute("\n".join(delete_sql), delete_params)
    deleted_rows = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    raw_rows = _fetch_position_history_rows_for_daily_profile(cur, user_id=user_id)
    trade_groups = _aggregate_position_history_trade_groups(raw_rows)
    grouped_rows = []
    for trade in trade_groups:
        trade_date = _coerce_utc_date(trade.get("trade_date"))
        if start_date is not None and trade_date < start_date:
            continue
        if end_date is not None and trade_date > end_date:
            continue
        grouped_rows.append(
            {
                "user_id": int(trade.get("user_id") or 0),
                "username": str(trade.get("username") or ""),
                "profile_date": trade_date,
            }
        )
    grouped_rows = list({(row["user_id"], row["profile_date"]): row for row in grouped_rows}.values())
    rebuilt_rows = len(grouped_rows)
    for grouped_row in grouped_rows:
        grouped_user_id = int(grouped_row["user_id"])
        grouped_profile_date = _coerce_utc_date(grouped_row["profile_date"])
        _refresh_daily_profile_for_user_date(
            cur,
            grouped_user_id,
            str(grouped_row.get("username") or ""),
            grouped_profile_date,
            historical_account_balance=existing_balances.get(
                (grouped_user_id, grouped_profile_date),
                _ACCOUNT_BALANCE_MISSING,
            ),
        )

    return {"deleted": int(deleted_rows), "rebuilt": int(rebuilt_rows)}


def rebuild_daily_profile(
    *,
    username: Optional[str] = None,
    start_date: Optional[date | str] = None,
    end_date: Optional[date | str] = None,
) -> dict[str, object]:
    normalized_username = str(username or "").strip() or None
    start_day = _coerce_utc_date(start_date) if start_date is not None else None
    end_day = _coerce_utc_date(end_date) if end_date is not None else None
    if start_day and end_day and start_day > end_day:
        raise ValueError("start_date cannot be later than end_date")

    user_id: Optional[int] = None
    if normalized_username:
        user = get_user_by_username(normalized_username)
        if not user:
            return {
                "ok": False,
                "user_found": False,
                "username": normalized_username,
                "start_date": str(start_day) if start_day else None,
                "end_date": str(end_day) if end_day else None,
                "deleted": 0,
                "rebuilt": 0,
            }
        user_id = int(user["id"])

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            result = _rebuild_daily_profile_from_history(
                cur,
                user_id=user_id,
                start_date=start_day,
                end_date=end_day,
            )
        conn.commit()
        return {
            "ok": True,
            "user_found": True,
            "username": normalized_username,
            "start_date": str(start_day) if start_day else None,
            "end_date": str(end_day) if end_day else None,
            **result,
        }
    finally:
        conn.close()


@contextmanager
def _pymysql_proxy_socket_patch(proxy_cfg: Optional[dict]):
    if not proxy_cfg:
        yield
        return

    try:
        import socks
    except ImportError as exc:
        raise RuntimeError(
            "TRADE_RELAY_MYSQL_PROXY_URL is configured but PySocks is not installed. "
            "Install PySocks first."
        ) from exc

    scheme_to_proxy_type = {
        "socks5": socks.SOCKS5,
        "socks5h": socks.SOCKS5,
        "socks4": socks.SOCKS4,
        "socks4a": socks.SOCKS4,
        "http": socks.HTTP,
        "https": socks.HTTP,
    }
    proxy_type = scheme_to_proxy_type[proxy_cfg["scheme"]]
    original_create_connection = pymysql.connections.socket.create_connection

    def create_connection(address, timeout=None, source_address=None, *, all_errors=False):
        return socks.create_connection(
            dest_pair=address,
            timeout=timeout,
            source_address=source_address,
            proxy_type=proxy_type,
            proxy_addr=proxy_cfg["host"],
            proxy_port=proxy_cfg["port"],
            proxy_username=proxy_cfg["username"],
            proxy_password=proxy_cfg["password"],
            proxy_rdns=proxy_cfg["scheme"] in {"socks5h", "socks4a"},
        )

    with _PYMYSQL_SOCKET_PATCH_LOCK:
        pymysql.connections.socket.create_connection = create_connection
        try:
            yield
        finally:
            pymysql.connections.socket.create_connection = original_create_connection


def _create_mysql_connection() -> pymysql.connections.Connection:
    mysql_cfg = _mysql_cfg()
    proxy_cfg = _mysql_proxy_cfg()
    if proxy_cfg:
        logger.info(
            "MySQL connect via proxy | target=%s:%s proxy=%s://%s:%s",
            mysql_cfg["host"],
            mysql_cfg["port"],
            proxy_cfg["scheme"],
            proxy_cfg["host"],
            proxy_cfg["port"],
        )
    with _pymysql_proxy_socket_patch(proxy_cfg):
        conn = pymysql.connect(**mysql_cfg)
    _set_mysql_session_utc(conn)
    return conn


class _PooledMySQLConnection:
    def __init__(self, pool: "_MySQLConnectionPool", conn: pymysql.connections.Connection):
        self._pool = pool
        self._conn = conn
        self._released = False

    def close(self) -> None:
        if self._released:
            return
        self._released = True
        self._pool.release(self._conn)

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


class _MySQLConnectionPool:
    def __init__(self, max_size: int):
        self._max_size = max(1, max_size)
        self._idle: list[pymysql.connections.Connection] = []
        self._created = 0
        self._condition = Condition()

    def acquire(self) -> _PooledMySQLConnection:
        conn: Optional[pymysql.connections.Connection] = None
        should_create = False
        with self._condition:
            while True:
                if self._idle:
                    conn = self._idle.pop()
                    break
                if self._created < self._max_size:
                    self._created += 1
                    should_create = True
                    break
                self._condition.wait()

        if should_create:
            try:
                conn = _create_mysql_connection()
            except Exception:
                with self._condition:
                    self._created -= 1
                    self._condition.notify()
                raise

        if conn is None:
            raise RuntimeError("Failed to acquire MySQL connection")

        if not self._prepare_for_checkout(conn):
            self._discard(conn)
            return self.acquire()

        return _PooledMySQLConnection(self, conn)

    def release(self, conn: pymysql.connections.Connection) -> None:
        try:
            conn.rollback()
        except Exception:
            self._discard(conn)
            return

        with self._condition:
            self._idle.append(conn)
            self._condition.notify()

    def _prepare_for_checkout(self, conn: pymysql.connections.Connection) -> bool:
        try:
            conn.ping(reconnect=True)
            _set_mysql_session_utc(conn)
            return True
        except Exception:
            return False

    def _discard(self, conn: pymysql.connections.Connection) -> None:
        try:
            conn.close()
        except Exception:
            pass
        finally:
            with self._condition:
                self._created = max(0, self._created - 1)
                self._condition.notify()


@lru_cache(maxsize=1)
def _mysql_connection_pool() -> Optional[_MySQLConnectionPool]:
    pool_size = _mysql_pool_size()
    if pool_size <= 0:
        return None
    return _MySQLConnectionPool(pool_size)


def get_connection():
    """Get a MySQL connection, reusing pooled connections when enabled."""
    pool = _mysql_connection_pool()
    if pool is None:
        return _create_mysql_connection()
    return pool.acquire()


def _normalize_order_category(order_type: Optional[str], order_category: Optional[str]) -> str:
    """Normalize persisted order_category values and derive conditional orders automatically."""
    normalized = (order_category or "").strip()
    if normalized:
        lowered = normalized.lower()
        if lowered == "condition":
            return "Conditional"
        if lowered == "conditional":
            return "Conditional"
        if lowered == "basic":
            return "Basic"

    order_type_upper = (order_type or "").strip().upper()
    if order_type_upper in {"STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET"}:
        return "Conditional"
    return "Basic"


def _table_exists(cur: pymysql.cursors.Cursor, table_name: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table_name,))
    return cur.fetchone() is not None

def _index_exists(cur: pymysql.cursors.Cursor, table_name: str, index_name: str) -> bool:
    cur.execute(f"SHOW INDEX FROM {table_name}")
    return any(str(row.get("Key_name") or "") == index_name for row in cur.fetchall())


def _create_positions_table(cur: pymysql.cursors.Cursor) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id              BIGINT          NOT NULL AUTO_INCREMENT,
            user_id         BIGINT          NOT NULL,
            username        VARCHAR(64)     NOT NULL,
            exchange        VARCHAR(32)     NOT NULL DEFAULT 'binance',
            symbol          VARCHAR(32)     NOT NULL,
            position_side   ENUM('LONG','SHORT','BOTH') NOT NULL DEFAULT 'BOTH',
            position_mode   VARCHAR(16)     NOT NULL DEFAULT 'UNKNOWN' COMMENT '持仓方式 SINGLE/DUAL/UNKNOWN',
            status          VARCHAR(8)      NOT NULL DEFAULT 'OPEN' COMMENT '持仓状态 OPEN/CLOSE',
            open_position_slot TINYINT      DEFAULT 1 COMMENT '仅当前打开仓位参与唯一约束；关闭后置空以保留历史记录',
            quantity        DECIMAL(20,8)   NOT NULL DEFAULT 0 COMMENT '持仓数量（负数为空头）',
            avg_entry_price DECIMAL(20,8)   DEFAULT NULL COMMENT '开仓均价',
            liquidation_price DECIMAL(20,8) DEFAULT NULL COMMENT '清算价',
            unrealized_pnl  DECIMAL(20,8)   DEFAULT NULL COMMENT '未实现盈亏',
            realized_pnl    DECIMAL(20,8)   NOT NULL DEFAULT 0 COMMENT '已实现盈亏',
            leverage        SMALLINT        NOT NULL DEFAULT 1 COMMENT '杠杆倍数',
            margin_type     ENUM('ISOLATED','CROSS') NOT NULL DEFAULT 'CROSS',
            updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY idx_positions_user (user_id),
            UNIQUE KEY uk_position_open (user_id, exchange, symbol, position_side, open_position_slot),
            CONSTRAINT fk_positions_user FOREIGN KEY (user_id) REFERENCES users (id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)


def _migrate_positions_table(cur: pymysql.cursors.Cursor) -> None:
    if not _table_exists(cur, "positions"):
        _create_positions_table(cur)
        return

    cur.execute("SHOW COLUMNS FROM positions")
    existing_columns = {row["Field"] for row in cur.fetchall()}
    if "liquidation_price" not in existing_columns:
        cur.execute("ALTER TABLE positions ADD COLUMN liquidation_price DECIMAL(20,8) DEFAULT NULL COMMENT '清算价' AFTER avg_entry_price")
        existing_columns.add("liquidation_price")
    if "position_mode" not in existing_columns:
        cur.execute("ALTER TABLE positions ADD COLUMN position_mode VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN' COMMENT '持仓方式 SINGLE/DUAL/UNKNOWN' AFTER position_side")
        cur.execute(
            "UPDATE positions SET position_mode = CASE "
            "WHEN UPPER(COALESCE(position_side, '')) = 'BOTH' THEN 'SINGLE' "
            "WHEN UPPER(COALESCE(position_side, '')) IN ('LONG', 'SHORT') THEN 'DUAL' "
            "ELSE 'UNKNOWN' END"
        )
        existing_columns.add("position_mode")
    if "status" not in existing_columns:
        cur.execute("ALTER TABLE positions ADD COLUMN status VARCHAR(8) NOT NULL DEFAULT 'OPEN' COMMENT '持仓状态 OPEN/CLOSE' AFTER position_mode")
        cur.execute(
            "UPDATE positions SET status = CASE "
            "WHEN ABS(COALESCE(quantity, 0)) > 0 THEN 'OPEN' "
            "ELSE 'CLOSE' END"
        )
        existing_columns.add("status")
    if "open_position_slot" not in existing_columns:
        cur.execute(
            "ALTER TABLE positions ADD COLUMN open_position_slot TINYINT DEFAULT 1 "
            "COMMENT '仅当前打开仓位参与唯一约束；关闭后置空以保留历史记录' AFTER status"
        )
        existing_columns.add("open_position_slot")
    cur.execute(
        "UPDATE positions SET open_position_slot = CASE "
        "WHEN UPPER(COALESCE(status, 'OPEN')) = 'OPEN' AND ABS(COALESCE(quantity, 0)) > 0 THEN 1 "
        "ELSE NULL END"
    )
    if not _index_exists(cur, "positions", "idx_positions_user"):
        cur.execute("ALTER TABLE positions ADD KEY idx_positions_user (user_id)")
    if _index_exists(cur, "positions", "uk_position"):
        cur.execute("ALTER TABLE positions DROP INDEX uk_position")
    if not _index_exists(cur, "positions", "uk_position_open"):
        cur.execute(
            "ALTER TABLE positions ADD UNIQUE KEY uk_position_open "
            "(user_id, exchange, symbol, position_side, open_position_slot)"
        )

    required_columns = {
        "id", "user_id", "username", "exchange", "symbol", "position_side", "position_mode", "status", "open_position_slot",
        "quantity", "avg_entry_price", "liquidation_price", "unrealized_pnl", "realized_pnl",
        "leverage", "margin_type", "updated_at",
    }
    if required_columns.issubset(existing_columns):
        return

    logger.info("Migrating positions table to current schema | existing_columns=%s", sorted(existing_columns))

    backup_table = "positions_legacy_backup"
    if not _table_exists(cur, backup_table):
        cur.execute(f"CREATE TABLE {backup_table} AS SELECT * FROM positions")
        logger.warning("Backed up legacy positions table to %s before migration", backup_table)

    cur.execute("SELECT * FROM positions ORDER BY id")
    legacy_rows = cur.fetchall()
    cur.execute("SELECT id, username FROM users WHERE is_active = 1 ORDER BY id")
    active_users = cur.fetchall()
    fallback_user = None
    if len(active_users) == 1:
        fallback_user = {
            "user_id": int(active_users[0]["id"]),
            "username": str(active_users[0]["username"]),
        }

    cur.execute("DROP TABLE positions")
    _create_positions_table(cur)

    migrated = 0
    skipped = 0
    for row in legacy_rows:
        legacy_position_id = int(row["id"])

        cur.execute(
            "SELECT user_id, username FROM orders WHERE position_id = %s AND user_id IS NOT NULL ORDER BY id DESC LIMIT 1",
            (legacy_position_id,),
        )
        owner = cur.fetchone()
        if owner:
            owner_info = {"user_id": int(owner["user_id"]), "username": str(owner["username"])}
        else:
            cur.execute(
                "SELECT user_id, username FROM position_history WHERE position_id = %s AND user_id IS NOT NULL ORDER BY id DESC LIMIT 1",
                (legacy_position_id,),
            )
            owner = cur.fetchone()
            owner_info = {"user_id": int(owner["user_id"]), "username": str(owner["username"])} if owner else fallback_user

        if not owner_info:
            skipped += 1
            logger.warning(
                "Skipped legacy positions row during migration; owner unresolved | backup_table=%s id=%s symbol=%s side=%s",
                backup_table,
                legacy_position_id,
                row.get("symbol"),
                row.get("side"),
            )
            continue

        position_side = str(row.get("position_side") or row.get("side") or "BOTH").upper()
        if position_side not in {"LONG", "SHORT", "BOTH"}:
            position_side = "BOTH"

        margin_type = str(row.get("margin_type") or "CROSS").upper()
        if margin_type not in {"CROSS", "ISOLATED"}:
            margin_type = "CROSS"

        updated_at = row.get("updated_at") or row.get("created_at") or row.get("opened_at") or _utc_now_naive()
        is_open = abs(float(row.get("quantity") or 0)) > 0

        cur.execute(
            """INSERT INTO positions
               (id, user_id, username, exchange, symbol, position_side, position_mode, status, open_position_slot,
                quantity, avg_entry_price, liquidation_price, unrealized_pnl, realized_pnl,
                leverage, margin_type, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                legacy_position_id,
                owner_info["user_id"],
                owner_info["username"],
                "binance",
                row.get("symbol"),
                position_side,
                "SINGLE" if position_side == "BOTH" else "DUAL",
                "OPEN" if is_open else "CLOSE",
                1 if is_open else None,
                row.get("quantity") or 0,
                row.get("avg_entry_price") if "avg_entry_price" in row else row.get("entry_price"),
                row.get("liquidation_price"),
                row.get("unrealized_pnl"),
                row.get("realized_pnl") or 0,
                row.get("leverage") or 1,
                margin_type,
                updated_at,
            ),
        )
        migrated += 1

    logger.info(
        "Positions schema migration finished | migrated=%s skipped=%s backup_table=%s",
        migrated,
        skipped,
        backup_table,
    )


def init_db() -> None:
    """Initialize database schema – idempotent, safe to call on every startup."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # ── users（用户信息）──────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id                BIGINT       NOT NULL AUTO_INCREMENT,
                    username          VARCHAR(64)  NOT NULL,
                    password_hash     VARCHAR(128) NOT NULL,
                    role              ENUM('admin','user') NOT NULL DEFAULT 'user',
                    is_active         TINYINT(1)   NOT NULL DEFAULT 1,
                    binance_api_key   TEXT         DEFAULT NULL COMMENT 'Binance API Key (encrypted)',
                    binance_api_secret TEXT        DEFAULT NULL COMMENT 'Binance API Secret (encrypted)',
                    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_username (username)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # Migration: add columns for existing deployments
            for col, definition in [
                ("binance_api_key",    "TEXT DEFAULT NULL COMMENT 'Binance API Key (encrypted)'"),
                ("binance_api_secret", "TEXT DEFAULT NULL COMMENT 'Binance API Secret (encrypted)'"),
                ("updated_at",         "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            ]:
                try:
                    cur.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                except pymysql.err.OperationalError:
                    pass  # column already exists

            # ── orders（当前委托 + 历史订单）────────────────────────────
            # status 含义:
            #   当前委托: NEW | PARTIALLY_FILLED | PENDING_CANCEL | PENDING
            #   历史订单: FILLED | CANCELED | EXPIRED | REJECTED | FAILED | ERROR
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id                BIGINT          NOT NULL AUTO_INCREMENT,
                    user_id           BIGINT          NOT NULL,
                    username          VARCHAR(64)     NOT NULL,
                    exchange          VARCHAR(32)     NOT NULL DEFAULT 'binance',
                    symbol            VARCHAR(32)     NOT NULL,
                    side              ENUM('BUY','SELL') NOT NULL,
                    order_type        VARCHAR(32)     NOT NULL,
                    quantity          DECIMAL(20,8)   NOT NULL,
                    price             DECIMAL(20,8)   DEFAULT NULL COMMENT '限价单委托价',
                    stop_price        DECIMAL(20,8)   DEFAULT NULL COMMENT '止损触发价',
                    tp_price          DECIMAL(20,8)   DEFAULT NULL COMMENT '计划止盈价',
                    sl_price          DECIMAL(20,8)   DEFAULT NULL COMMENT '计划止损价',
                    status            VARCHAR(32)     NOT NULL DEFAULT 'NEW',
                    algo_id           VARCHAR(64)     DEFAULT NULL COMMENT '条件单算法订单ID',
                    algo_client_id    VARCHAR(64)     DEFAULT NULL COMMENT '条件单客户端算法订单ID',
                    exchange_order_id VARCHAR(64)     DEFAULT NULL COMMENT '交易所订单ID',
                    client_order_id   VARCHAR(64)     DEFAULT NULL COMMENT '客户端订单ID',
                    filled_qty        DECIMAL(20,8)   NOT NULL DEFAULT 0 COMMENT '已成交数量',
                    avg_price         DECIMAL(20,8)   DEFAULT NULL COMMENT '成交均价',
                    filled_at         DATETIME        DEFAULT NULL COMMENT '实际成交时间',
                    realized_pnl      DECIMAL(30,10)  DEFAULT NULL COMMENT '已实现盈亏',
                    commission        DECIMAL(20,8)   DEFAULT NULL COMMENT '手续费',
                    commission_asset  VARCHAR(16)     DEFAULT NULL COMMENT '手续费币种',
                    trade_details_sync_attempts INT   NOT NULL DEFAULT 0 COMMENT '成交明细回填重试次数',
                    trade_details_sync_next_retry_at DATETIME DEFAULT NULL COMMENT '成交明细下次回填时间',
                    trade_details_sync_last_error TEXT COMMENT '成交明细最近回填错误',
                    close_tpsl_sync_attempts INT    NOT NULL DEFAULT 0 COMMENT '平仓TP/SL刷新重试次数',
                    close_tpsl_sync_next_retry_at DATETIME DEFAULT NULL COMMENT '平仓TP/SL下次刷新时间',
                    close_tpsl_sync_last_error TEXT COMMENT '平仓TP/SL最近刷新错误',
                    trade_direction   ENUM('OPEN','CLOSE') DEFAULT NULL COMMENT '开仓/平仓',
                    position_mode     VARCHAR(16)     NOT NULL DEFAULT 'UNKNOWN' COMMENT '持仓方式 SINGLE/DUAL/UNKNOWN',
                    reduce_only       TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '只减仓',
                    post_only         TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '只做Maker',
                    position_id       BIGINT          DEFAULT NULL COMMENT '关联持仓ID',
                    order_category    ENUM('Basic','Conditional') NOT NULL DEFAULT 'Basic' COMMENT '订单分类',
                    source            ENUM('trade_relay','external') NOT NULL DEFAULT 'trade_relay' COMMENT '订单来源: trade_relay=本系统下单, external=外部工具下单',
                    error_message     TEXT            COMMENT '错误信息',
                    created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                                      ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    KEY idx_user_status (user_id, status),
                    KEY idx_user_created_at (user_id, created_at),
                    KEY idx_status_created (status, created_at),
                    KEY idx_category_status_created (order_category, status, created_at),
                    KEY idx_user_category_status_created (user_id, order_category, status, created_at),
                    KEY idx_username_status_created (username, status, created_at),
                    KEY idx_user_symbol_status_filled_at (user_id, symbol, status, filled_at),
                    KEY idx_username_exchange_order (username, exchange_order_id),
                    KEY idx_username_algo_id (username, algo_id),
                    KEY idx_created_at (created_at DESC),
                    CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users (id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # Migration: add columns that may be missing in older deployments
            for _col, _ddl in [
                ("trade_direction", "ALTER TABLE orders ADD COLUMN trade_direction ENUM('OPEN','CLOSE') DEFAULT NULL COMMENT '开仓/平仓' AFTER commission_asset"),
                ("position_mode",  "ALTER TABLE orders ADD COLUMN position_mode VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN' COMMENT '持仓方式 SINGLE/DUAL/UNKNOWN' AFTER trade_direction"),
                ("position_id",     "ALTER TABLE orders ADD COLUMN position_id BIGINT DEFAULT NULL COMMENT '关联持仓ID' AFTER trade_direction"),
                ("reduce_only",     "ALTER TABLE orders ADD COLUMN reduce_only TINYINT(1) NOT NULL DEFAULT 0 COMMENT '只减仓' AFTER trade_direction"),
                ("post_only",       "ALTER TABLE orders ADD COLUMN post_only TINYINT(1) NOT NULL DEFAULT 0 COMMENT '只做Maker' AFTER reduce_only"),
                ("order_category",  "ALTER TABLE orders ADD COLUMN order_category ENUM('Basic','Conditional') NOT NULL DEFAULT 'Basic' COMMENT '订单分类' AFTER position_id"),
                ("tp_price",        "ALTER TABLE orders ADD COLUMN tp_price DECIMAL(20,8) DEFAULT NULL COMMENT '计划止盈价' AFTER stop_price"),
                ("sl_price",        "ALTER TABLE orders ADD COLUMN sl_price DECIMAL(20,8) DEFAULT NULL COMMENT '计划止损价' AFTER tp_price"),
                ("filled_at",       "ALTER TABLE orders ADD COLUMN filled_at DATETIME DEFAULT NULL COMMENT '实际成交时间' AFTER avg_price"),
                ("realized_pnl",    "ALTER TABLE orders ADD COLUMN realized_pnl DECIMAL(30,10) DEFAULT NULL COMMENT '已实现盈亏' AFTER avg_price"),
                ("trade_details_sync_attempts", "ALTER TABLE orders ADD COLUMN trade_details_sync_attempts INT NOT NULL DEFAULT 0 COMMENT '成交明细回填重试次数' AFTER commission_asset"),
                ("trade_details_sync_next_retry_at", "ALTER TABLE orders ADD COLUMN trade_details_sync_next_retry_at DATETIME DEFAULT NULL COMMENT '成交明细下次回填时间' AFTER trade_details_sync_attempts"),
                ("trade_details_sync_last_error", "ALTER TABLE orders ADD COLUMN trade_details_sync_last_error TEXT COMMENT '成交明细最近回填错误' AFTER trade_details_sync_next_retry_at"),
                ("close_tpsl_sync_attempts", "ALTER TABLE orders ADD COLUMN close_tpsl_sync_attempts INT NOT NULL DEFAULT 0 COMMENT '平仓TP/SL刷新重试次数' AFTER trade_details_sync_last_error"),
                ("close_tpsl_sync_next_retry_at", "ALTER TABLE orders ADD COLUMN close_tpsl_sync_next_retry_at DATETIME DEFAULT NULL COMMENT '平仓TP/SL下次刷新时间' AFTER close_tpsl_sync_attempts"),
                ("close_tpsl_sync_last_error", "ALTER TABLE orders ADD COLUMN close_tpsl_sync_last_error TEXT COMMENT '平仓TP/SL最近刷新错误' AFTER close_tpsl_sync_next_retry_at"),
                ("algo_id",         "ALTER TABLE orders ADD COLUMN algo_id VARCHAR(64) DEFAULT NULL COMMENT '条件单算法订单ID' AFTER status"),
                ("algo_client_id",  "ALTER TABLE orders ADD COLUMN algo_client_id VARCHAR(64) DEFAULT NULL COMMENT '条件单客户端算法订单ID' AFTER algo_id"),
                ("source",          "ALTER TABLE orders ADD COLUMN source ENUM('trade_relay','external') NOT NULL DEFAULT 'trade_relay' COMMENT '订单来源: trade_relay=本系统下单, external=外部工具下单' AFTER exchange"),
            ]:
                try:
                    cur.execute(_ddl)
                except Exception:
                    pass  # column already exists
            try:
                cur.execute("ALTER TABLE orders ADD INDEX idx_status_created (status, created_at)")
            except Exception:
                pass  # index already exists
            for _index_ddl in [
                "ALTER TABLE orders ADD INDEX idx_user_created_at (user_id, created_at)",
                "ALTER TABLE orders ADD INDEX idx_category_status_created (order_category, status, created_at)",
                "ALTER TABLE orders ADD INDEX idx_user_category_status_created (user_id, order_category, status, created_at)",
                "ALTER TABLE orders ADD INDEX idx_username_status_created (username, status, created_at)",
                "ALTER TABLE orders ADD INDEX idx_user_symbol_status_filled_at (user_id, symbol, status, filled_at)",
                "ALTER TABLE orders ADD INDEX idx_username_exchange_order (username, exchange_order_id)",
                "ALTER TABLE orders ADD INDEX idx_username_algo_id (username, algo_id)",
                "ALTER TABLE orders ADD INDEX idx_trade_details_retry_due (status, trade_details_sync_next_retry_at)",
                "ALTER TABLE orders ADD INDEX idx_close_tpsl_retry_due (status, close_tpsl_sync_next_retry_at)",
            ]:
                try:
                    cur.execute(_index_ddl)
                except Exception:
                    pass  # index already exists
            try:
                cur.execute("ALTER TABLE orders MODIFY COLUMN order_category ENUM('Basic','Condition','Conditional') NOT NULL DEFAULT 'Basic' COMMENT '订单分类'")
            except Exception:
                pass
            try:
                cur.execute("UPDATE orders SET order_category = 'Conditional' WHERE order_category = 'Condition'")
            except Exception:
                pass
            try:
                cur.execute("ALTER TABLE orders MODIFY COLUMN order_category ENUM('Basic','Conditional') NOT NULL DEFAULT 'Basic' COMMENT '订单分类'")
            except Exception:
                pass
            try:
                cur.execute(
                    "UPDATE orders SET algo_id = exchange_order_id "
                    "WHERE order_category = 'Conditional' "
                    "AND (algo_id IS NULL OR TRIM(COALESCE(algo_id, '')) = '') "
                    "AND exchange_order_id IS NOT NULL "
                    "AND TRIM(COALESCE(exchange_order_id, '')) <> ''"
                )
            except Exception:
                pass
            try:
                cur.execute(
                    "UPDATE orders SET algo_client_id = client_order_id "
                    "WHERE order_category = 'Conditional' "
                    "AND (algo_client_id IS NULL OR TRIM(COALESCE(algo_client_id, '')) = '') "
                    "AND client_order_id IS NOT NULL "
                    "AND TRIM(COALESCE(client_order_id, '')) <> ''"
                )
            except Exception:
                pass

            # ── positions（头寸信息）──────────────────────────────────────
            _migrate_positions_table(cur)
            _get_table_columns.cache_clear()

            # ── operation_logs（操作日志）─────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS operation_logs (
                    id         BIGINT      NOT NULL AUTO_INCREMENT,
                    user_id    BIGINT      DEFAULT NULL,
                    username   VARCHAR(64) DEFAULT NULL,
                    action     VARCHAR(64) NOT NULL,
                    details    TEXT,
                    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    KEY idx_created_at (created_at DESC)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # ── position_history（持仓历史）───────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS position_history (
                    id            BIGINT          NOT NULL AUTO_INCREMENT,
                    user_id       INT             NOT NULL DEFAULT 0 COMMENT '用户ID',
                    username      VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '用户名',
                    symbol        VARCHAR(32)     NOT NULL COMMENT '交易对',
                    side          VARCHAR(8)      NOT NULL DEFAULT 'LONG' COMMENT '方向 LONG/SHORT',
                    position_mode VARCHAR(16)     NOT NULL DEFAULT 'UNKNOWN' COMMENT '持仓方式 SINGLE/DUAL/UNKNOWN',
                    entry_price   DECIMAL(30,10)  NOT NULL COMMENT '开仓均价',
                    close_price   DECIMAL(30,10)  NOT NULL COMMENT '平仓价格',
                    quantity      DECIMAL(30,10)  NOT NULL COMMENT '成交数量',
                    realized_pnl  DECIMAL(30,10)  NOT NULL DEFAULT 0 COMMENT '已实现盈亏',
                    commission    DECIMAL(30,10)  NOT NULL DEFAULT 0 COMMENT '手续费',
                    commission_asset VARCHAR(16)  DEFAULT NULL COMMENT '手续费币种',
                    position_id   BIGINT          DEFAULT NULL COMMENT '关联持仓ID（对应 positions.id）',
                    close_order_id BIGINT         DEFAULT NULL COMMENT '关联实际平仓订单ID（对应 orders.id）',
                    created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    updated_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                    PRIMARY KEY (id),
                    KEY idx_user_id (user_id),
                    KEY idx_username (username),
                    KEY idx_symbol (symbol),
                    KEY idx_close_order_id (close_order_id),
                    KEY idx_created_at (created_at DESC)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='持仓历史'
            """)
            try:
                cur.execute("SHOW COLUMNS FROM position_history LIKE 'update_at'")
                has_legacy_update_at = cur.fetchone() is not None
                cur.execute("SHOW COLUMNS FROM position_history LIKE 'updated_at'")
                has_updated_at = cur.fetchone() is not None
                if has_legacy_update_at and not has_updated_at:
                    cur.execute(
                        "ALTER TABLE position_history "
                        "CHANGE COLUMN update_at updated_at DATETIME NOT NULL "
                        "DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间'"
                    )
            except Exception:
                pass
            # Add columns that may be missing if table was created before this schema version
            for _col, _ddl in [
                ("user_id",     "ALTER TABLE position_history ADD COLUMN user_id INT NOT NULL DEFAULT 0 COMMENT '用户ID' AFTER id"),
                ("username",    "ALTER TABLE position_history ADD COLUMN username VARCHAR(64) NOT NULL DEFAULT '' COMMENT '用户名' AFTER user_id"),
                ("side",        "ALTER TABLE position_history ADD COLUMN side VARCHAR(8) NOT NULL DEFAULT 'LONG' COMMENT '方向 LONG/SHORT' AFTER symbol"),
                ("position_mode", "ALTER TABLE position_history ADD COLUMN position_mode VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN' COMMENT '持仓方式 SINGLE/DUAL/UNKNOWN' AFTER side"),
                ("commission_asset", "ALTER TABLE position_history ADD COLUMN commission_asset VARCHAR(16) DEFAULT NULL COMMENT '手续费币种' AFTER commission"),
                ("position_id", "ALTER TABLE position_history ADD COLUMN position_id BIGINT DEFAULT NULL COMMENT '关联持仓ID（对应 positions.id）' AFTER commission"),
                ("close_order_id", "ALTER TABLE position_history ADD COLUMN close_order_id BIGINT DEFAULT NULL COMMENT '关联实际平仓订单ID（对应 orders.id）' AFTER position_id"),
                ("updated_at",  "ALTER TABLE position_history ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间' AFTER created_at"),
            ]:
                try:
                    cur.execute(_ddl)
                except Exception:
                    pass  # column already exists
            try:
                cur.execute("ALTER TABLE position_history ADD INDEX idx_close_order_id (close_order_id)")
            except Exception:
                pass

            cur.execute("""
                CREATE TABLE IF NOT EXISTS daily_profile (
                    id            BIGINT          NOT NULL AUTO_INCREMENT,
                    user_id       BIGINT          NOT NULL COMMENT '用户ID',
                    username      VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '用户名',
                    profile_date  DATE            NOT NULL COMMENT 'UTC自然日',
                    pnl           DECIMAL(30,10)  NOT NULL DEFAULT 0 COMMENT '当日已实现盈亏',
                    account_balance DECIMAL(30,10) DEFAULT NULL COMMENT '更新时的实际钱包余额',
                    trade_count   INT             NOT NULL DEFAULT 0 COMMENT '当日交易次数',
                    win_count     INT             NOT NULL DEFAULT 0 COMMENT '当日盈利次数',
                    win_rate      DECIMAL(10,4)   NOT NULL DEFAULT 0 COMMENT '当日胜率',
                    commission    DECIMAL(30,10)  NOT NULL DEFAULT 0 COMMENT '当日手续费',
                    updated_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_user_date (user_id, profile_date),
                    KEY idx_profile_date (profile_date, pnl DESC),
                    KEY idx_username (username)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='每日收益汇总'
            """)
            for _col, _ddl in [
                ("username", "ALTER TABLE daily_profile ADD COLUMN username VARCHAR(64) NOT NULL DEFAULT '' COMMENT '用户名' AFTER user_id"),
                ("account_balance", "ALTER TABLE daily_profile ADD COLUMN account_balance DECIMAL(30,10) DEFAULT NULL COMMENT '更新时的实际钱包余额' AFTER pnl"),
                ("trade_count", "ALTER TABLE daily_profile ADD COLUMN trade_count INT NOT NULL DEFAULT 0 COMMENT '当日交易次数' AFTER pnl"),
                ("win_count", "ALTER TABLE daily_profile ADD COLUMN win_count INT NOT NULL DEFAULT 0 COMMENT '当日盈利次数' AFTER trade_count"),
                ("win_rate", "ALTER TABLE daily_profile ADD COLUMN win_rate DECIMAL(10,4) NOT NULL DEFAULT 0 COMMENT '当日胜率' AFTER win_count"),
                ("commission", "ALTER TABLE daily_profile ADD COLUMN commission DECIMAL(30,10) NOT NULL DEFAULT 0 COMMENT '当日手续费' AFTER win_rate"),
                ("updated_at", "ALTER TABLE daily_profile ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间' AFTER commission"),
            ]:
                try:
                    cur.execute(_ddl)
                except Exception:
                    pass
            try:
                cur.execute("ALTER TABLE daily_profile ADD UNIQUE KEY uk_user_date (user_id, profile_date)")
            except Exception:
                pass
            try:
                cur.execute("ALTER TABLE daily_profile ADD INDEX idx_profile_date (profile_date, pnl DESC)")
            except Exception:
                pass
            try:
                cur.execute("ALTER TABLE daily_profile ADD INDEX idx_username (username)")
            except Exception:
                pass
            _rebuild_daily_profile_from_history(cur)

            # ── ticker_messages（滚动播报文案）───────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ticker_messages (
                    id          BIGINT       NOT NULL AUTO_INCREMENT,
                    contents_zh TEXT         NOT NULL COMMENT '中文播报内容',
                    contents_en TEXT         NOT NULL COMMENT '英文播报内容',
                    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    PRIMARY KEY (id),
                    KEY idx_created_at (created_at DESC)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # ── tickers（交易对表）───────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tickers (
                    id                    INT          NOT NULL AUTO_INCREMENT,
                    symbol                VARCHAR(50)  NOT NULL COMMENT '交易对符号',
                    pair                  VARCHAR(50)  NOT NULL COMMENT '交易对名称',
                    base_asset            VARCHAR(50)  DEFAULT NULL COMMENT '基础资产',
                    quote_asset           VARCHAR(50)  DEFAULT NULL COMMENT '计价资产',
                    delivery_date         DATETIME     DEFAULT NULL COMMENT '交割日期',
                    onboard_date          DATETIME     DEFAULT NULL COMMENT '上线日期',
                    status                VARCHAR(20)  DEFAULT NULL COMMENT '状态',
                    fdv_value             FLOAT        DEFAULT NULL COMMENT 'FDV市值',
                    price_precision       INT          DEFAULT NULL COMMENT '价格精度',
                    quantity_precision    INT          DEFAULT NULL COMMENT '数量精度',
                    base_asset_precision  INT          DEFAULT NULL COMMENT '基础资产精度',
                    quote_asset_precision INT          DEFAULT NULL COMMENT '计价资产精度',
                    max_price             FLOAT        DEFAULT NULL COMMENT '最大价格',
                    min_price             FLOAT        DEFAULT NULL COMMENT '最小价格',
                    tick_size             FLOAT        DEFAULT NULL COMMENT '价格步长',
                    is_monitor            TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否监控',
                    created_at            DATETIME     DEFAULT CURRENT_TIMESTAMP,
                    updated_at            DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    UNIQUE KEY symbol (symbol),
                    KEY idx_symbol (symbol),
                    KEY idx_is_monitor (is_monitor),
                    KEY idx_status (status)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='交易对表'
            """)

            # ── account_summary（账户快照缓存）──────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS account_summary (
                    id                   BIGINT        NOT NULL AUTO_INCREMENT,
                    user_id              BIGINT        NOT NULL COMMENT '用户ID（关联 users.id）',
                    symbol               VARCHAR(32)   DEFAULT NULL COMMENT '交易对（NULL 表示全局）',
                    base_asset           VARCHAR(16)   DEFAULT NULL,
                    quote_asset          VARCHAR(16)   DEFAULT NULL,
                    position_mode        VARCHAR(16)   DEFAULT NULL COMMENT '持仓方式 SINGLE/DUAL/UNKNOWN',
                    leverage             INT           DEFAULT NULL COMMENT '用户最近设置的杠杆',
                    configured_leverage  INT           DEFAULT NULL,
                    long_position_qty    DECIMAL(30,10) DEFAULT NULL,
                    short_position_qty   DECIMAL(30,10) DEFAULT NULL,
                    long_position_value  DECIMAL(30,10) DEFAULT NULL,
                    short_position_value DECIMAL(30,10) DEFAULT NULL,
                    rest_mark_price      DECIMAL(30,10) DEFAULT NULL,
                    available_balance    DECIMAL(30,10) DEFAULT NULL,
                    margin_ratio         DECIMAL(20,10) DEFAULT NULL,
                    risk_rate            DECIMAL(20,10) DEFAULT NULL,
                    maint_margin         DECIMAL(30,10) DEFAULT NULL,
                    total_equity         DECIMAL(30,10) DEFAULT NULL,
                    position_value       DECIMAL(30,10) DEFAULT NULL,
                    actual_leverage      DECIMAL(20,10) DEFAULT NULL,
                    unrealized_pnl       DECIMAL(30,10) DEFAULT NULL,
                    wallet_balance       DECIMAL(30,10) DEFAULT NULL,
                    has_api_credentials  TINYINT(1)    NOT NULL DEFAULT 0,
                    message              TEXT          DEFAULT NULL,
                    synced_at            DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                                         ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '最后同步时间',
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_user_symbol (user_id, symbol)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='账户快照缓存（后台定时同步）'
            """)

            # ── income_history（交易所资金流水）────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS income_history (
                    id            BIGINT          NOT NULL AUTO_INCREMENT,
                    user_id       BIGINT          NOT NULL COMMENT '用户ID（关联 users.id）',
                    username      VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '用户名',
                    exchange      VARCHAR(32)     NOT NULL DEFAULT 'binance' COMMENT '交易所',
                    symbol        VARCHAR(32)     NOT NULL DEFAULT '' COMMENT '交易对',
                    income_type   VARCHAR(32)     NOT NULL COMMENT '流水类型 REALIZED_PNL/COMMISSION/FUNDING_FEE/...',
                    income        DECIMAL(30,10)  NOT NULL DEFAULT 0 COMMENT '资金变动金额，保持交易所原始符号',
                    asset         VARCHAR(16)     NOT NULL DEFAULT '' COMMENT '资产币种',
                    info_text     VARCHAR(128)    NOT NULL DEFAULT '' COMMENT '交易所 info 字段',
                    trade_id      VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '交易所 tradeId',
                    tran_id       VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '交易所 tranId',
                    income_time   DATETIME(3)     NOT NULL COMMENT '交易所资金流水时间（UTC）',
                    created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    updated_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_income_event (user_id, exchange, tran_id, trade_id, income_type, income_time, symbol, asset),
                    KEY idx_income_user_time (user_id, income_time),
                    KEY idx_income_user_type_time (user_id, income_type, income_time),
                    CONSTRAINT fk_income_history_user FOREIGN KEY (user_id) REFERENCES users (id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易所 income history 资金流水'
            """)
            for _index_ddl in [
                "ALTER TABLE income_history ADD INDEX idx_income_user_time (user_id, income_time)",
                "ALTER TABLE income_history ADD INDEX idx_income_user_type_time (user_id, income_type, income_time)",
            ]:
                try:
                    cur.execute(_index_ddl)
                except Exception:
                    pass
            # ── 迁移：若旧表仍使用 username 列，自动切换为 user_id ──────────
            cur.execute("SHOW COLUMNS FROM account_summary LIKE 'username'")
            if cur.fetchone():
                cur.execute("ALTER TABLE account_summary DROP KEY uk_user_symbol")
                cur.execute(
                    "ALTER TABLE account_summary "
                    "ADD COLUMN `user_id` BIGINT NOT NULL DEFAULT 0 COMMENT '用户ID' AFTER id"
                )
                cur.execute("ALTER TABLE account_summary DROP COLUMN `username`")
                cur.execute(
                    "ALTER TABLE account_summary "
                    "ADD UNIQUE KEY uk_user_symbol (user_id, symbol)"
                )
            try:
                cur.execute("ALTER TABLE account_summary ADD COLUMN position_mode VARCHAR(16) DEFAULT NULL COMMENT '持仓方式 SINGLE/DUAL/UNKNOWN' AFTER quote_asset")
            except Exception:
                pass
            try:
                cur.execute("ALTER TABLE account_summary ADD COLUMN leverage INT DEFAULT NULL COMMENT '用户最近设置的杠杆' AFTER position_mode")
            except Exception:
                pass

        conn.commit()
    finally:
        conn.close()


# ──────────────────────────────────────────────
# account_summary CRUD
# ──────────────────────────────────────────────

_ACCOUNT_SUMMARY_COLUMNS = (
    "user_id", "symbol", "base_asset", "quote_asset", "position_mode", "leverage",
    "configured_leverage", "long_position_qty", "short_position_qty",
    "long_position_value", "short_position_value", "rest_mark_price",
    "available_balance", "margin_ratio", "risk_rate", "maint_margin",
    "total_equity", "position_value", "actual_leverage", "unrealized_pnl",
    "wallet_balance", "has_api_credentials", "message",
)


def upsert_account_summary(user_id: int, symbol: Optional[str], data: dict) -> None:
    """Insert or update account_summary row for the given user_id+symbol."""
    row = {col: data.get(col) for col in _ACCOUNT_SUMMARY_COLUMNS}
    row["user_id"] = user_id
    row["symbol"] = symbol.upper() if symbol else None

    cols = list(row.keys())
    mutable_cols = [c for c in cols if c not in ("user_id", "symbol")]
    assignments = ", ".join(f"{c} = %s" for c in mutable_cols)
    update_sql = (
        f"UPDATE account_summary SET {assignments}, synced_at = CURRENT_TIMESTAMP(3)"
        " WHERE user_id = %s AND symbol <=> %s"
    )
    insert_sql = (
        f"INSERT INTO account_summary ({', '.join(cols)}) VALUES "
        f"({', '.join(['%s'] * len(cols))})"
    )
    _log_db_write("upsert", "account_summary", row)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            update_params = [row[c] for c in mutable_cols] + [user_id, row["symbol"]]
            cur.execute(update_sql, update_params)
            if cur.rowcount == 0:
                try:
                    cur.execute(insert_sql, [row[c] for c in cols])
                except pymysql.err.IntegrityError:
                    cur.execute(update_sql, update_params)
        conn.commit()
        _log_db_write_result("upsert", "account_summary", user_id=user_id, symbol=row["symbol"], affected_rows=1)
    finally:
        conn.close()


def get_account_summary_from_db(user_id: int, symbol: Optional[str]) -> Optional[dict]:
    """Read the latest account_summary snapshot from DB. Returns None if not found."""
    normalized = symbol.upper() if symbol else None
    _log_db_query("account_summary", "get_account_summary_from_db", user_id=user_id, symbol=normalized)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM account_summary WHERE user_id = %s AND symbol <=> %s",
                (user_id, normalized),
            )
            row = cur.fetchone()
            if row is None and normalized is None:
                cur.execute(
                    """
                    SELECT * FROM account_summary
                    WHERE user_id = %s
                    ORDER BY synced_at DESC, id DESC
                    LIMIT 1
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
            _log_db_query_result(
                "account_summary",
                "get_account_summary_from_db",
                user_id=user_id,
                symbol=normalized,
                found=bool(row),
            )
            return row
    finally:
        conn.close()


# ──────────────────────────────────────────────
# income_history CRUD / 对账
# ──────────────────────────────────────────────

_INCOME_HISTORY_COLUMNS = (
    "user_id",
    "username",
    "exchange",
    "symbol",
    "income_type",
    "income",
    "asset",
    "info_text",
    "trade_id",
    "tran_id",
    "income_time",
)


def _normalize_income_history_row(user_id: int, username: str, income_row: dict, *, exchange: str = "binance") -> dict:
    income_time = _coerce_utc_naive_datetime(income_row.get("time"))
    if user_id <= 0:
        raise ValueError("user_id must be positive")
    if income_time is None:
        raise ValueError("income row is missing a valid time")

    normalized_income_type = str(income_row.get("incomeType") or "").strip().upper()
    if not normalized_income_type:
        raise ValueError("income row is missing incomeType")

    return {
        "user_id": user_id,
        "username": str(username or "").strip(),
        "exchange": str(exchange or "binance").strip() or "binance",
        "symbol": str(income_row.get("symbol") or "").strip().upper(),
        "income_type": normalized_income_type,
        "income": str(income_row.get("income") or "0").strip() or "0",
        "asset": str(income_row.get("asset") or "").strip().upper(),
        "info_text": str(income_row.get("info") or "").strip(),
        "trade_id": str(income_row.get("tradeId") or "").strip(),
        "tran_id": str(income_row.get("tranId") or "").strip(),
        "income_time": income_time,
    }


def upsert_income_history_entry(user_id: int, username: str, income_row: dict, *, exchange: str = "binance") -> int:
    row = _normalize_income_history_row(user_id, username, income_row, exchange=exchange)
    _log_db_write("upsert", "income_history", row)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO income_history ({', '.join(_INCOME_HISTORY_COLUMNS)})
                VALUES ({', '.join(['%s'] * len(_INCOME_HISTORY_COLUMNS))})
                ON DUPLICATE KEY UPDATE
                    username = VALUES(username),
                    income = VALUES(income),
                    info_text = VALUES(info_text),
                    updated_at = CURRENT_TIMESTAMP
                """,
                [row[column] for column in _INCOME_HISTORY_COLUMNS],
            )
        conn.commit()
        _log_db_write_result("upsert", "income_history", user_id=user_id, income_type=row["income_type"], tran_id=row["tran_id"], affected_rows=1)
        return 1
    finally:
        conn.close()


def upsert_income_history_entries(user_id: int, username: str, income_rows: list[dict], *, exchange: str = "binance") -> int:
    written = 0
    for income_row in income_rows:
        written += upsert_income_history_entry(user_id, username, income_row, exchange=exchange)
    return written


def get_income_history_totals(user_id: int) -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS row_count,
                       SUM(COALESCE(income, 0)) AS total_income,
                       SUM(CASE WHEN income_type = 'REALIZED_PNL' THEN COALESCE(income, 0) ELSE 0 END) AS realized_pnl,
                       SUM(CASE WHEN income_type = 'COMMISSION' THEN COALESCE(income, 0) ELSE 0 END) AS commission,
                       SUM(CASE WHEN income_type = 'FUNDING_FEE' THEN COALESCE(income, 0) ELSE 0 END) AS funding_fee,
                       SUM(CASE WHEN income_type NOT IN ('REALIZED_PNL', 'COMMISSION', 'FUNDING_FEE') THEN COALESCE(income, 0) ELSE 0 END) AS other_income
                FROM income_history
                WHERE user_id = %s
                """,
                (user_id,),
            )
            return cur.fetchone() or {}
    finally:
        conn.close()


def get_filled_order_totals(user_id: int) -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT SUM(CASE WHEN status = 'FILLED' AND UPPER(COALESCE(trade_direction, '')) = 'CLOSE' THEN 1 ELSE 0 END) AS close_count,
                       SUM(CASE WHEN status = 'FILLED' THEN COALESCE(realized_pnl, 0) ELSE 0 END) AS pnl,
                       SUM(CASE WHEN status = 'FILLED' THEN COALESCE(commission, 0) ELSE 0 END) AS commission
                FROM orders
                WHERE user_id = %s
                """,
                (user_id,),
            )
            return cur.fetchone() or {}
    finally:
        conn.close()


def get_position_history_trade_totals(user_id: int) -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(DISTINCT COALESCE(NULLIF(position_id, 0), -id)) AS trade_count,
                       SUM(COALESCE(realized_pnl, 0)) AS pnl,
                       SUM(COALESCE(commission, 0)) AS commission
                FROM position_history
                WHERE user_id = %s
                """,
                (user_id,),
            )
            return cur.fetchone() or {}
    finally:
        conn.close()


def _decimal_or_zero(value) -> Decimal:
    return Decimal(str(value or 0))


def _decimal_or_none(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def get_income_reconciliation_summary(user_id: int) -> dict:
    income_totals = get_income_history_totals(user_id)
    order_totals = get_filled_order_totals(user_id)
    position_totals = get_position_history_trade_totals(user_id)

    initial_balance = _decimal_or_none(get_profile_initial_balance(user_id))
    current_balance = _decimal_or_none(get_profile_current_balance(user_id))
    balance_net = None if initial_balance is None or current_balance is None else current_balance - initial_balance

    income_total = _decimal_or_zero(income_totals.get("total_income"))
    income_realized_pnl = _decimal_or_zero(income_totals.get("realized_pnl"))
    income_commission = _decimal_or_zero(income_totals.get("commission"))
    income_funding_fee = _decimal_or_zero(income_totals.get("funding_fee"))
    income_other = _decimal_or_zero(income_totals.get("other_income"))
    order_pnl = _decimal_or_zero(order_totals.get("pnl"))
    order_commission = _decimal_or_zero(order_totals.get("commission"))
    position_pnl = _decimal_or_zero(position_totals.get("pnl"))
    position_commission = _decimal_or_zero(position_totals.get("commission"))

    unexplained_gap = None if balance_net is None else balance_net - income_total
    income_vs_order_realized_gap = income_realized_pnl - order_pnl
    income_vs_order_commission_gap = abs(income_commission) - order_commission
    income_vs_order_net_gap = income_total - (order_pnl - order_commission)

    return {
        "initial_balance": float(initial_balance) if initial_balance is not None else None,
        "current_balance": float(current_balance) if current_balance is not None else None,
        "balance_net": float(balance_net) if balance_net is not None else None,
        "income_row_count": int(income_totals.get("row_count") or 0),
        "income_total": float(income_total),
        "income_realized_pnl": float(income_realized_pnl),
        "income_commission": float(income_commission),
        "income_commission_cost": float(abs(income_commission)),
        "income_funding_fee": float(income_funding_fee),
        "income_other": float(income_other),
        "order_close_count": int(order_totals.get("close_count") or 0),
        "order_pnl": float(order_pnl),
        "order_commission": float(order_commission),
        "order_net": float(order_pnl - order_commission),
        "income_vs_order_realized_gap": float(income_vs_order_realized_gap),
        "income_vs_order_commission_gap": float(income_vs_order_commission_gap),
        "income_vs_order_net_gap": float(income_vs_order_net_gap),
        "position_trade_count": int(position_totals.get("trade_count") or 0),
        "position_pnl": float(position_pnl),
        "position_commission": float(position_commission),
        "position_net": float(position_pnl - position_commission),
        "unexplained_gap": float(unexplained_gap) if unexplained_gap is not None else None,
    }


def get_all_active_users_with_api_keys() -> list[dict]:
    """Return list of {id, username} for all active users who have API credentials."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username FROM users"
                " WHERE is_active = 1"
                "   AND binance_api_key IS NOT NULL AND binance_api_key != ''"
            )
            return cur.fetchall()
    finally:
        conn.close()


# ──────────────────────────────────────────────
# User CRUD（用户信息）
# ──────────────────────────────────────────────

def create_user(
    username: str,
    password_hash: str,
    role: str = "user",
    binance_api_key: str = "",
    binance_api_secret: str = "",
) -> Optional[int]:
    enc_key = encrypt_api_credential(binance_api_key) if binance_api_key else None
    enc_secret = encrypt_api_credential(binance_api_secret) if binance_api_secret else None
    _log_db_write(
        "insert",
        "users",
        {
            "username": username,
            "password_hash": password_hash,
            "role": role,
            "binance_api_key": enc_key,
            "binance_api_secret": enc_secret,
        },
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, role, binance_api_key, binance_api_secret)"
                " VALUES (%s, %s, %s, %s, %s)",
                (username, password_hash, role, enc_key, enc_secret),
            )
            conn.commit()
            _log_db_write_result("insert", "users", username=username, user_id=cur.lastrowid)
            return cur.lastrowid
    except pymysql.err.IntegrityError:
        _log_db_write_result("insert", "users", username=username, success=False, error="integrity_error")
        return None
    finally:
        conn.close()


def update_user_api_credentials(
    user_id: int,
    binance_api_key: str,
    binance_api_secret: str,
) -> bool:
    """Encrypt and persist Binance API credentials for a user."""
    enc_key = encrypt_api_credential(binance_api_key) if binance_api_key else None
    enc_secret = encrypt_api_credential(binance_api_secret) if binance_api_secret else None
    _log_db_write(
        "update",
        "users",
        {"user_id": user_id, "binance_api_key": enc_key, "binance_api_secret": enc_secret},
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET binance_api_key = %s, binance_api_secret = %s WHERE id = %s",
                (enc_key, enc_secret, user_id),
            )
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("update", "users", user_id=user_id, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


def get_user_by_username(username: str) -> Optional[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM users WHERE username = %s AND is_active = 1",
                (username,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            return cur.fetchone()
    finally:
        conn.close()


def get_all_users() -> list:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, role, is_active,"
                " binance_api_key, binance_api_secret,"
                " created_at, updated_at FROM users ORDER BY created_at"
            )
            return cur.fetchall()
    finally:
        conn.close()


def update_user_password(user_id: int, password_hash: str) -> bool:
    _log_db_write("update", "users", {"user_id": user_id, "password_hash": password_hash})
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (password_hash, user_id),
            )
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("update", "users", user_id=user_id, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


def update_username(user_id: int, username: str) -> bool:
    _log_db_write("update", "users", {"user_id": user_id, "username": username})
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET username = %s WHERE id = %s",
                (username, user_id),
            )
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("update", "users", user_id=user_id, username=username, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


def update_user_role(user_id: int, role: str) -> bool:
    _log_db_write("update", "users", {"user_id": user_id, "role": role})
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET role = %s WHERE id = %s",
                (role, user_id),
            )
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("update", "users", user_id=user_id, role=role, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


def deactivate_user(user_id: int) -> bool:
    _log_db_write("update", "users", {"user_id": user_id, "is_active": 0})
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_active = 0 WHERE id = %s", (user_id,)
            )
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("update", "users", user_id=user_id, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


def hard_delete_user(user_id: int) -> bool:
    """Permanently delete a user row. Caller must ensure no FK-constrained child rows remain."""
    _log_db_write("delete", "users", {"user_id": user_id})
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("delete", "users", user_id=user_id, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


def activate_user(user_id: int) -> bool:
    _log_db_write("update", "users", {"user_id": user_id, "is_active": 1})
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_active = 1 WHERE id = %s", (user_id,)
            )
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("update", "users", user_id=user_id, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


# ──────────────────────────────────────────────
# Order CRUD（委托 / 订单）
# ──────────────────────────────────────────────

def create_order(
    user_id: int,
    username: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float],
    status: str,
    binance_order_id: Optional[str] = None,   # 兼容旧调用方，存入 exchange_order_id
    algo_id: Optional[str] = None,
    algo_client_id: Optional[str] = None,
    error_message: Optional[str] = None,
    exchange: str = "binance",
    stop_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    sl_price: Optional[float] = None,
    client_order_id: Optional[str] = None,
    trade_direction: Optional[str] = None,     # OPEN | CLOSE
    position_mode: Optional[str] = None,       # SINGLE | DUAL | UNKNOWN
    position_id: Optional[int] = None,         # 关联持仓ID
    realized_pnl: Optional[float] = None,
    reduce_only: bool = False,
    post_only: bool = False,
    order_category: str = 'Basic',             # Basic | Conditional
    source: str = 'trade_relay',               # trade_relay | external
) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            normalized_order_category = _normalize_order_category(order_type, order_category)
            normalized_algo_id = str(algo_id).strip() if algo_id is not None and str(algo_id).strip() else None
            normalized_algo_client_id = str(algo_client_id).strip() if algo_client_id is not None and str(algo_client_id).strip() else None
            normalized_exchange_order_id = str(binance_order_id).strip() if binance_order_id is not None and str(binance_order_id).strip() else None
            normalized_client_order_id = str(client_order_id).strip() if client_order_id is not None and str(client_order_id).strip() else None
            if normalized_order_category == "Conditional":
                if normalized_algo_id is None and normalized_exchange_order_id is not None:
                    normalized_algo_id = normalized_exchange_order_id
                    normalized_exchange_order_id = None
                if normalized_algo_client_id is None and normalized_client_order_id is not None:
                    normalized_algo_client_id = normalized_client_order_id
                    normalized_client_order_id = None
            _log_db_write(
                "insert",
                "orders",
                {
                    "user_id": user_id,
                    "username": username,
                    "exchange": exchange,
                    "symbol": symbol,
                    "side": side,
                    "order_type": order_type,
                    "quantity": quantity,
                    "price": price,
                    "stop_price": stop_price,
                    "tp_price": tp_price,
                    "sl_price": sl_price,
                    "status": status,
                    "algo_id": normalized_algo_id,
                    "algo_client_id": normalized_algo_client_id,
                    "exchange_order_id": normalized_exchange_order_id,
                    "client_order_id": normalized_client_order_id,
                    "realized_pnl": realized_pnl,
                    "trade_direction": trade_direction,
                    "position_mode": position_mode,
                    "reduce_only": int(reduce_only),
                    "post_only": int(post_only),
                    "position_id": position_id,
                    "order_category": normalized_order_category,
                    "source": source,
                    "error_message": error_message,
                },
            )
            cur.execute(
                """INSERT INTO orders
                   (user_id, username, exchange, source, symbol, side, order_type,
                    quantity, price, stop_price, tp_price, sl_price, status,
                    algo_id, algo_client_id, exchange_order_id, client_order_id,
                    trade_direction, position_mode, reduce_only, post_only, position_id, realized_pnl, order_category, error_message)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    user_id, username, exchange, source, symbol, side, order_type,
                    quantity, price, stop_price, tp_price, sl_price, status,
                    normalized_algo_id, normalized_algo_client_id, normalized_exchange_order_id, normalized_client_order_id,
                    trade_direction, position_mode, int(reduce_only), int(post_only), position_id, realized_pnl, normalized_order_category, error_message,
                ),
            )
            conn.commit()
            _log_db_write_result(
                "insert",
                "orders",
                db_id=cur.lastrowid,
                user_id=user_id,
                algo_id=normalized_algo_id,
                algo_client_id=normalized_algo_client_id,
                exchange_order_id=normalized_exchange_order_id,
                order_type=order_type,
                status=status,
                order_category=normalized_order_category,
            )
            return cur.lastrowid
    finally:
        conn.close()


def update_order_status(
    order_id: int,
    status: str,
    filled_qty: Optional[float] = None,
    avg_price: Optional[float] = None,
    filled_at = None,
    realized_pnl: Optional[float] = None,
    commission: Optional[float] = None,
    commission_asset: Optional[str] = None,
    error_message: Optional[str] = None,
) -> bool:
    """更新订单状态及成交信息。"""
    current_row = get_order_by_id(order_id)
    if not current_row:
        return False

    updates: list[tuple[str, object]] = [("status", status)]
    if filled_qty is not None:
        updates.append(("filled_qty", filled_qty))
    if avg_price is not None:
        updates.append(("avg_price", avg_price))
    normalized_filled_at = _coerce_utc_naive_datetime(filled_at)
    if normalized_filled_at is not None:
        updates.append(("filled_at", normalized_filled_at))
    if realized_pnl is not None:
        updates.append(("realized_pnl", realized_pnl))
    if commission is not None:
        updates.append(("commission", commission))
    if commission_asset is not None:
        updates.append(("commission_asset", commission_asset))
    if error_message is not None:
        updates.append(("error_message", error_message))

    changed_updates = _filter_changed_order_updates(current_row, updates)
    if not changed_updates:
        return False

    fields = [f"{field} = %s" for field, _ in changed_updates]
    params: list = [value for _, value in changed_updates]
    params.append(order_id)

    _log_db_write(
        "update",
        "orders",
        {"order_id": order_id, **dict(changed_updates)},
    )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE orders SET {', '.join(fields)} WHERE id = %s", params
            )
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("update", "orders", order_id=order_id, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


def update_order_trade_details_sync_state(
    order_id: int,
    *,
    attempts: Optional[int] = None,
    next_retry_at=...,
    last_error=...,
) -> bool:
    current_row = get_order_by_id(order_id)
    if not current_row:
        return False

    updates: list[tuple[str, object]] = []

    if attempts is not None:
        updates.append(("trade_details_sync_attempts", max(0, int(attempts))))

    if next_retry_at is not ...:
        normalized_next_retry_at = _coerce_utc_naive_datetime(next_retry_at)
        updates.append(("trade_details_sync_next_retry_at", normalized_next_retry_at))
    else:
        normalized_next_retry_at = None

    if last_error is not ...:
        updates.append(("trade_details_sync_last_error", last_error))

    changed_updates = _filter_changed_order_updates(current_row, updates)
    if not changed_updates:
        return False

    fields = [f"{field} = %s" for field, _ in changed_updates]
    params: list = [value for _, value in changed_updates]

    params.append(order_id)
    _log_db_write(
        "update",
        "orders",
        {"order_id": order_id, **dict(changed_updates)},
    )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE orders SET {', '.join(fields)} WHERE id = %s",
                params,
            )
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("update", "orders", order_id=order_id, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


def clear_order_trade_details_sync_state(order_id: int) -> bool:
    return update_order_trade_details_sync_state(
        order_id,
        attempts=0,
        next_retry_at=None,
        last_error=None,
    )


def schedule_order_trade_details_retry(order_id: int, *, delay_seconds: float, error_message: str) -> bool:
    row = get_order_by_id(order_id)
    if not row:
        return False

    current_attempts = int(row.get("trade_details_sync_attempts") or 0)
    next_retry_at = _utc_now_naive() + timedelta(seconds=max(0.0, float(delay_seconds)))
    trimmed_error = (error_message or "trade_details_sync_pending").strip()[:2000]
    return update_order_trade_details_sync_state(
        order_id,
        attempts=current_attempts + 1,
        next_retry_at=next_retry_at,
        last_error=trimmed_error,
    )


def get_due_order_trade_details_retry_candidates(limit: int = 100) -> list[dict]:
    safe_limit = max(1, min(int(limit or 100), 500))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM orders
                WHERE status = 'FILLED'
                  AND exchange_order_id IS NOT NULL
                  AND TRIM(COALESCE(exchange_order_id, '')) <> ''
                  AND UPPER(COALESCE(trade_direction, '')) IN ('OPEN', 'CLOSE')
                  AND (
                                commission IS NULL
                     OR commission_asset IS NULL
                     OR TRIM(COALESCE(commission_asset, '')) = ''
                            OR ABS(ABS(COALESCE(quantity, 0)) - ABS(COALESCE(filled_qty, 0))) > 0.000000001
                     OR (UPPER(COALESCE(trade_direction, '')) = 'CLOSE' AND realized_pnl IS NULL)
                  )
                  AND (
                        trade_details_sync_next_retry_at IS NULL
                     OR trade_details_sync_next_retry_at <= UTC_TIMESTAMP()
                  )
                ORDER BY COALESCE(trade_details_sync_next_retry_at, updated_at, created_at) ASC, id ASC
                LIMIT %s
                """,
                (safe_limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def update_order_close_tpsl_sync_state(
    order_id: int,
    *,
    attempts: Optional[int] = None,
    next_retry_at=...,
    last_error=...,
) -> bool:
    current_row = get_order_by_id(order_id)
    if not current_row:
        return False

    updates: list[tuple[str, object]] = []

    if attempts is not None:
        updates.append(("close_tpsl_sync_attempts", max(0, int(attempts))))

    if next_retry_at is not ...:
        normalized_next_retry_at = _coerce_utc_naive_datetime(next_retry_at)
        updates.append(("close_tpsl_sync_next_retry_at", normalized_next_retry_at))
    else:
        normalized_next_retry_at = None

    if last_error is not ...:
        updates.append(("close_tpsl_sync_last_error", last_error))

    changed_updates = _filter_changed_order_updates(current_row, updates)
    if not changed_updates:
        return False

    fields = [f"{field} = %s" for field, _ in changed_updates]
    params: list = [value for _, value in changed_updates]

    params.append(order_id)
    _log_db_write(
        "update",
        "orders",
        {"order_id": order_id, **dict(changed_updates)},
    )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE orders SET {', '.join(fields)} WHERE id = %s",
                params,
            )
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("update", "orders", order_id=order_id, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


def clear_order_close_tpsl_sync_state(order_id: int) -> bool:
    return update_order_close_tpsl_sync_state(
        order_id,
        attempts=0,
        next_retry_at=None,
        last_error=None,
    )


def enqueue_order_close_tpsl_refresh(order_id: int, *, delay_seconds: float, error_message: str) -> bool:
    next_retry_at = _utc_now_naive() + timedelta(seconds=max(0.0, float(delay_seconds)))
    trimmed_error = (error_message or "close_tpsl_sync_pending").strip()[:2000]
    return update_order_close_tpsl_sync_state(
        order_id,
        next_retry_at=next_retry_at,
        last_error=trimmed_error,
    )


def schedule_order_close_tpsl_retry(order_id: int, *, delay_seconds: float, error_message: str) -> bool:
    row = get_order_by_id(order_id)
    if not row:
        return False

    current_attempts = int(row.get("close_tpsl_sync_attempts") or 0)
    next_retry_at = _utc_now_naive() + timedelta(seconds=max(0.0, float(delay_seconds)))
    trimmed_error = (error_message or "close_tpsl_sync_pending").strip()[:2000]
    return update_order_close_tpsl_sync_state(
        order_id,
        attempts=current_attempts + 1,
        next_retry_at=next_retry_at,
        last_error=trimmed_error,
    )


def get_due_order_close_tpsl_retry_candidates(limit: int = 100) -> list[dict]:
    safe_limit = max(1, min(int(limit or 100), 500))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM orders
                WHERE UPPER(COALESCE(trade_direction, '')) = 'CLOSE'
                  AND close_tpsl_sync_next_retry_at IS NOT NULL
                  AND close_tpsl_sync_next_retry_at <= UTC_TIMESTAMP()
                ORDER BY close_tpsl_sync_next_retry_at ASC, id ASC
                LIMIT %s
                """,
                (safe_limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def has_pending_close_tpsl_refresh(*, user_id: int, symbol: str, position_side: str) -> bool:
    normalized_symbol = str(symbol or "").strip().upper()
    normalized_side = str(position_side or "").strip().upper()
    if not normalized_symbol or normalized_side not in {"LONG", "SHORT"}:
        return False

    close_side = "SELL" if normalized_side == "LONG" else "BUY"
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM orders
                WHERE user_id = %s
                  AND UPPER(COALESCE(trade_direction, '')) = 'CLOSE'
                  AND UPPER(COALESCE(symbol, '')) = %s
                  AND UPPER(COALESCE(side, '')) = %s
                  AND close_tpsl_sync_next_retry_at IS NOT NULL
                LIMIT 1
                """,
                (int(user_id), normalized_symbol, close_side),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def update_order_source(order_id: int, source: str) -> bool:
    """Flip orders.source for an adopted external order that was later confirmed as a trade_relay order."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE orders SET source = %s WHERE id = %s AND source != %s", (source, order_id, source))
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def update_order_metadata(order_id: int, **fields_to_update) -> bool:
    """Update selected order fields by primary key."""
    allowed_fields = {
        "quantity",
        "price",
        "stop_price",
        "algo_id",
        "algo_client_id",
        "exchange_order_id",
        "client_order_id",
        "trade_direction",
        "error_message",
    }
    fields = {key: value for key, value in fields_to_update.items() if key in allowed_fields and value is not None}
    if not fields:
        return False

    current_row = get_order_by_id(order_id)
    if not current_row:
        return False

    changed_fields = {
        key: value
        for key, value in fields.items()
        if _order_field_value_changed(current_row, key, value)
    }
    if not changed_fields:
        return False

    assignments = [f"{key} = %s" for key in changed_fields]
    params = list(changed_fields.values()) + [order_id]

    _log_db_write("update", "orders", {"order_id": order_id, **changed_fields})

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE orders SET {', '.join(assignments)} WHERE id = %s",
                params,
            )
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("update", "orders", order_id=order_id, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


def update_order_status_by_exchange_id(
    username: str,
    exchange_order_id: str,
    status: str,
    filled_qty: Optional[float] = None,
    avg_price: Optional[float] = None,
    filled_at = None,
    realized_pnl: Optional[float] = None,
    commission: Optional[float] = None,
    commission_asset: Optional[str] = None,
    error_message: Optional[str] = None,
) -> bool:
    """Update an order row by username + exchange_order_id."""
    current_row = get_order_by_exchange_id(username, exchange_order_id)
    if not current_row:
        return False

    updates: list[tuple[str, object]] = [("status", status)]
    if filled_qty is not None:
        updates.append(("filled_qty", filled_qty))
    if avg_price is not None:
        updates.append(("avg_price", avg_price))
    normalized_filled_at = _coerce_utc_naive_datetime(filled_at)
    if normalized_filled_at is not None:
        updates.append(("filled_at", normalized_filled_at))
    if realized_pnl is not None:
        updates.append(("realized_pnl", realized_pnl))
    if commission is not None:
        updates.append(("commission", commission))
    if commission_asset is not None:
        updates.append(("commission_asset", commission_asset))
    if error_message is not None:
        updates.append(("error_message", error_message))

    changed_updates = _filter_changed_order_updates(current_row, updates)
    if not changed_updates:
        return False

    fields = [f"{field} = %s" for field, _ in changed_updates]
    params: list = [value for _, value in changed_updates]
    params.extend([username, exchange_order_id])

    _log_db_write(
        "update",
        "orders",
        {"username": username, "exchange_order_id": exchange_order_id, **dict(changed_updates)},
    )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""UPDATE orders
                       SET {', '.join(fields)}
                     WHERE username = %s AND exchange_order_id = %s""",
                params,
            )
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("update", "orders", username=username, exchange_order_id=exchange_order_id, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


def get_recent_fills(limit: int = 20) -> list:
    """Return most recent FILLED orders for the ticker broadcast.

    Returns rows: {username, symbol, side, filled_qty, avg_price, commission, created_at}
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT u.username, o.symbol, o.side, o.filled_qty, o.avg_price,
                          COALESCE(o.commission, 0) AS commission, o.created_at
                   FROM orders o
                   JOIN users u ON u.id = o.user_id
                   WHERE o.status = 'FILLED'
                     AND o.filled_qty > 0
                     AND o.avg_price IS NOT NULL
                   ORDER BY o.created_at DESC
                   LIMIT %s""",
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_ticker_messages(limit: int = 10) -> list:
    """Return most recent ticker_messages rows for the scrolling broadcast.

    Returns rows: {id, contents_zh, contents_en, created_at}
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, contents_zh, contents_en, created_at"
                " FROM ticker_messages"
                " ORDER BY created_at DESC"
                " LIMIT %s",
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def add_ticker_message(contents_zh: str, contents_en: str) -> int:
    """Insert a new ticker_messages row and return its id."""
    _log_db_write("insert", "ticker_messages", {"contents_zh": contents_zh, "contents_en": contents_en})
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ticker_messages (contents_zh, contents_en) VALUES (%s, %s)",
                (contents_zh, contents_en),
            )
            conn.commit()
            _log_db_write_result("insert", "ticker_messages", msg_id=cur.lastrowid)
            return cur.lastrowid
    finally:
        conn.close()


def delete_ticker_message(msg_id: int) -> bool:
    """Delete a ticker_messages row by id. Returns True if a row was deleted."""
    _log_db_write("delete", "ticker_messages", {"msg_id": msg_id})
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ticker_messages WHERE id = %s", (msg_id,))
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("delete", "ticker_messages", msg_id=msg_id, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


# ──────────────────────────────────────────────
# Tickers（交易对）
# ──────────────────────────────────────────────

def get_ticker_symbols(status: str = "TRADING") -> list[str]:
    """Return sorted list of symbol strings from the tickers table.

    Pass status=None to return all symbols regardless of status.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if status:
                cur.execute(
                    "SELECT symbol FROM tickers WHERE status = %s ORDER BY symbol",
                    (status,),
                )
            else:
                cur.execute("SELECT symbol FROM tickers ORDER BY symbol")
            rows = cur.fetchall()
            return [r["symbol"] for r in rows]
    finally:
        conn.close()


def sync_tickers(rows: list[dict]) -> int:
    """Bulk-upsert tickers from Binance exchange info.

    Each dict in rows must have at minimum a 'symbol' key.
    Returns the number of rows inserted / updated.
    """
    if not rows:
        return 0
    _log_db_write(
        "upsert_many",
        "tickers",
        {"row_count": len(rows), "symbols_sample": [row.get("symbol") for row in rows[:10]]},
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            sql = """
                INSERT INTO tickers
                    (symbol, pair, base_asset, quote_asset,
                     delivery_date, onboard_date, status,
                     price_precision, quantity_precision,
                     base_asset_precision, quote_asset_precision,
                     max_price, min_price, tick_size)
                VALUES
                    (%(symbol)s, %(pair)s, %(base_asset)s, %(quote_asset)s,
                     %(delivery_date)s, %(onboard_date)s, %(status)s,
                     %(price_precision)s, %(quantity_precision)s,
                     %(base_asset_precision)s, %(quote_asset_precision)s,
                     %(max_price)s, %(min_price)s, %(tick_size)s)
                ON DUPLICATE KEY UPDATE
                    pair                  = VALUES(pair),
                    base_asset            = VALUES(base_asset),
                    quote_asset           = VALUES(quote_asset),
                    delivery_date         = VALUES(delivery_date),
                    onboard_date          = VALUES(onboard_date),
                    status                = VALUES(status),
                    price_precision       = VALUES(price_precision),
                    quantity_precision    = VALUES(quantity_precision),
                    base_asset_precision  = VALUES(base_asset_precision),
                    quote_asset_precision = VALUES(quote_asset_precision),
                    max_price             = VALUES(max_price),
                    min_price             = VALUES(min_price),
                    tick_size             = VALUES(tick_size),
                    updated_at            = CURRENT_TIMESTAMP
            """
            cur.executemany(sql, rows)
            conn.commit()
            _log_db_write_result("upsert_many", "tickers", row_count=len(rows), affected_rows=cur.rowcount)
            return cur.rowcount
    finally:
        conn.close()


def get_all_orders(limit: int = 200) -> list:
    """返回最近的所有订单（当前委托 + 历史订单），兼容旧代码字段名。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, username, symbol, side, order_type, quantity, price,
                          status, exchange_order_id AS binance_order_id, created_at
                   FROM orders
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_active_orders(user_id: Optional[int] = None) -> list:
    """返回 Basic 当前委托（未完结订单）。"""
    placeholders = ", ".join(["%s"] * len(ACTIVE_STATUSES))
    params: list = list(ACTIVE_STATUSES)
    sql = f"""SELECT * FROM orders
              WHERE status IN ({placeholders})
                AND order_category = 'Basic'"""
    if user_id is not None:
        sql += " AND user_id = %s"
        params.append(user_id)
    sql += " ORDER BY created_at DESC"

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def get_active_orders_for_sync() -> list:
    """Return active basic orders for backend startup/status sync."""
    placeholders = ", ".join(["%s"] * len(ACTIVE_STATUSES))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT * FROM orders
                      WHERE status IN ({placeholders})
                        AND order_category = 'Basic'
                      ORDER BY created_at DESC""",
                list(ACTIVE_STATUSES),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_active_orders_for_user(username: str) -> list:
    """返回指定用户的当前委托（含可继续监听真实订单ID的条件单）。"""
    placeholders = ", ".join(["%s"] * len(ACTIVE_STATUSES))
    params: list = list(ACTIVE_STATUSES)
    params.append(username)
    sql = f"""SELECT * FROM orders
              WHERE status IN ({placeholders})
                AND username = %s
              ORDER BY created_at DESC"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def get_active_order_usernames() -> list[str]:
    """Return usernames that currently have active orders in any category."""
    placeholders = ", ".join(["%s"] * len(ACTIVE_STATUSES))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT DISTINCT username
                       FROM orders
                      WHERE status IN ({placeholders})
                        AND username IS NOT NULL
                        AND username <> ''""",
                list(ACTIVE_STATUSES),
            )
            rows = cur.fetchall()
            return [str(row["username"]) for row in rows if row.get("username")]
    finally:
        conn.close()


def get_all_active_usernames() -> list[str]:
    """Return usernames for all active (non-disabled) users."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username FROM users WHERE is_active = 1 AND username IS NOT NULL AND username <> ''"
            )
            rows = cur.fetchall()
            return [str(row["username"]) for row in rows if row.get("username")]
    finally:
        conn.close()


def get_order_history(
    user_id: Optional[int] = None,
    limit: int = 200,
    trade_direction: Optional[str] = None,
) -> list:
    """返回历史订单（已完结：FILLED / CANCELED / EXPIRED / REJECTED / FAILED / ERROR）。"""
    placeholders = ", ".join(["%s"] * len(ACTIVE_STATUSES))
    params: list = list(ACTIVE_STATUSES)
    sql = f"""SELECT * FROM orders
              WHERE status NOT IN ({placeholders})"""
    if user_id is not None:
        sql += " AND user_id = %s"
        params.append(user_id)
    if trade_direction:
        sql += " AND trade_direction = %s"
        params.append(trade_direction.upper())
    sql += " ORDER BY updated_at DESC, created_at DESC LIMIT %s"
    params.append(limit)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def query_orders(
    *,
    limit: int = 200,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    order_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    status: Optional[str] = None,
    trade_direction: Optional[str] = None,
) -> list:
    """Return orders with optional filters for user, order id, time range, and status."""
    sql = "SELECT * FROM orders WHERE 1 = 1"
    params: list = []

    if user_id is not None:
        sql += " AND user_id = %s"
        params.append(user_id)

    if username:
        sql += " AND username LIKE %s"
        params.append(f"%{username}%")

    if order_id:
        sql += " AND (CAST(id AS CHAR) LIKE %s OR exchange_order_id LIKE %s OR algo_id LIKE %s OR algo_client_id LIKE %s OR client_order_id LIKE %s)"
        like_value = f"%{order_id}%"
        params.extend([like_value, like_value, like_value, like_value, like_value])

    if start_time:
        sql += " AND created_at >= %s"
        params.append(start_time)

    if end_time:
        sql += " AND created_at <= %s"
        params.append(end_time)

    if status:
        sql += " AND status = %s"
        params.append(status)

    if trade_direction:
        sql += " AND trade_direction = %s"
        params.append(trade_direction.upper())

    sql += " ORDER BY COALESCE(updated_at, created_at) DESC LIMIT %s"
    params.append(limit)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def get_recent_platform_trades(limit: int = 30) -> list:
    """返回平台内所有用户最近的已成交订单。
    每行包含: username, symbol, side, order_type, order_category, filled_qty, avg_price,
    realized_pnl, commission, commission_asset, created_at
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT username, symbol, side, trade_direction, order_type, order_category,
                      filled_qty, price, avg_price, realized_pnl,
                      COALESCE(commission, 0) AS commission,
                      commission_asset, created_at
                FROM orders
                WHERE status = 'FILLED'
                  AND filled_qty > 0
                  AND avg_price IS NOT NULL
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_user_filled_order_markers(
    *,
    user_id: int,
    symbol: str,
    limit: int = 200,
) -> list:
    """Return filled orders for one user+symbol for chart marker rendering."""
    normalized_symbol = str(symbol or "").strip().upper()
    if not normalized_symbol:
        return []

    safe_limit = max(1, min(int(limit), 500))
    _log_db_query(
        "orders",
        "get_user_filled_order_markers",
        user_id=user_id,
        symbol=normalized_symbol,
        limit=safe_limit,
    )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, user_id, username, symbol, side, trade_direction,
                       order_type, order_category, filled_qty, avg_price,
                      created_at, updated_at, filled_at
                                FROM orders FORCE INDEX (idx_user_symbol_status_filled_at)
                WHERE user_id = %s
                                    AND symbol = %s
                  AND status = 'FILLED'
                  AND filled_qty > 0
                  AND avg_price IS NOT NULL
                                    ORDER BY filled_at DESC
                LIMIT %s
                """,
                (user_id, normalized_symbol, safe_limit),
            )
            rows = cur.fetchall()
            _log_db_query_result(
                "orders",
                "get_user_filled_order_markers",
                user_id=user_id,
                symbol=normalized_symbol,
                limit=safe_limit,
                count=len(rows),
            )
            return rows
    finally:
        conn.close()


def get_daily_pnl(user_id: int) -> list:
    """Return daily profile rows using the position_id-based daily_profile aggregation."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT profile_date AS date,
                       pnl,
                       account_balance,
                       commission,
                       trade_count AS trades,
                       win_rate,
                       win_count
                FROM daily_profile
                WHERE user_id = %s
                ORDER BY profile_date ASC
                """,
                (user_id,),
            )
            daily_profile_rows = cur.fetchall() or []
    finally:
        conn.close()

    merged_rows: list[dict] = []
    for daily_row in daily_profile_rows:
        merged_rows.append(
            {
                "date": _coerce_utc_date(daily_row["date"]),
                "pnl": daily_row.get("pnl") or 0,
                "account_balance": daily_row.get("account_balance"),
                "commission": daily_row.get("commission") or 0,
                "net_pnl": (daily_row.get("pnl") or 0) - (daily_row.get("commission") or 0),
                "trades": int(daily_row.get("trades") or 0),
                "win_rate": float(daily_row.get("win_rate") or 0),
                "win_count": int(daily_row.get("win_count") or 0),
            }
        )

    return merged_rows


def get_daily_profile_leaderboard(
    profile_date: Optional[date] = None,
    limit: int = 10,
) -> list:
    leaderboard_date = profile_date or _utc_now_naive().date()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id,
                       username,
                       profile_date AS date,
                       pnl,
                       account_balance,
                       trade_count AS trades,
                       win_rate,
                       win_count,
                       commission
                FROM daily_profile
                WHERE profile_date = %s
                """,
                (leaderboard_date,),
            )
            daily_profile_rows = cur.fetchall() or []
    finally:
        conn.close()

    merged_rows = []
    for daily_row in daily_profile_rows:
        merged_rows.append(
            {
                "user_id": int(daily_row.get("user_id") or 0),
                "username": str(daily_row.get("username") or ""),
                "date": leaderboard_date,
                "pnl": daily_row.get("pnl") or 0,
                "trades": int(daily_row.get("trades") or 0),
                "win_rate": float(daily_row.get("win_rate") or 0),
                "commission": daily_row.get("commission") or 0,
                "net_pnl": (daily_row.get("pnl") or 0) - (daily_row.get("commission") or 0),
                "account_balance": daily_row.get("account_balance"),
            }
        )

    merged_rows.sort(
        key=lambda row: (
            -float(row.get("pnl") or 0),
            -float(row.get("win_rate") or 0),
            -int(row.get("trades") or 0),
            str(row.get("username") or ""),
        )
    )
    return merged_rows[:limit]


def get_all_time_profile_leaderboard(limit: int = 20) -> list:
    return get_all_time_profile_leaderboard_for_days(limit=limit, days=None)


def get_all_time_profile_leaderboard_for_days(
    *,
    limit: int = 20,
    days: Optional[int] = None,
) -> list:
    start_date: date | None = None
    if days is not None and days > 0:
        start_date = _utc_now_naive().date() - timedelta(days=days - 1)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            daily_profile_sql = [
                """
                SELECT user_id,
                       COALESCE(MAX(NULLIF(TRIM(COALESCE(username, '')), '')), '') AS username,
                       SUM(COALESCE(pnl, 0)) AS pnl,
                       SUM(COALESCE(trade_count, 0)) AS trades,
                       SUM(COALESCE(win_count, 0)) AS win_count,
                       CASE
                           WHEN SUM(COALESCE(trade_count, 0)) = 0 THEN 0
                           ELSE SUM(COALESCE(win_count, 0)) / SUM(COALESCE(trade_count, 0)) * 100
                       END AS win_rate,
                       SUM(COALESCE(commission, 0)) AS commission
                FROM daily_profile
                WHERE 1 = 1
                """
            ]
            daily_profile_params: list[object] = []
            income_sql = [
                """
                SELECT user_id,
                       COALESCE(MAX(NULLIF(TRIM(COALESCE(username, '')), '')), '') AS username,
                       SUM(CASE WHEN income_type = 'REALIZED_PNL' THEN COALESCE(income, 0) ELSE 0 END) AS pnl,
                       SUM(CASE WHEN income_type = 'COMMISSION' THEN ABS(COALESCE(income, 0)) ELSE 0 END) AS commission
                FROM income_history
                WHERE 1 = 1
                """
            ]
            income_params: list[object] = []
            if start_date is not None:
                daily_profile_sql.append("AND profile_date >= %s")
                daily_profile_params.append(start_date)
                income_sql.append("AND DATE(income_time) >= %s")
                income_params.append(start_date)

            daily_profile_sql.append("GROUP BY user_id")
            income_sql.append("GROUP BY user_id")

            cur.execute("\n".join(daily_profile_sql), daily_profile_params)
            daily_profile_rows = cur.fetchall() or []
            cur.execute("\n".join(income_sql), income_params)
            income_rows = cur.fetchall() or []
    finally:
        conn.close()

    daily_profile_by_user = {int(row["user_id"]): row for row in daily_profile_rows}
    income_by_user = {int(row["user_id"]): row for row in income_rows}
    user_ids = set(daily_profile_by_user) | set(income_by_user)

    merged_rows = []
    for ranked_user_id in user_ids:
        daily_row = daily_profile_by_user.get(ranked_user_id, {})
        income_row = income_by_user.get(ranked_user_id, {})
        merged_rows.append(
            {
                "user_id": ranked_user_id,
                "username": str(income_row.get("username") or daily_row.get("username") or ""),
                "pnl": income_row.get("pnl", daily_row.get("pnl") or 0),
                "trades": int(daily_row.get("trades") or 0),
                "win_rate": float(daily_row.get("win_rate") or 0),
                "commission": income_row.get("commission", daily_row.get("commission") or 0),
            }
        )

    merged_rows.sort(
        key=lambda row: (
            -float(row.get("pnl") or 0),
            -float(row.get("win_rate") or 0),
            -int(row.get("trades") or 0),
            str(row.get("username") or ""),
        )
    )
    return merged_rows[:limit]


def get_total_commission_by_asset(user_id: int) -> list:
    """Return total commission grouped by commission_asset for a user.

    Each row: { asset: str, total: float }
    Prefer income_history because it is the exchange-level source of truth.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(NULLIF(TRIM(asset), ''), 'UNKNOWN') AS asset,
                       SUM(ABS(COALESCE(income, 0))) AS total
                FROM income_history
                WHERE user_id = %s
                  AND income_type = 'COMMISSION'
                GROUP BY COALESCE(NULLIF(TRIM(asset), ''), 'UNKNOWN')
                ORDER BY asset ASC
                """,
                (user_id,),
            )
            income_rows = cur.fetchall() or []
            if income_rows:
                return income_rows

            cur.execute(
                """
                SELECT COALESCE(NULLIF(TRIM(commission_asset), ''), 'UNKNOWN') AS asset,
                       SUM(COALESCE(commission, 0)) AS total
                FROM orders
                WHERE user_id = %s
                  AND status = 'FILLED'
                  AND (commission IS NOT NULL OR commission_asset IS NOT NULL)
                GROUP BY COALESCE(NULLIF(TRIM(commission_asset), ''), 'UNKNOWN')
                ORDER BY asset ASC
                """,
                (user_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_profile_initial_balance(user_id: int) -> float | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT account_balance, pnl, commission
                FROM daily_profile
                WHERE user_id = %s
                  AND account_balance IS NOT NULL
                ORDER BY profile_date ASC, id ASC
                LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone() or {}
    finally:
        conn.close()

    account_balance = row.get("account_balance")
    if account_balance is None:
        return None
    return float(account_balance or 0) - float(row.get("pnl") or 0) + float(row.get("commission") or 0)


def get_profile_current_balance(user_id: int) -> float | None:
    summary = get_account_summary_from_db(user_id, None) or {}
    wallet_balance = summary.get("wallet_balance")
    if wallet_balance is not None:
        return float(wallet_balance)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT account_balance
                FROM daily_profile
                WHERE user_id = %s
                  AND account_balance IS NOT NULL
                ORDER BY profile_date DESC, id DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone() or {}
    finally:
        conn.close()

    account_balance = row.get("account_balance")
    return float(account_balance) if account_balance is not None else None


# ──────────────────────────────────────────────
# Position CRUD（头寸信息）
# ──────────────────────────────────────────────

def upsert_position(
    user_id: int,
    username: str,
    symbol: str,
    quantity: float,
    avg_entry_price: Optional[float] = None,
    liquidation_price: Optional[float] = None,
    unrealized_pnl: Optional[float] = None,
    realized_pnl: float = 0.0,
    leverage: int = 1,
    margin_type: str = "CROSS",
    position_side: str = "BOTH",
    position_mode: str = "UNKNOWN",
    status: str = "OPEN",
    exchange: str = "binance",
) -> None:
    """插入或更新当前统一结构的持仓记录。"""
    _log_db_write(
        "upsert",
        "positions",
        {
            "user_id": user_id,
            "username": username,
            "exchange": exchange,
            "symbol": symbol,
            "position_side": position_side,
            "position_mode": position_mode,
            "status": status,
            "quantity": quantity,
            "avg_entry_price": avg_entry_price,
            "liquidation_price": liquidation_price,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl,
            "leverage": leverage,
            "margin_type": margin_type,
        },
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO positions
                   (user_id, username, exchange, symbol, position_side, position_mode, status, open_position_slot,
                    quantity, avg_entry_price, liquidation_price, unrealized_pnl, realized_pnl,
                    leverage, margin_type)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                       username        = VALUES(username),
                       status          = VALUES(status),
                       open_position_slot = VALUES(open_position_slot),
                       quantity        = VALUES(quantity),
                       avg_entry_price = VALUES(avg_entry_price),
                       liquidation_price = VALUES(liquidation_price),
                       unrealized_pnl  = VALUES(unrealized_pnl),
                       realized_pnl    = VALUES(realized_pnl),
                       leverage        = VALUES(leverage),
                       position_mode   = VALUES(position_mode),
                       margin_type     = VALUES(margin_type),
                       updated_at      = CURRENT_TIMESTAMP""",
                (
                    user_id, username, exchange, symbol, position_side, position_mode,
                    status, 1 if str(status or "").strip().upper() == "OPEN" else None,
                    quantity, avg_entry_price, liquidation_price, unrealized_pnl, realized_pnl,
                    leverage, margin_type,
                ),
            )
            conn.commit()
            _log_db_write_result("upsert", "positions", user_id=user_id, symbol=symbol, position_side=position_side, affected_rows=cur.rowcount)
    finally:
        conn.close()


@lru_cache(maxsize=None)
def _get_table_columns(table_name: str) -> set[str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SHOW COLUMNS FROM {table_name}")
            return {row['Field'] for row in cur.fetchall()}
    finally:
        conn.close()


def get_positions(
    user_id: Optional[int] = None,
    exchange: str = "binance",
    status: Optional[str] = None,
) -> list:
    """返回当前统一结构的持仓列表。"""
    columns = _get_table_columns("positions")
    has_status_column = "status" in columns
    sql = "SELECT * FROM positions WHERE exchange = %s"
    params: list = [exchange]
    if user_id is not None:
        sql += " AND user_id = %s"
        params.append(user_id)
    normalized_status = str(status or "").strip().upper()
    if has_status_column and normalized_status and normalized_status != "ALL":
        sql += " AND UPPER(COALESCE(status, 'OPEN')) = %s"
        params.append(normalized_status)
    if has_status_column:
        sql += " ORDER BY CASE WHEN UPPER(COALESCE(status, 'OPEN')) = 'OPEN' THEN 0 ELSE 1 END, symbol, position_side"
    else:
        sql += " ORDER BY symbol, position_side"

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def close_position(
    user_id: int,
    symbol: str,
    position_side: str = "BOTH",
    exchange: str = "binance",
) -> bool:
    """将指定持仓记录标记为已关闭。"""
    _log_db_write(
        "update",
        "positions",
        {
            "user_id": user_id,
            "exchange": exchange,
            "symbol": symbol,
            "position_side": position_side,
            "status": "CLOSE",
        },
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE positions
                   SET status = 'CLOSE',
                       open_position_slot = NULL,
                       quantity = 0,
                       liquidation_price = NULL,
                       unrealized_pnl = 0,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE user_id = %s AND exchange = %s
                     AND symbol = %s AND position_side = %s
                     AND UPPER(COALESCE(status, 'OPEN')) = 'OPEN'""",
                (user_id, exchange, symbol, position_side),
            )
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("update", "positions", user_id=user_id, symbol=symbol, position_side=position_side, status="CLOSE", affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


def delete_position(
    user_id: int,
    symbol: str,
    position_side: str = "BOTH",
    exchange: str = "binance",
) -> bool:
    """Backward-compatible alias for closing a position row instead of deleting it."""
    return close_position(user_id=user_id, symbol=symbol, position_side=position_side, exchange=exchange)


def _write_operation_log_sync(
    user_id: Optional[int],
    username: Optional[str],
    action: str,
    details: str = "",
) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO operation_logs (user_id, username, action, details) VALUES (%s, %s, %s, %s)",
                (user_id, username, action, details),
            )
            conn.commit()
            _log_db_write_result("insert", "operation_logs", log_id=cur.lastrowid, user_id=user_id, action=action)
    finally:
        conn.close()


def _operation_log_worker_loop() -> None:
    global _op_log_queue

    queue_ref = _op_log_queue
    if queue_ref is None:
        return

    while True:
        try:
            item = queue_ref.get(timeout=0.5)
        except Empty:
            if _op_log_stop_event.is_set():
                break
            continue

        try:
            if item is _OP_LOG_STOP:
                break
            user_id, username, action, details = item
            _write_operation_log_sync(user_id, username, action, details)
        except Exception:
            logger.exception("Failed to persist operation log | action=%s username=%s", item[2], item[1] if item is not _OP_LOG_STOP else None)
        finally:
            queue_ref.task_done()


def start_operation_log_worker() -> None:
    global _op_log_queue, _op_log_worker

    with _OP_LOG_WORKER_LOCK:
        if _op_log_worker is not None and _op_log_worker.is_alive():
            return

        _op_log_stop_event.clear()
        _op_log_queue = Queue(maxsize=_operation_log_queue_size())
        _op_log_worker = Thread(
            target=_operation_log_worker_loop,
            name="trade-relay-op-log-worker",
            daemon=True,
        )
        _op_log_worker.start()


def stop_operation_log_worker() -> None:
    global _op_log_queue, _op_log_worker

    with _OP_LOG_WORKER_LOCK:
        queue_ref = _op_log_queue
        worker = _op_log_worker
        if queue_ref is None or worker is None:
            return

        _op_log_stop_event.set()
        try:
            queue_ref.put(_OP_LOG_STOP, timeout=1)
        except Full:
            pass

    worker.join(timeout=5)

    with _OP_LOG_WORKER_LOCK:
        _op_log_queue = None
        _op_log_worker = None


# ──────────────────────────────────────────────
# Operation log（操作日志）
# ──────────────────────────────────────────────

def log_operation(
    user_id: Optional[int],
    username: Optional[str],
    action: str,
    details: str = "",
) -> None:
    _log_db_write("insert", "operation_logs", {"user_id": user_id, "username": username, "action": action, "details": details})
    queue_ref = _op_log_queue
    if queue_ref is None:
        _write_operation_log_sync(user_id, username, action, details)
        return

    try:
        queue_ref.put_nowait((user_id, username, action, details))
    except Full:
        logger.warning("Operation log queue is full, falling back to synchronous write")
        _write_operation_log_sync(user_id, username, action, details)


# ──────────────────────────────────────────────
# Position History（持仓历史）
# ──────────────────────────────────────────────

def add_position_history(
    user_id: int,
    username: str,
    symbol: str,
    side: str,
    entry_price: float,
    close_price: float,
    quantity: float,
    realized_pnl: float = 0.0,
    commission: float = 0.0,
    commission_asset: Optional[str] = None,
    position_id: Optional[int] = None,
    close_order_id: Optional[int] = None,
    position_mode: str = "UNKNOWN",
    created_at: Optional[datetime] = None,
) -> int:
    """插入一条持仓历史记录，返回新行 id。"""
    normalized_created_at = _coerce_utc_naive_datetime(created_at) or _utc_now_naive()
    _log_db_write(
        "insert",
        "position_history",
        {
            "user_id": user_id,
            "username": username,
            "symbol": symbol,
            "side": side.upper(),
            "position_mode": position_mode,
            "entry_price": entry_price,
            "close_price": close_price,
            "quantity": quantity,
            "realized_pnl": realized_pnl,
            "commission": commission,
            "commission_asset": commission_asset,
            "position_id": position_id,
            "close_order_id": close_order_id,
            "created_at": normalized_created_at,
        },
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Dedup guard: if close_order_id is set, check for an existing row first.
            # This prevents duplicate rows when WS and REST poll paths race each other.
            if close_order_id is not None:
                cur.execute(
                    "SELECT id FROM position_history WHERE close_order_id = %s AND username = %s LIMIT 1",
                    (close_order_id, username),
                )
                existing = cur.fetchone()
                if existing:
                    _log_db_write_result(
                        "insert_skipped_duplicate",
                        "position_history",
                        history_id=existing["id"],
                        user_id=user_id,
                        symbol=symbol,
                        side=side.upper(),
                    )
                    return existing["id"]

            cur.execute(
                """INSERT INTO position_history
                         (user_id, username, symbol, side, position_mode, entry_price, close_price, quantity, realized_pnl, commission, commission_asset, position_id, close_order_id, created_at, updated_at)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    user_id,
                    username,
                    symbol,
                    side.upper(),
                    position_mode,
                    entry_price,
                    close_price,
                    quantity,
                    realized_pnl,
                    commission,
                    commission_asset,
                    position_id,
                    close_order_id,
                    normalized_created_at,
                    normalized_created_at,
                ),
            )
            _refresh_daily_profile_for_user_date(cur, user_id, username, normalized_created_at.date())
            conn.commit()
            _log_db_write_result("insert", "position_history", history_id=cur.lastrowid, user_id=user_id, symbol=symbol, side=side.upper())
            return cur.lastrowid
    finally:
        conn.close()


def update_position_history_values(
    history_id: int,
    realized_pnl: float,
    commission: float,
    commission_asset: Optional[str] = None,
) -> bool:
    _log_db_write(
        "update",
        "position_history",
        {
            "history_id": history_id,
            "realized_pnl": realized_pnl,
            "commission": commission,
            "commission_asset": commission_asset,
        },
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if commission_asset is None:
                cur.execute(
                    "UPDATE position_history SET realized_pnl = %s, commission = %s WHERE id = %s",
                    (realized_pnl, commission, history_id),
                )
            else:
                cur.execute(
                    "UPDATE position_history SET realized_pnl = %s, commission = %s, commission_asset = %s WHERE id = %s",
                    (realized_pnl, commission, commission_asset, history_id),
                )
            if cur.rowcount > 0:
                _refresh_daily_profile_for_history_row(cur, history_id)
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("update", "position_history", history_id=history_id, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


def get_order_by_exchange_id(username: str, exchange_order_id: str) -> Optional[dict]:
    """按 username + exchange_order_id 查询单条订单，失败返回 None。
    ORDER BY id ASC 确保有重复行时始终返回最早创建的那条（原始行）。
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM orders WHERE username = %s AND exchange_order_id = %s ORDER BY id ASC LIMIT 1",
                (username, exchange_order_id),
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_order_by_client_order_id(username: str, client_order_id: str) -> Optional[dict]:
    """按 client_order_id 或 algo_client_id 查询订单（覆盖条件单触发场景）。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM orders
                   WHERE username = %s
                     AND (client_order_id = %s OR algo_client_id = %s)
                   ORDER BY id ASC LIMIT 1""",
                (username, client_order_id, client_order_id),
            )
            return cur.fetchone()
    finally:
        conn.close()


def adopt_external_order(username: str, exchange_order_id: str, ws_event: dict) -> Optional[int]:
    """将外部工具（TradingView / Binance 客户端等）产生的订单写入本地 orders 表。

    从 Binance User Data Stream ORDER_TRADE_UPDATE 的 ``o`` 字段提取字段，
    使用 INSERT ... ON DUPLICATE KEY UPDATE 保证幂等（WS 重连时不重复写入）。
    返回写入/已存在记录的主键 id，失败返回 None。
    """
    user = get_user_by_username(username)
    if not user:
        logger.warning("adopt_external_order: user not found for username=%s", username)
        return None
    user_id = int(user["id"])

    def _f(key: str) -> Optional[float]:
        v = ws_event.get(key)
        try:
            return float(v) if v not in (None, "", "0", 0) else None
        except (TypeError, ValueError):
            return None

    symbol        = str(ws_event.get("s") or "")
    side          = str(ws_event.get("S") or "").upper()
    order_type    = str(ws_event.get("o") or "MARKET").upper()
    quantity      = float(ws_event.get("q") or 0)
    price         = _f("p")
    stop_price    = _f("sp")
    status        = str(ws_event.get("X") or "NEW").upper()
    client_order_id = str(ws_event.get("c") or "").strip() or None
    filled_qty    = float(ws_event.get("z") or 0)
    avg_price     = _f("ap")
    reduce_only   = bool(ws_event.get("R") or False)
    raw_ps        = str(ws_event.get("ps") or "BOTH").upper()
    position_mode = "DUAL" if raw_ps in ("LONG", "SHORT") else "SINGLE"

    # Infer trade_direction:
    # 1. reduce_only=True  → always CLOSE
    # 2. Dual (LONG/SHORT) → can match side vs position_side:
    #    LONG position closed by SELL, SHORT position closed by BUY
    # 3. Single (BOTH) without reduce_only: query current DB position to check
    #    if the order side opposes the open position direction.
    if reduce_only:
        trade_direction: Optional[str] = "CLOSE"
    elif raw_ps == "LONG":
        trade_direction = "CLOSE" if side == "SELL" else "OPEN"
    elif raw_ps == "SHORT":
        trade_direction = "CLOSE" if side == "BUY" else "OPEN"
    else:
        # Single / BOTH mode: inspect the current position in DB
        trade_direction = "OPEN"  # default
        user_row = get_user_by_username(username)
        if user_row:
            pos = get_position(int(user_row["id"]), symbol, "BOTH")
            if pos:
                pos_qty = float(pos.get("quantity") or 0)
                # Long position (qty > 0) is closed by SELL; short (qty < 0) by BUY
                if pos_qty > 0 and side == "SELL":
                    trade_direction = "CLOSE"
                elif pos_qty < 0 and side == "BUY":
                    trade_direction = "CLOSE"
    order_category = _normalize_order_category(order_type, "")
    # filled_at from trade time field 'T' (millisecond epoch)
    filled_at = None
    trade_time = ws_event.get("T")
    if trade_time and status in ("FILLED", "PARTIALLY_FILLED"):
        try:
            import datetime as _dt
            filled_at = _dt.datetime.utcfromtimestamp(int(trade_time) / 1000)
        except Exception:
            pass

    if not symbol or not side or not order_type or quantity <= 0:
        logger.warning(
            "adopt_external_order: incomplete WS event for exchange_order_id=%s, skipping",
            exchange_order_id,
        )
        return None

    # Guard: check if this exchange_order_id is already in DB (race between concurrent WS events
    # or between WS and order_manager write).  orders.exchange_order_id has no UNIQUE constraint
    # so ON DUPLICATE KEY UPDATE would not fire — do the dedup at application level instead.
    existing = get_order_by_exchange_id(username, exchange_order_id)
    if existing:
        return int(existing["id"])

    # Also check by client_order_id to avoid duplicating triggered conditional orders.
    client_oid = str(ws_event.get("c") or "").strip()
    if client_oid:
        existing = get_order_by_client_order_id(username, client_oid)
        if existing:
            # Back-fill exchange_order_id if missing
            if not str(existing.get("exchange_order_id") or "").strip():
                update_order_metadata(int(existing["id"]), exchange_order_id=exchange_order_id)
            return int(existing["id"])

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO orders
                   (user_id, username, exchange, source, symbol, side, order_type,
                    quantity, price, stop_price, status,
                    exchange_order_id, client_order_id,
                    filled_qty, avg_price, filled_at,
                    trade_direction, position_mode, reduce_only,
                    order_category)
                   VALUES (%s, %s, 'binance', 'external', %s, %s, %s,
                           %s, %s, %s, %s,
                           %s, %s,
                           %s, %s, %s,
                           %s, %s, %s,
                           %s)
                """,
                (
                    user_id, username, symbol, side, order_type,
                    quantity, price, stop_price, status,
                    exchange_order_id, client_order_id,
                    filled_qty, avg_price, filled_at,
                    trade_direction, position_mode, int(reduce_only),
                    order_category,
                ),
            )
            conn.commit()
            new_id = cur.lastrowid
            if new_id:
                logger.info(
                    "adopt_external_order: adopted order exchange_order_id=%s for user=%s symbol=%s side=%s status=%s",
                    exchange_order_id, username, symbol, side, status,
                )
                return new_id
            row = get_order_by_exchange_id(username, exchange_order_id)
            return int(row["id"]) if row else None
    except Exception:
        logger.exception(
            "adopt_external_order: failed to adopt exchange_order_id=%s for user=%s",
            exchange_order_id, username,
        )
        return None
    finally:
        conn.close()


def get_order_by_algo_id(username: str, algo_id: str) -> Optional[dict]:
    """按 username + algo_id 查询单条条件订单，失败返回 None。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM orders WHERE username = %s AND algo_id = %s LIMIT 1",
                (username, algo_id),
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_order_by_id(order_id: int) -> Optional[dict]:
    """按主键查询单条订单，失败返回 None。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM orders WHERE id = %s LIMIT 1",
                (order_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_position(
    user_id: int,
    symbol: str,
    position_side: str,
    exchange: str = "binance",
    status: Optional[str] = "OPEN",
) -> Optional[dict]:
    """按 user_id + symbol + position_side 查询单条持仓，默认只返回当前 OPEN 持仓。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            params: list = [user_id, exchange, symbol, position_side]
            sql = """SELECT * FROM positions
                   WHERE user_id = %s AND exchange = %s AND symbol = %s AND position_side = %s"""
            normalized_status = str(status or "").strip().upper()
            if normalized_status and normalized_status != "ALL":
                sql += " AND UPPER(COALESCE(status, 'OPEN')) = %s"
                params.append(normalized_status)
            sql += " ORDER BY id DESC LIMIT 1"
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def get_position_history(user_id: Optional[int] = None, limit: int = 200) -> list:
    """返回持仓历史记录。user_id=None 时返回所有用户。"""
    params: list = []
    sql = """SELECT id, user_id, username, symbol, side, position_mode, entry_price, close_price,
                    quantity, realized_pnl, commission, commission_asset, position_id, close_order_id, created_at, updated_at
             FROM position_history"""
    if user_id is not None:
        sql += " WHERE user_id = %s"
        params.append(user_id)
    sql += " ORDER BY COALESCE(updated_at, created_at) DESC, id DESC LIMIT %s"
    params.append(limit)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()
