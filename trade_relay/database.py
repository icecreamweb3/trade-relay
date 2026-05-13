"""
Database management - MySQL via PyMySQL.

Connection parameters are read from environment variables (set in .env):
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
import os
from typing import Optional

import pymysql
import pymysql.cursors
import pymysql.err
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


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
        "host":        os.environ.get("TRADE_RELAY_MYSQL_HOST", "127.0.0.1"),
        "port":        int(os.environ.get("TRADE_RELAY_MYSQL_PORT", "3306")),
        "user":        os.environ.get("TRADE_RELAY_MYSQL_USER", "trade_relay"),
        "password":    os.environ.get("TRADE_RELAY_MYSQL_PASSWORD", ""),
        "database":    os.environ.get("TRADE_RELAY_MYSQL_DATABASE", "trade_relay"),
        "charset":     "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit":  False,
        "connect_timeout": 10,
    }


def get_connection() -> pymysql.connections.Connection:
    """Get a new MySQL connection."""
    return pymysql.connect(**_mysql_cfg())


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
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_username (username)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # Migration: add columns for existing deployments
            for col, definition in [
                ("binance_api_key",    "TEXT DEFAULT NULL COMMENT 'Binance API Key (encrypted)'"),
                ("binance_api_secret", "TEXT DEFAULT NULL COMMENT 'Binance API Secret (encrypted)'"),
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
                    status            VARCHAR(32)     NOT NULL DEFAULT 'NEW',
                    exchange_order_id VARCHAR(64)     DEFAULT NULL COMMENT '交易所订单ID',
                    client_order_id   VARCHAR(64)     DEFAULT NULL COMMENT '客户端订单ID',
                    filled_qty        DECIMAL(20,8)   NOT NULL DEFAULT 0 COMMENT '已成交数量',
                    avg_price         DECIMAL(20,8)   DEFAULT NULL COMMENT '成交均价',
                    commission        DECIMAL(20,8)   DEFAULT NULL COMMENT '手续费',
                    commission_asset  VARCHAR(16)     DEFAULT NULL COMMENT '手续费币种',
                    trade_direction   ENUM('OPEN','CLOSE') DEFAULT NULL COMMENT '开仓/平仓',
                    position_id       BIGINT          DEFAULT NULL COMMENT '关联持仓ID',
                    error_message     TEXT            COMMENT '错误信息',
                    created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP
                                      ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    KEY idx_user_status (user_id, status),
                    KEY idx_created_at (created_at DESC),
                    CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users (id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)

            # Migration: add columns that may be missing in older deployments
            for _col, _ddl in [
                ("trade_direction", "ALTER TABLE orders ADD COLUMN trade_direction ENUM('OPEN','CLOSE') DEFAULT NULL COMMENT '开仓/平仓' AFTER commission_asset"),
                ("position_id",     "ALTER TABLE orders ADD COLUMN position_id BIGINT DEFAULT NULL COMMENT '关联持仓ID' AFTER trade_direction"),
            ]:
                try:
                    cur.execute(_ddl)
                except Exception:
                    pass  # column already exists

            # ── positions（头寸信息）──────────────────────────────────────
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
                    position_id   BIGINT          DEFAULT NULL COMMENT '关联持仓ID（对应 positions.id）',
                    created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
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
                ("position_id", "ALTER TABLE position_history ADD COLUMN position_id BIGINT DEFAULT NULL COMMENT '关联持仓ID（对应 positions.id）' AFTER commission"),
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

        conn.commit()
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
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, role, binance_api_key, binance_api_secret)"
                " VALUES (%s, %s, %s, %s, %s)",
                (username, password_hash, role, enc_key, enc_secret),
            )
            conn.commit()
            return cur.lastrowid
    except pymysql.err.IntegrityError:
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
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET binance_api_key = %s, binance_api_secret = %s WHERE id = %s",
                (enc_key, enc_secret, user_id),
            )
            conn.commit()
            return cur.rowcount > 0
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
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (password_hash, user_id),
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def update_username(user_id: int, username: str) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET username = %s WHERE id = %s",
                (username, user_id),
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def update_user_role(user_id: int, role: str) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET role = %s WHERE id = %s",
                (role, user_id),
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def deactivate_user(user_id: int) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_active = 0 WHERE id = %s", (user_id,)
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def activate_user(user_id: int) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_active = 1 WHERE id = %s", (user_id,)
            )
            conn.commit()
            return cur.rowcount > 0
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
    client_order_id: Optional[str] = None,
    trade_direction: Optional[str] = None,     # OPEN | CLOSE
    position_id: Optional[int] = None,         # 关联持仓ID
) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO orders
                   (user_id, username, exchange, symbol, side, order_type,
                    quantity, price, stop_price, status,
                    exchange_order_id, client_order_id,
                    trade_direction, position_id, error_message)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    user_id, username, exchange, symbol, side, order_type,
                    quantity, price, stop_price, status,
                    binance_order_id, client_order_id,
                    trade_direction, position_id, error_message,
                ),
            )
            conn.commit()
            return cur.lastrowid
    finally:
        conn.close()


def update_order_status(
    order_id: int,
    status: str,
    filled_qty: Optional[float] = None,
    avg_price: Optional[float] = None,
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
    if commission is not None:
        fields.append("commission = %s"); params.append(commission)
    if commission_asset is not None:
        fields.append("commission_asset = %s"); params.append(commission_asset)
    if error_message is not None:
        fields.append("error_message = %s"); params.append(error_message)
    params.append(order_id)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE orders SET {', '.join(fields)} WHERE id = %s", params
            )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


def update_order_status_by_exchange_id(
    username: str,
    exchange_order_id: str,
    status: str,
    filled_qty: Optional[float] = None,
    avg_price: Optional[float] = None,
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
    if commission is not None:
        fields.append("commission = %s"); params.append(commission)
    if commission_asset is not None:
        fields.append("commission_asset = %s"); params.append(commission_asset)
    if error_message is not None:
        fields.append("error_message = %s"); params.append(error_message)
    params.extend([username, exchange_order_id])

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
            return cur.rowcount > 0
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
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ticker_messages (contents_zh, contents_en) VALUES (%s, %s)",
                (contents_zh, contents_en),
            )
            conn.commit()
            return cur.lastrowid
    finally:
        conn.close()


def delete_ticker_message(msg_id: int) -> bool:
    """Delete a ticker_messages row by id. Returns True if a row was deleted."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ticker_messages WHERE id = %s", (msg_id,))
            conn.commit()
            return cur.rowcount > 0
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
    """返回当前委托（未完结订单）。"""
    placeholders = ", ".join(["%s"] * len(ACTIVE_STATUSES))
    params: list = list(ACTIVE_STATUSES)
    sql = f"""SELECT * FROM orders
              WHERE status IN ({placeholders})"""
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


