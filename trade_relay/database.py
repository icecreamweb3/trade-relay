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
from queue import Empty, Full, Queue
from contextlib import contextmanager
from datetime import datetime
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

_PYMYSQL_SOCKET_PATCH_LOCK = RLock()
_MYSQL_PROXY_SCHEME_NAMES = frozenset({"socks5", "socks5h", "socks4", "socks4a", "http", "https"})

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
        return pymysql.connect(**mysql_cfg)


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


def _create_positions_table(cur: pymysql.cursors.Cursor) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id              BIGINT          NOT NULL AUTO_INCREMENT,
            user_id         BIGINT          NOT NULL,
            username        VARCHAR(64)     NOT NULL,
            exchange        VARCHAR(32)     NOT NULL DEFAULT 'binance',
            symbol          VARCHAR(32)     NOT NULL,
            position_side   ENUM('LONG','SHORT','BOTH') NOT NULL DEFAULT 'BOTH',
            quantity        DECIMAL(20,8)   NOT NULL DEFAULT 0 COMMENT '持仓数量（负数为空头）',
            avg_entry_price DECIMAL(20,8)   DEFAULT NULL COMMENT '开仓均价',
            unrealized_pnl  DECIMAL(20,8)   DEFAULT NULL COMMENT '未实现盈亏',
            realized_pnl    DECIMAL(20,8)   NOT NULL DEFAULT 0 COMMENT '已实现盈亏',
            leverage        SMALLINT        NOT NULL DEFAULT 1 COMMENT '杠杆倍数',
            margin_type     ENUM('ISOLATED','CROSS') NOT NULL DEFAULT 'CROSS',
            updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uk_position (user_id, exchange, symbol, position_side),
            CONSTRAINT fk_positions_user FOREIGN KEY (user_id) REFERENCES users (id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)


