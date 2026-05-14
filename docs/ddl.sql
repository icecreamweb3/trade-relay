CREATE TABLE users (
    id                BIGINT       NOT NULL PRIMARY KEY AUTO_INCREMENT,
    username          VARCHAR(64)  NOT NULL,
    password_hash     VARCHAR(128) NOT NULL,
    role              ENUM('admin','user') NOT NULL DEFAULT 'user',
    is_active         TINYINT(1)   NOT NULL DEFAULT 1,
    binance_api_key   TEXT         DEFAULT NULL COMMENT 'Binance API Key (encrypted)',
    binance_api_secret TEXT        DEFAULT NULL COMMENT 'Binance API Secret (encrypted)',
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE positions (
    id                BIGINT          NOT NULL PRIMARY KEY AUTO_INCREMENT,
    symbol            VARCHAR(32)     NOT NULL COMMENT '交易对',
    side              ENUM('LONG','SHORT') NOT NULL COMMENT '持仓方向',
    quantity          DECIMAL(30,10)  NOT NULL COMMENT '持仓数量',
    entry_price       DECIMAL(30,10)  NOT NULL COMMENT '开仓均价',
    opened_at         DATETIME        NOT NULL COMMENT '开仓时间',
    unrealized_pnl    DECIMAL(30,10)  NOT NULL DEFAULT 0 COMMENT '未实现盈利',
    leverage          INT             NOT NULL DEFAULT 1 COMMENT '杠杆倍数',
    margin_type       ENUM('CROSS','ISOLATED') NOT NULL DEFAULT 'CROSS' COMMENT '保证金类型：全仓/逐仓',
    liquidation_price DECIMAL(30,10)  COMMENT '清算价格',
    created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间'
);

CREATE TABLE position_history (
    id              BIGINT          NOT NULL PRIMARY KEY AUTO_INCREMENT,
    user_id         INT             NOT NULL DEFAULT 0      COMMENT '用户ID',
    username        VARCHAR(64)     NOT NULL DEFAULT ''     COMMENT '用户名',
    symbol          VARCHAR(32)     NOT NULL               COMMENT '交易对',
    side            VARCHAR(8)      NOT NULL DEFAULT 'LONG' COMMENT '方向 LONG/SHORT',
    entry_price     DECIMAL(30,10)  NOT NULL               COMMENT '开仓均价',
    close_price     DECIMAL(30,10)  NOT NULL               COMMENT '平仓价格',
    quantity        DECIMAL(30,10)  NOT NULL               COMMENT '成交数量',
    realized_pnl    DECIMAL(30,10)  NOT NULL DEFAULT 0     COMMENT '已实现盈亏',
    commission      DECIMAL(30,10)  NOT NULL DEFAULT 0     COMMENT '手续费',
    position_id     BIGINT          DEFAULT NULL           COMMENT '关联持仓ID（对应 positions.id）',
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    KEY idx_user_id   (user_id),
    KEY idx_username  (username),
    KEY idx_symbol    (symbol),
    KEY idx_created_at (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='持仓历史';

CREATE TABLE orders (
    id                BIGINT          NOT NULL PRIMARY KEY AUTO_INCREMENT,
    user_id           BIGINT          NOT NULL COMMENT '用户ID',
    username          VARCHAR(64)     NOT NULL COMMENT '用户名',
    exchange          VARCHAR(32)     NOT NULL DEFAULT 'binance' COMMENT '交易所',
    symbol            VARCHAR(32)     NOT NULL COMMENT '交易对',
    side              ENUM('BUY','SELL') NOT NULL COMMENT '方向',
    order_type        VARCHAR(32)     NOT NULL COMMENT '订单类型',
    quantity          DECIMAL(20,8)   NOT NULL COMMENT '委托数量',
    price             DECIMAL(20,8)   DEFAULT NULL COMMENT '委托价格',
    stop_price        DECIMAL(20,8)   DEFAULT NULL COMMENT '止损触发价',
    status            VARCHAR(32)     NOT NULL DEFAULT 'NEW' COMMENT '订单状态',
    exchange_order_id VARCHAR(64)     DEFAULT NULL COMMENT '交易所订单ID',
    client_order_id   VARCHAR(64)     DEFAULT NULL COMMENT '客户端订单ID',
    filled_qty        DECIMAL(20,8)   NOT NULL DEFAULT 0 COMMENT '已成交数量',
    avg_price         DECIMAL(20,8)   DEFAULT NULL COMMENT '成交均价',
    commission        DECIMAL(20,8)   DEFAULT NULL COMMENT '手续费',
    commission_asset  VARCHAR(16)     DEFAULT NULL COMMENT '手续费币种',
    trade_direction   ENUM('OPEN','CLOSE') DEFAULT NULL COMMENT '开仓/平仓',
    reduce_only       TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '只减仓（平仓单为1）',
    post_only         TINYINT(1)      NOT NULL DEFAULT 0 COMMENT '只做Maker',
    position_id       BIGINT          DEFAULT NULL COMMENT '关联持仓ID',
    order_category    ENUM('Basic','Condition') NOT NULL DEFAULT 'Basic' COMMENT '订单分类',
    error_message     TEXT            COMMENT '错误信息',
    created_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    KEY idx_user_status (user_id, status),
    KEY idx_created_at (created_at DESC),
    CONSTRAINT fk_orders_user FOREIGN KEY (user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE ticker_messages (
    id          BIGINT   NOT NULL PRIMARY KEY AUTO_INCREMENT,
    contents_zh TEXT     NOT NULL COMMENT '中文播报内容',
    contents_en TEXT     NOT NULL COMMENT '英文播报内容',
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    KEY idx_created_at (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='滚动播报文案表';


CREATE TABLE `tickers` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `symbol` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '交易对符号',
  `pair` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '交易对名称',
  `base_asset` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '基础资产',
  `quote_asset` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '计价资产',
  `delivery_date` datetime DEFAULT NULL COMMENT '交割日期',
  `onboard_date` datetime DEFAULT NULL COMMENT '上线日期',
  `status` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '状态',
  `fdv_value` float DEFAULT NULL COMMENT 'FDV市值',
  `price_precision` int DEFAULT NULL COMMENT '价格精度（小数位数）',
  `quantity_precision` int DEFAULT NULL COMMENT '数量精度（小数位数）',
  `base_asset_precision` int DEFAULT NULL COMMENT '基础资产精度（小数位数）',
  `quote_asset_precision` int DEFAULT NULL COMMENT '计价资产精度（小数位数）',
  `max_price` float DEFAULT NULL COMMENT '最大价格',
  `min_price` float DEFAULT NULL COMMENT '最小价格',
  `tick_size` float DEFAULT NULL COMMENT '价格步长（tick size）',
  `is_monitor` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否监控(1=是,0=否)',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `symbol` (`symbol`),
  KEY `idx_symbol` (`symbol`),
  KEY `idx_is_monitor` (`is_monitor`),
  KEY `idx_status` (`status`)
) ENGINE=InnoDB AUTO_INCREMENT=588 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='交易对表';

-- ============================================================
-- 存量库升级脚本（初次部署后第一次执行，重复执行无影响）
-- 新增字段：orders.trade_direction, reduce_only, post_only, position_id
--          position_history.user_id, username, side, position_id
-- ============================================================

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS trade_direction  ENUM('OPEN','CLOSE') DEFAULT NULL    COMMENT '开仓/平仓'            AFTER commission_asset,
    ADD COLUMN IF NOT EXISTS reduce_only      TINYINT(1) NOT NULL  DEFAULT 0       COMMENT '只减仓（平仓单为1）'    AFTER trade_direction,
    ADD COLUMN IF NOT EXISTS post_only        TINYINT(1) NOT NULL  DEFAULT 0       COMMENT '只做Maker'               AFTER reduce_only,
    ADD COLUMN IF NOT EXISTS position_id      BIGINT               DEFAULT NULL    COMMENT '关联持仓ID'            AFTER post_only,
    ADD COLUMN IF NOT EXISTS order_category   ENUM('Basic','Condition') NOT NULL DEFAULT 'Basic' COMMENT '订单分类' AFTER position_id;

ALTER TABLE position_history
    ADD COLUMN IF NOT EXISTS user_id     INT         NOT NULL DEFAULT 0      COMMENT '用户ID'                          AFTER id,
    ADD COLUMN IF NOT EXISTS username    VARCHAR(64) NOT NULL DEFAULT ''     COMMENT '用户名'                          AFTER user_id,
    ADD COLUMN IF NOT EXISTS side        VARCHAR(8)  NOT NULL DEFAULT 'LONG' COMMENT '方向 LONG/SHORT'                  AFTER symbol,
    ADD COLUMN IF NOT EXISTS position_id BIGINT               DEFAULT NULL   COMMENT '关联持仓ID（对应 positions.id）'  AFTER commission;
