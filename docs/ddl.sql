CREATE TABLE users (
    id                 BIGINT       NOT NULL PRIMARY KEY AUTO_INCREMENT,
    username           VARCHAR(64)  NOT NULL,
    password_hash      VARCHAR(128) NOT NULL,
    role               ENUM('admin','user') NOT NULL DEFAULT 'user',
    is_active          TINYINT(1)   NOT NULL DEFAULT 1,
    binance_api_key    TEXT         DEFAULT NULL COMMENT 'Binance API Key (encrypted)',
    binance_api_secret TEXT         DEFAULT NULL COMMENT 'Binance API Secret (encrypted)',
    created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE orders (
    id                BIGINT          NOT NULL PRIMARY KEY AUTO_INCREMENT,
    user_id           BIGINT          NOT NULL COMMENT '用户ID',
    username          VARCHAR(64)     NOT NULL COMMENT '用户名',
    exchange          VARCHAR(32)     NOT NULL DEFAULT 'binance' COMMENT '交易所',
    source            ENUM('trade_relay','external') NOT NULL DEFAULT 'trade_relay' COMMENT '订单来源: trade_relay=本系统下单, external=外部工具下单',
    symbol            VARCHAR(32)     NOT NULL COMMENT '交易对',
    side              ENUM('BUY','SELL') NOT NULL COMMENT '方向',
    order_type        VARCHAR(32)     NOT NULL COMMENT '订单类型',
    quantity          DECIMAL(20,8)   NOT NULL COMMENT '委托数量',
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
    trade_direction   ENUM('OPEN','CLOSE') DEFAULT NULL COMMENT '开仓/平仓',
    position_mode     VARCHAR(16)     NOT NULL DEFAULT 'UNKNOWN' COMMENT '持仓方式 SINGLE/DUAL/UNKNOWN',
    reduce_only       TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '只减仓',
    post_only         TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '只做Maker',
    position_id       BIGINT          DEFAULT NULL COMMENT '关联持仓ID',
    order_category    ENUM('Basic','Conditional') NOT NULL DEFAULT 'Basic' COMMENT '订单分类',
    error_message     TEXT            COMMENT '错误信息',
    created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_user_status (user_id, status),
    KEY idx_user_created_at (user_id, created_at),
    KEY idx_status_created (status, created_at),
    KEY idx_category_status_created (order_category, status, created_at),
    KEY idx_user_category_status_created (user_id, order_category, status, created_at),
    KEY idx_username_status_created (username, status, created_at),
    KEY idx_user_symbol_status_filled_at (user_id, symbol, status, filled_at),
    KEY idx_username_exchange_order (username, exchange_order_id),
    KEY idx_username_algo_id (username, algo_id),
    KEY idx_trade_details_retry_due (status, trade_details_sync_next_retry_at),
    KEY idx_created_at (created_at DESC),
    CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE positions (
    id              BIGINT          NOT NULL PRIMARY KEY AUTO_INCREMENT,
    user_id         BIGINT          NOT NULL,
    username        VARCHAR(64)     NOT NULL,
    exchange        VARCHAR(32)     NOT NULL DEFAULT 'binance',
    symbol          VARCHAR(32)     NOT NULL,
    position_side   ENUM('LONG','SHORT','BOTH') NOT NULL DEFAULT 'BOTH',
    position_mode   VARCHAR(16)     NOT NULL DEFAULT 'UNKNOWN' COMMENT '持仓方式 SINGLE/DUAL/UNKNOWN',
    quantity        DECIMAL(20,8)   NOT NULL DEFAULT 0 COMMENT '持仓数量（负数为空头）',
    avg_entry_price DECIMAL(20,8)   DEFAULT NULL COMMENT '开仓均价',
    liquidation_price DECIMAL(20,8) DEFAULT NULL COMMENT '清算价',
    unrealized_pnl  DECIMAL(20,8)   DEFAULT NULL COMMENT '未实现盈亏',
    realized_pnl    DECIMAL(20,8)   NOT NULL DEFAULT 0 COMMENT '已实现盈亏',
    leverage        SMALLINT        NOT NULL DEFAULT 1 COMMENT '杠杆倍数',
    margin_type     ENUM('ISOLATED','CROSS') NOT NULL DEFAULT 'CROSS',
    updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_position (user_id, exchange, symbol, position_side),
    CONSTRAINT fk_positions_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE operation_logs (
    id         BIGINT      NOT NULL PRIMARY KEY AUTO_INCREMENT,
    user_id    BIGINT      DEFAULT NULL,
    username   VARCHAR(64) DEFAULT NULL,
    action     VARCHAR(64) NOT NULL,
    details    TEXT,
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_created_at (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE position_history (
    id            BIGINT          NOT NULL PRIMARY KEY AUTO_INCREMENT,
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
    created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    KEY idx_user_id (user_id),
    KEY idx_username (username),
    KEY idx_symbol (symbol),
    KEY idx_created_at (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='持仓历史';

CREATE TABLE daily_profile (
    id            BIGINT          NOT NULL PRIMARY KEY AUTO_INCREMENT,
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
    UNIQUE KEY uk_user_date (user_id, profile_date),
    KEY idx_profile_date (profile_date, pnl DESC),
    KEY idx_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='每日收益汇总';

CREATE TABLE ticker_messages (
    id          BIGINT   NOT NULL PRIMARY KEY AUTO_INCREMENT,
    contents_zh TEXT     NOT NULL COMMENT '中文播报内容',
    contents_en TEXT     NOT NULL COMMENT '英文播报内容',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    KEY idx_created_at (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='滚动播报文案表';

CREATE TABLE tickers (
    id                    INT          NOT NULL AUTO_INCREMENT COMMENT '主键ID',
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='交易对表';

CREATE TABLE account_summary (
    id                   BIGINT          NOT NULL PRIMARY KEY AUTO_INCREMENT,
    user_id              BIGINT          NOT NULL COMMENT '用户ID（关联 users.id）',
    symbol               VARCHAR(32)     DEFAULT NULL COMMENT '交易对（NULL 表示全局）',
    base_asset           VARCHAR(16)     DEFAULT NULL,
    quote_asset          VARCHAR(16)     DEFAULT NULL,
    position_mode        VARCHAR(16)     DEFAULT NULL COMMENT '持仓方式 SINGLE/DUAL/UNKNOWN',
    leverage             INT             DEFAULT NULL COMMENT '用户最近设置的杠杆',
    configured_leverage  INT             DEFAULT NULL,
    long_position_qty    DECIMAL(30,10)  DEFAULT NULL,
    short_position_qty   DECIMAL(30,10)  DEFAULT NULL,
    long_position_value  DECIMAL(30,10)  DEFAULT NULL,
    short_position_value DECIMAL(30,10)  DEFAULT NULL,
    rest_mark_price      DECIMAL(30,10)  DEFAULT NULL,
    available_balance    DECIMAL(30,10)  DEFAULT NULL,
    margin_ratio         DECIMAL(20,10)  DEFAULT NULL,
    risk_rate            DECIMAL(20,10)  DEFAULT NULL,
    maint_margin         DECIMAL(30,10)  DEFAULT NULL,
    total_equity         DECIMAL(30,10)  DEFAULT NULL,
    position_value       DECIMAL(30,10)  DEFAULT NULL,
    actual_leverage      DECIMAL(20,10)  DEFAULT NULL,
    unrealized_pnl       DECIMAL(30,10)  DEFAULT NULL,
    wallet_balance       DECIMAL(30,10)  DEFAULT NULL,
    has_api_credentials  TINYINT(1)      NOT NULL DEFAULT 0,
    message              TEXT            DEFAULT NULL,
    synced_at            DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '最后同步时间',
    UNIQUE KEY uk_user_symbol (user_id, symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='账户快照缓存（后台定时同步）';

CREATE TABLE income_history (
    id            BIGINT          NOT NULL PRIMARY KEY AUTO_INCREMENT,
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
    UNIQUE KEY uk_income_event (user_id, exchange, tran_id, trade_id, income_type, income_time, symbol, asset),
    KEY idx_income_user_time (user_id, income_time),
    KEY idx_income_user_type_time (user_id, income_type, income_time),
    CONSTRAINT fk_income_history_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易所 income history 资金流水';

-- ============================================================
-- 存量库升级脚本（初次部署后第一次执行，重复执行无影响）
-- ============================================================

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER created_at;

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS source ENUM('trade_relay','external') NOT NULL DEFAULT 'trade_relay' COMMENT '订单来源: trade_relay=本系统下单, external=外部工具下单' AFTER exchange,
    ADD COLUMN IF NOT EXISTS algo_id VARCHAR(64) DEFAULT NULL COMMENT '条件单算法订单ID' AFTER status,
    ADD COLUMN IF NOT EXISTS algo_client_id VARCHAR(64) DEFAULT NULL COMMENT '条件单客户端算法订单ID' AFTER algo_id,
    ADD COLUMN IF NOT EXISTS trade_direction ENUM('OPEN','CLOSE') DEFAULT NULL COMMENT '开仓/平仓' AFTER commission_asset,
    ADD COLUMN IF NOT EXISTS position_mode VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN' COMMENT '持仓方式 SINGLE/DUAL/UNKNOWN' AFTER trade_direction,
    ADD COLUMN IF NOT EXISTS position_id BIGINT DEFAULT NULL COMMENT '关联持仓ID' AFTER trade_direction,
    ADD COLUMN IF NOT EXISTS reduce_only TINYINT(1) NOT NULL DEFAULT 0 COMMENT '只减仓' AFTER trade_direction,
    ADD COLUMN IF NOT EXISTS post_only TINYINT(1) NOT NULL DEFAULT 0 COMMENT '只做Maker' AFTER reduce_only,
    ADD COLUMN IF NOT EXISTS order_category ENUM('Basic','Conditional') NOT NULL DEFAULT 'Basic' COMMENT '订单分类' AFTER position_id,
    ADD COLUMN IF NOT EXISTS tp_price DECIMAL(20,8) DEFAULT NULL COMMENT '计划止盈价' AFTER stop_price,
    ADD COLUMN IF NOT EXISTS sl_price DECIMAL(20,8) DEFAULT NULL COMMENT '计划止损价' AFTER tp_price,
    ADD COLUMN IF NOT EXISTS filled_at DATETIME DEFAULT NULL COMMENT '实际成交时间' AFTER avg_price,
    ADD COLUMN IF NOT EXISTS realized_pnl DECIMAL(30,10) DEFAULT NULL COMMENT '已实现盈亏' AFTER avg_price,
    ADD COLUMN IF NOT EXISTS trade_details_sync_attempts INT NOT NULL DEFAULT 0 COMMENT '成交明细回填重试次数' AFTER commission_asset,
    ADD COLUMN IF NOT EXISTS trade_details_sync_next_retry_at DATETIME DEFAULT NULL COMMENT '成交明细下次回填时间' AFTER trade_details_sync_attempts,
    ADD COLUMN IF NOT EXISTS trade_details_sync_last_error TEXT COMMENT '成交明细最近回填错误' AFTER trade_details_sync_next_retry_at;

ALTER TABLE orders
    MODIFY COLUMN order_category ENUM('Basic','Condition','Conditional') NOT NULL DEFAULT 'Basic' COMMENT '订单分类';

UPDATE orders SET order_category = 'Conditional' WHERE order_category = 'Condition';

UPDATE orders
SET algo_id = exchange_order_id
WHERE order_category = 'Conditional'
    AND (algo_id IS NULL OR TRIM(COALESCE(algo_id, '')) = '')
    AND exchange_order_id IS NOT NULL
    AND TRIM(COALESCE(exchange_order_id, '')) <> '';

UPDATE orders
SET algo_client_id = client_order_id
WHERE order_category = 'Conditional'
    AND (algo_client_id IS NULL OR TRIM(COALESCE(algo_client_id, '')) = '')
    AND client_order_id IS NOT NULL
    AND TRIM(COALESCE(client_order_id, '')) <> '';

ALTER TABLE orders
    MODIFY COLUMN order_category ENUM('Basic','Conditional') NOT NULL DEFAULT 'Basic' COMMENT '订单分类';

ALTER TABLE orders ADD INDEX idx_user_created_at (user_id, created_at);
ALTER TABLE orders ADD INDEX idx_category_status_created (order_category, status, created_at);
ALTER TABLE orders ADD INDEX idx_user_category_status_created (user_id, order_category, status, created_at);
ALTER TABLE orders ADD INDEX idx_username_status_created (username, status, created_at);
ALTER TABLE orders ADD INDEX idx_user_symbol_status_filled_at (user_id, symbol, status, filled_at);
ALTER TABLE orders ADD INDEX idx_username_exchange_order (username, exchange_order_id);
ALTER TABLE orders ADD INDEX idx_username_algo_id (username, algo_id);
ALTER TABLE orders ADD INDEX idx_trade_details_retry_due (status, trade_details_sync_next_retry_at);

ALTER TABLE position_history
    ADD COLUMN IF NOT EXISTS user_id INT NOT NULL DEFAULT 0 COMMENT '用户ID' AFTER id,
    ADD COLUMN IF NOT EXISTS username VARCHAR(64) NOT NULL DEFAULT '' COMMENT '用户名' AFTER user_id,
    ADD COLUMN IF NOT EXISTS side VARCHAR(8) NOT NULL DEFAULT 'LONG' COMMENT '方向 LONG/SHORT' AFTER symbol,
    ADD COLUMN IF NOT EXISTS position_mode VARCHAR(16) NOT NULL DEFAULT 'UNKNOWN' COMMENT '持仓方式 SINGLE/DUAL/UNKNOWN' AFTER side,
    ADD COLUMN IF NOT EXISTS commission_asset VARCHAR(16) DEFAULT NULL COMMENT '手续费币种' AFTER commission,
    ADD COLUMN IF NOT EXISTS position_id BIGINT DEFAULT NULL COMMENT '关联持仓ID（对应 positions.id）' AFTER commission,
    ADD COLUMN IF NOT EXISTS updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间' AFTER created_at;

-- Legacy one-time rename for older databases:
-- ALTER TABLE position_history
--     CHANGE COLUMN update_at updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间';
--
-- Runtime init_db() handles this rename automatically and preserves existing data.

ALTER TABLE daily_profile
    ADD COLUMN IF NOT EXISTS username VARCHAR(64) NOT NULL DEFAULT '' COMMENT '用户名' AFTER user_id,
    ADD COLUMN IF NOT EXISTS account_balance DECIMAL(30,10) DEFAULT NULL COMMENT '更新时的实际钱包余额' AFTER pnl,
    ADD COLUMN IF NOT EXISTS trade_count INT NOT NULL DEFAULT 0 COMMENT '当日交易次数' AFTER pnl,
    ADD COLUMN IF NOT EXISTS win_count INT NOT NULL DEFAULT 0 COMMENT '当日盈利次数' AFTER trade_count,
    ADD COLUMN IF NOT EXISTS win_rate DECIMAL(10,4) NOT NULL DEFAULT 0 COMMENT '当日胜率' AFTER win_count,
    ADD COLUMN IF NOT EXISTS commission DECIMAL(30,10) NOT NULL DEFAULT 0 COMMENT '当日手续费' AFTER win_rate,
    ADD COLUMN IF NOT EXISTS updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间' AFTER commission;

ALTER TABLE daily_profile ADD UNIQUE KEY uk_user_date (user_id, profile_date);
ALTER TABLE daily_profile ADD INDEX idx_profile_date (profile_date, pnl DESC);
ALTER TABLE daily_profile ADD INDEX idx_username (username);

CREATE TABLE IF NOT EXISTS income_history (
    id            BIGINT          NOT NULL PRIMARY KEY AUTO_INCREMENT,
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
    UNIQUE KEY uk_income_event (user_id, exchange, tran_id, trade_id, income_type, income_time, symbol, asset),
    KEY idx_income_user_time (user_id, income_time),
    KEY idx_income_user_type_time (user_id, income_type, income_time),
    CONSTRAINT fk_income_history_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易所 income history 资金流水';

ALTER TABLE income_history
    ADD INDEX idx_income_user_time (user_id, income_time),
    ADD INDEX idx_income_user_type_time (user_id, income_type, income_time);

-- positions 旧结构不再兼容。应用启动时会：
-- 1. 备份旧表到 positions_legacy_backup
-- 2. 重建为当前结构
-- 3. 尝试根据 orders.position_id / position_history.position_id 推断用户归属并迁移数据
-- 4. 无法推断归属的旧行仅保留在备份表中

ALTER TABLE account_summary DROP KEY IF EXISTS uk_user_symbol;
ALTER TABLE account_summary ADD COLUMN IF NOT EXISTS user_id BIGINT NOT NULL DEFAULT 0 COMMENT '用户ID' AFTER id;
ALTER TABLE account_summary DROP COLUMN IF EXISTS username;
ALTER TABLE account_summary ADD COLUMN IF NOT EXISTS position_mode VARCHAR(16) DEFAULT NULL COMMENT '持仓方式 SINGLE/DUAL/UNKNOWN' AFTER quote_asset;
ALTER TABLE account_summary ADD COLUMN IF NOT EXISTS leverage INT DEFAULT NULL COMMENT '用户最近设置的杠杆' AFTER position_mode;
ALTER TABLE account_summary ADD UNIQUE KEY uk_user_symbol (user_id, symbol);