def _migrate_positions_table(cur: pymysql.cursors.Cursor) -> None:
    if not _table_exists(cur, "positions"):
        _create_positions_table(cur)
        return

    cur.execute("SHOW COLUMNS FROM positions")
    existing_columns = {row["Field"] for row in cur.fetchall()}
    required_columns = {
        "id", "user_id", "username", "exchange", "symbol", "position_side",
        "quantity", "avg_entry_price", "unrealized_pnl", "realized_pnl",
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

        updated_at = row.get("updated_at") or row.get("created_at") or row.get("opened_at") or datetime.now()

        cur.execute(
            """INSERT INTO positions
               (id, user_id, username, exchange, symbol, position_side,
                quantity, avg_entry_price, unrealized_pnl, realized_pnl,
                leverage, margin_type, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                legacy_position_id,
                owner_info["user_id"],
                owner_info["username"],
                "binance",
                row.get("symbol"),
                position_side,
                row.get("quantity") or 0,
                row.get("avg_entry_price") if "avg_entry_price" in row else row.get("entry_price"),
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
                    exchange_order_id VARCHAR(64)     DEFAULT NULL COMMENT '交易所订单ID',
                    client_order_id   VARCHAR(64)     DEFAULT NULL COMMENT '客户端订单ID',
                    filled_qty        DECIMAL(20,8)   NOT NULL DEFAULT 0 COMMENT '已成交数量',
                    avg_price         DECIMAL(20,8)   DEFAULT NULL COMMENT '成交均价',
                    realized_pnl      DECIMAL(30,10)  DEFAULT NULL COMMENT '已实现盈亏',
                    commission        DECIMAL(20,8)   DEFAULT NULL COMMENT '手续费',
                    commission_asset  VARCHAR(16)     DEFAULT NULL COMMENT '手续费币种',
                    trade_direction   ENUM('OPEN','CLOSE') DEFAULT NULL COMMENT '开仓/平仓',
                    reduce_only       TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '只减仓',
                    post_only         TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '只做Maker',
                    position_id       BIGINT          DEFAULT NULL COMMENT '关联持仓ID',
                    order_category    ENUM('Basic','Conditional') NOT NULL DEFAULT 'Basic' COMMENT '订单分类',
                    error_message     TEXT            COMMENT '错误信息',
                    created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                                      ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    KEY idx_user_status (user_id, status),
                    KEY idx_status_created (status, created_at),
                    KEY idx_created_at (created_at DESC),
                    CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users (id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # Migration: add columns that may be missing in older deployments
            for _col, _ddl in [
                ("trade_direction", "ALTER TABLE orders ADD COLUMN trade_direction ENUM('OPEN','CLOSE') DEFAULT NULL COMMENT '开仓/平仓' AFTER commission_asset"),
                ("position_id",     "ALTER TABLE orders ADD COLUMN position_id BIGINT DEFAULT NULL COMMENT '关联持仓ID' AFTER trade_direction"),
                ("reduce_only",     "ALTER TABLE orders ADD COLUMN reduce_only TINYINT(1) NOT NULL DEFAULT 0 COMMENT '只减仓' AFTER trade_direction"),
                ("post_only",       "ALTER TABLE orders ADD COLUMN post_only TINYINT(1) NOT NULL DEFAULT 0 COMMENT '只做Maker' AFTER reduce_only"),
                ("order_category",  "ALTER TABLE orders ADD COLUMN order_category ENUM('Basic','Conditional') NOT NULL DEFAULT 'Basic' COMMENT '订单分类' AFTER position_id"),
                ("tp_price",        "ALTER TABLE orders ADD COLUMN tp_price DECIMAL(20,8) DEFAULT NULL COMMENT '计划止盈价' AFTER stop_price"),
                ("sl_price",        "ALTER TABLE orders ADD COLUMN sl_price DECIMAL(20,8) DEFAULT NULL COMMENT '计划止损价' AFTER tp_price"),
                ("realized_pnl",    "ALTER TABLE orders ADD COLUMN realized_pnl DECIMAL(30,10) DEFAULT NULL COMMENT '已实现盈亏' AFTER avg_price"),
            ]:
                try:
                    cur.execute(_ddl)
                except Exception:
                    pass  # column already exists
            try:
                cur.execute("ALTER TABLE orders ADD INDEX idx_status_created (status, created_at)")
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
                    entry_price   DECIMAL(30,10)  NOT NULL COMMENT '开仓均价',
                    close_price   DECIMAL(30,10)  NOT NULL COMMENT '平仓价格',
                    quantity      DECIMAL(30,10)  NOT NULL COMMENT '成交数量',
                    realized_pnl  DECIMAL(30,10)  NOT NULL DEFAULT 0 COMMENT '已实现盈亏',
                    commission    DECIMAL(30,10)  NOT NULL DEFAULT 0 COMMENT '手续费',
                    commission_asset VARCHAR(16)  DEFAULT NULL COMMENT '手续费币种',
                    position_id   BIGINT          DEFAULT NULL COMMENT '关联持仓ID（对应 positions.id）',
                    created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
                    update_at     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
                    PRIMARY KEY (id),
                    KEY idx_user_id (user_id),
                    KEY idx_username (username),
                    KEY idx_symbol (symbol),
                    KEY idx_created_at (created_at DESC)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='持仓历史'
            """)
            # Add columns that may be missing if table was created before this schema version
            for _col, _ddl in [
                ("user_id",     "ALTER TABLE position_history ADD COLUMN user_id INT NOT NULL DEFAULT 0 COMMENT '用户ID' AFTER id"),
                ("username",    "ALTER TABLE position_history ADD COLUMN username VARCHAR(64) NOT NULL DEFAULT '' COMMENT '用户名' AFTER user_id"),
                ("side",        "ALTER TABLE position_history ADD COLUMN side VARCHAR(8) NOT NULL DEFAULT 'LONG' COMMENT '方向 LONG/SHORT' AFTER symbol"),
                ("commission_asset", "ALTER TABLE position_history ADD COLUMN commission_asset VARCHAR(16) DEFAULT NULL COMMENT '手续费币种' AFTER commission"),
                ("position_id", "ALTER TABLE position_history ADD COLUMN position_id BIGINT DEFAULT NULL COMMENT '关联持仓ID（对应 positions.id）' AFTER commission"),
                ("update_at",   "ALTER TABLE position_history ADD COLUMN update_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间' AFTER created_at"),
            ]:
                try:
                    cur.execute(_ddl)
                except Exception:
                    pass  # column already exists

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

        conn.commit()
    finally:
        conn.close()


# ──────────────────────────────────────────────
# account_summary CRUD
# ──────────────────────────────────────────────

_ACCOUNT_SUMMARY_COLUMNS = (
    "user_id", "symbol", "base_asset", "quote_asset",
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
    error_message: Optional[str] = None,
    exchange: str = "binance",
    stop_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    sl_price: Optional[float] = None,
    client_order_id: Optional[str] = None,
    trade_direction: Optional[str] = None,     # OPEN | CLOSE
    position_id: Optional[int] = None,         # 关联持仓ID
    realized_pnl: Optional[float] = None,
    reduce_only: bool = False,
    post_only: bool = False,
    order_category: str = 'Basic',             # Basic | Conditional
) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            normalized_order_category = _normalize_order_category(order_type, order_category)
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
                    "exchange_order_id": binance_order_id,
                    "client_order_id": client_order_id,
                    "realized_pnl": realized_pnl,
                    "trade_direction": trade_direction,
                    "reduce_only": int(reduce_only),
                    "post_only": int(post_only),
                    "position_id": position_id,
                    "order_category": normalized_order_category,
                    "error_message": error_message,
                },
            )
            cur.execute(
                """INSERT INTO orders
                   (user_id, username, exchange, symbol, side, order_type,
                    quantity, price, stop_price, tp_price, sl_price, status,
                    exchange_order_id, client_order_id,
                    trade_direction, reduce_only, post_only, position_id, realized_pnl, order_category, error_message)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    user_id, username, exchange, symbol, side, order_type,
                    quantity, price, stop_price, tp_price, sl_price, status,
                    binance_order_id, client_order_id,
                    trade_direction, int(reduce_only), int(post_only), position_id, realized_pnl, normalized_order_category, error_message,
                ),
            )
            conn.commit()
            _log_db_write_result(
                "insert",
                "orders",
                db_id=cur.lastrowid,
                user_id=user_id,
                exchange_order_id=binance_order_id,
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
    realized_pnl: Optional[float] = None,
    commission: Optional[float] = None,
    commission_asset: Optional[str] = None,
    error_message: Optional[str] = None,
) -> bool:
    """更新订单状态及成交信息。"""
    fields = ["status = %s"]
    params: list = [status]
    if filled_qty is not None:
        fields.append("filled_qty = %s"); params.append(filled_qty)
    if avg_price is not None:
        fields.append("avg_price = %s"); params.append(avg_price)
    if realized_pnl is not None:
        fields.append("realized_pnl = %s"); params.append(realized_pnl)
    if commission is not None:
        fields.append("commission = %s"); params.append(commission)
    if commission_asset is not None:
        fields.append("commission_asset = %s"); params.append(commission_asset)
    if error_message is not None:
        fields.append("error_message = %s"); params.append(error_message)
    params.append(order_id)

    _log_db_write(
        "update",
        "orders",
        {
            "order_id": order_id,
            "status": status,
            "filled_qty": filled_qty,
            "avg_price": avg_price,
            "realized_pnl": realized_pnl,
            "commission": commission,
            "commission_asset": commission_asset,
            "error_message": error_message,
        },
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


def update_order_metadata(order_id: int, **fields_to_update) -> bool:
    """Update selected order fields by primary key."""
    allowed_fields = {
        "price",
        "stop_price",
        "exchange_order_id",
        "client_order_id",
        "trade_direction",
        "error_message",
    }
    fields = {key: value for key, value in fields_to_update.items() if key in allowed_fields and value is not None}
    if not fields:
        return False

    assignments = [f"{key} = %s" for key in fields]
    params = list(fields.values()) + [order_id]

    _log_db_write("update", "orders", {"order_id": order_id, **fields})

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
    realized_pnl: Optional[float] = None,
    commission: Optional[float] = None,
    commission_asset: Optional[str] = None,
    error_message: Optional[str] = None,
) -> bool:
    """Update an order row by username + exchange_order_id."""
    fields = ["status = %s"]
    params: list = [status]
    if filled_qty is not None:
        fields.append("filled_qty = %s"); params.append(filled_qty)
    if avg_price is not None:
        fields.append("avg_price = %s"); params.append(avg_price)
    if realized_pnl is not None:
        fields.append("realized_pnl = %s"); params.append(realized_pnl)
    if commission is not None:
        fields.append("commission = %s"); params.append(commission)
    if commission_asset is not None:
        fields.append("commission_asset = %s"); params.append(commission_asset)
    if error_message is not None:
        fields.append("error_message = %s"); params.append(error_message)
    params.extend([username, exchange_order_id])

    _log_db_write(
        "update",
        "orders",
        {
            "username": username,
            "exchange_order_id": exchange_order_id,
            "status": status,
            "filled_qty": filled_qty,
            "avg_price": avg_price,
            "realized_pnl": realized_pnl,
            "commission": commission,
            "commission_asset": commission_asset,
            "error_message": error_message,
        },
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
    """Return all active orders for backend startup/status sync across all categories."""
    placeholders = ", ".join(["%s"] * len(ACTIVE_STATUSES))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT * FROM orders
                      WHERE status IN ({placeholders})
                      ORDER BY created_at DESC""",
                list(ACTIVE_STATUSES),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_active_orders_for_user(username: str) -> list:
    """返回指定用户的 Basic 当前委托（未完结订单），按 username 过滤。"""
    placeholders = ", ".join(["%s"] * len(ACTIVE_STATUSES))
    params: list = list(ACTIVE_STATUSES)
    params.append(username)
    sql = f"""SELECT * FROM orders
              WHERE status IN ({placeholders})
                AND order_category = 'Basic'
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


def get_order_history(user_id: Optional[int] = None, limit: int = 200) -> list:
    """返回历史订单（已完结：FILLED / CANCELED / EXPIRED / REJECTED / FAILED / ERROR）。"""
    placeholders = ", ".join(["%s"] * len(ACTIVE_STATUSES))
    params: list = list(ACTIVE_STATUSES)
    sql = f"""SELECT * FROM orders
              WHERE status NOT IN ({placeholders})"""
    if user_id is not None:
        sql += " AND user_id = %s"
        params.append(user_id)
    sql += " ORDER BY created_at DESC LIMIT %s"
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
        sql += " AND (CAST(id AS CHAR) LIKE %s OR exchange_order_id LIKE %s)"
        like_value = f"%{order_id}%"
        params.extend([like_value, like_value])

    if start_time:
        sql += " AND created_at >= %s"
        params.append(start_time)

    if end_time:
        sql += " AND created_at <= %s"
        params.append(end_time)

    if status:
        sql += " AND status = %s"
        params.append(status)

    sql += " ORDER BY created_at DESC LIMIT %s"
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


def get_daily_pnl(user_id: int) -> list:
    """Return daily realized P&L for a user from position history.

    Each row: { date: str, pnl: float, commission: float }
    P&L and commission are aggregated from closed-position records.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DATE(created_at) AS date,
                       SUM(COALESCE(realized_pnl, 0)) AS pnl,
                       SUM(COALESCE(commission, 0))   AS commission
                FROM position_history
                WHERE user_id = %s
                GROUP BY DATE(created_at)
                ORDER BY date ASC
                """,
                (user_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_total_commission_by_asset(user_id: int) -> list:
    """Return total commission grouped by commission_asset for a user.

    Each row: { asset: str, total: float }
    Data is aggregated from filled orders to preserve existing totals for historical rows.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
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


# ──────────────────────────────────────────────
# Position CRUD（头寸信息）
# ──────────────────────────────────────────────

def upsert_position(
    user_id: int,
    username: str,
    symbol: str,
    quantity: float,
    avg_entry_price: Optional[float] = None,
    unrealized_pnl: Optional[float] = None,
    realized_pnl: float = 0.0,
    leverage: int = 1,
    margin_type: str = "CROSS",
    position_side: str = "BOTH",
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
            "quantity": quantity,
            "avg_entry_price": avg_entry_price,
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
                   (user_id, username, exchange, symbol, position_side,
                    quantity, avg_entry_price, unrealized_pnl, realized_pnl,
                    leverage, margin_type)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                       quantity        = VALUES(quantity),
                       avg_entry_price = VALUES(avg_entry_price),
                       unrealized_pnl  = VALUES(unrealized_pnl),
                       realized_pnl    = VALUES(realized_pnl),
                       leverage        = VALUES(leverage),
                       margin_type     = VALUES(margin_type),
                       updated_at      = CURRENT_TIMESTAMP""",
                (
                    user_id, username, exchange, symbol, position_side,
                    quantity, avg_entry_price, unrealized_pnl, realized_pnl,
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


def get_positions(user_id: Optional[int] = None, exchange: str = "binance") -> list:
    """返回当前统一结构的持仓列表。"""
    sql = "SELECT * FROM positions WHERE exchange = %s"
    params: list = [exchange]
    if user_id is not None:
        sql += " AND user_id = %s"
        params.append(user_id)
    sql += " ORDER BY symbol, position_side"

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def delete_position(
    user_id: int,
    symbol: str,
    position_side: str = "BOTH",
    exchange: str = "binance",
) -> bool:
    """删除（清除）指定持仓记录。"""
    _log_db_write(
        "delete",
        "positions",
        {
            "user_id": user_id,
            "exchange": exchange,
            "symbol": symbol,
            "position_side": position_side,
        },
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """DELETE FROM positions
                   WHERE user_id = %s AND exchange = %s
                     AND symbol = %s AND position_side = %s""",
                (user_id, exchange, symbol, position_side),
            )
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("delete", "positions", user_id=user_id, symbol=symbol, position_side=position_side, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


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
) -> int:
    """插入一条持仓历史记录，返回新行 id。"""
    _log_db_write(
        "insert",
        "position_history",
        {
            "user_id": user_id,
            "username": username,
            "symbol": symbol,
            "side": side.upper(),
            "entry_price": entry_price,
            "close_price": close_price,
            "quantity": quantity,
            "realized_pnl": realized_pnl,
            "commission": commission,
            "commission_asset": commission_asset,
            "position_id": position_id,
        },
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO position_history
                   (user_id, username, symbol, side, entry_price, close_price, quantity, realized_pnl, commission, commission_asset, position_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (user_id, username, symbol, side.upper(), entry_price, close_price, quantity, realized_pnl, commission, commission_asset, position_id),
            )
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
            conn.commit()
            success = cur.rowcount > 0
            _log_db_write_result("update", "position_history", history_id=history_id, affected_rows=cur.rowcount, success=success)
            return success
    finally:
        conn.close()


def get_order_by_exchange_id(username: str, exchange_order_id: str) -> Optional[dict]:
    """按 username + exchange_order_id 查询单条订单，失败返回 None。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM orders WHERE username = %s AND exchange_order_id = %s LIMIT 1",
                (username, exchange_order_id),
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


def get_position(user_id: int, symbol: str, position_side: str, exchange: str = "binance") -> Optional[dict]:
    """按 user_id + symbol + position_side 查询单条持仓，失败返回 None。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM positions
                   WHERE user_id = %s AND exchange = %s AND symbol = %s AND position_side = %s
                   LIMIT 1""",
                (user_id, exchange, symbol, position_side),
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_position_history(user_id: Optional[int] = None, limit: int = 200) -> list:
    """返回持仓历史记录。user_id=None 时返回所有用户。"""
    params: list = []
    sql = """SELECT id, user_id, username, symbol, side, entry_price, close_price,
                    quantity, realized_pnl, commission, commission_asset, position_id, created_at, update_at
             FROM position_history"""
    if user_id is not None:
        sql += " WHERE user_id = %s"
        params.append(user_id)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()