def get_active_orders_for_user(username: str) -> list:
    """返回指定用户的当前委托（未完结订单），按 username 过滤。"""
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
    """Return usernames that currently have active orders."""
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
    每行包含: username, symbol, side, filled_qty, avg_price, created_at
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT username, symbol, side, filled_qty, price, avg_price, created_at
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
    """Return daily realized P&L for a user from filled orders.

    Each row: { date: str, pnl: float, commission: float }
    P&L is approximated as sum of (avg_price * filled_qty * sign) per day,
    where SELL = +revenue and BUY = -cost (net cash flow proxy).
    commission is subtracted.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DATE(created_at) AS date,
                       SUM(CASE WHEN side='SELL' THEN  avg_price * filled_qty
                                WHEN side='BUY'  THEN -avg_price * filled_qty
                                ELSE 0 END)           AS pnl,
                       SUM(COALESCE(commission, 0))    AS commission
                FROM orders
                WHERE user_id = %s
                  AND status = 'FILLED'
                  AND avg_price IS NOT NULL
                  AND filled_qty > 0
                GROUP BY DATE(created_at)
                ORDER BY date ASC
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
    """插入或更新头寸记录，兼容新版（含 user_id）和旧版（side/entry_price）表结构。"""
    columns = _get_table_columns("positions")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if "user_id" in columns:
                # New schema
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
            else:
                # Legacy schema: id, symbol, side, quantity, entry_price,
                #                liquidation_price, unrealized_pnl, leverage, margin_type
                side = position_side if position_side in ("LONG", "SHORT") else "LONG"
                cur.execute(
                    "SELECT id FROM positions WHERE symbol = %s AND side = %s",
                    (symbol, side),
                )
                row = cur.fetchone()
                if row:
                    cur.execute(
                        """UPDATE positions
                           SET quantity = %s, entry_price = %s,
                               unrealized_pnl = %s, leverage = %s, margin_type = %s
                           WHERE id = %s""",
                        (quantity, avg_entry_price, unrealized_pnl, leverage,
                         margin_type.upper(), row["id"]),
                    )
                else:
                    cur.execute(
                        """INSERT INTO positions
                           (symbol, side, quantity, entry_price,
                            unrealized_pnl, leverage, margin_type, opened_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())""",
                        (symbol, side, quantity, avg_entry_price,
                         unrealized_pnl, leverage, margin_type.upper()),
                    )
            conn.commit()
    finally:
        conn.close()


def _get_table_columns(table_name: str) -> set[str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SHOW COLUMNS FROM {table_name}")
            return {row['Field'] for row in cur.fetchall()}
    finally:
        conn.close()


def get_positions(user_id: Optional[int] = None, exchange: str = "binance") -> list:
    """返回头寸列表，兼容旧版与新版 positions 表结构。"""
    columns = _get_table_columns("positions")

    if "exchange" in columns and "position_side" in columns:
        sql = "SELECT * FROM positions WHERE exchange = %s"
        params: list = [exchange]
        if user_id is not None and "user_id" in columns:
            sql += " AND user_id = %s"
            params.append(user_id)
        sql += " ORDER BY symbol, position_side"
    else:
        # Legacy schema from docs/ddl.sql
        sql = """
            SELECT
                id,
                '' AS username,
                symbol,
                side AS position_side,
                quantity,
                entry_price AS avg_entry_price,
                unrealized_pnl,
                0 AS realized_pnl,
                leverage,
                margin_type,
                created_at AS updated_at
            FROM positions
            ORDER BY symbol, side
        """
        params = []

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
    """删除（清除）指定头寸记录，兼容旧版与新版 positions 表结构。"""
    columns = _get_table_columns("positions")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if "exchange" in columns and "position_side" in columns and "user_id" in columns:
                cur.execute(
                    """DELETE FROM positions
                       WHERE user_id = %s AND exchange = %s
                         AND symbol = %s AND position_side = %s""",
                    (user_id, exchange, symbol, position_side),
                )
            else:
                legacy_side = "SHORT" if position_side == "SHORT" else "LONG"
                cur.execute(
                    "DELETE FROM positions WHERE symbol = %s AND side = %s",
                    (symbol, legacy_side),
                )
            conn.commit()
            return cur.rowcount > 0
    finally:
        conn.close()


# ──────────────────────────────────────────────
# Operation log（操作日志）
# ──────────────────────────────────────────────

def log_operation(
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
    finally:
        conn.close()


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
    position_id: Optional[int] = None,
) -> int:
    """插入一条持仓历史记录，返回新行 id。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO position_history
                   (user_id, username, symbol, side, entry_price, close_price, quantity, realized_pnl, commission, position_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (user_id, username, symbol, side.upper(), entry_price, close_price, quantity, realized_pnl, commission, position_id),
            )
            conn.commit()
            return cur.lastrowid
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


def get_position(user_id: int, symbol: str, position_side: str, exchange: str = "binance") -> Optional[dict]:
    """按 user_id + symbol + position_side 查询单条持仓，失败返回 None。兼容旧版表结构。"""
    columns = _get_table_columns("positions")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if "user_id" in columns and "exchange" in columns and "position_side" in columns:
                cur.execute(
                    """SELECT * FROM positions
                       WHERE user_id = %s AND exchange = %s AND symbol = %s AND position_side = %s
                       LIMIT 1""",
                    (user_id, exchange, symbol, position_side),
                )
            else:
                # Legacy schema: no user_id/exchange, uses side instead of position_side
                legacy_side = position_side  # LONG/SHORT maps directly to side in legacy
                cur.execute(
                    "SELECT * FROM positions WHERE symbol = %s AND side = %s LIMIT 1",
                    (symbol, legacy_side),
                )
            return cur.fetchone()
    finally:
        conn.close()


def get_position_history(user_id: Optional[int] = None, limit: int = 200) -> list:
    """返回持仓历史记录。user_id=None 时返回所有用户。"""
    params: list = []
    sql = """SELECT id, user_id, username, symbol, side, entry_price, close_price,
                    quantity, realized_pnl, commission, created_at
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
