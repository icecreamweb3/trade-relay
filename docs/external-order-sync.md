# 外部订单同步方案

## 背景

用户除了通过 trade-relay 界面下单，也会使用 TradingView、Binance 客户端等外部工具操作账户。当前系统的委托列表、持仓、历史订单均只呈现 trade-relay 本身创建的记录，外部工具的操作不可见。

## 需求

1. 用户可通过任意外部工具（TradingView、Binance App、Binance Web 等）下单；
2. 外部工具产生的订单和持仓变化，同样展示在 trade-relay 界面的委托列表、历史订单、历史持仓中。

---

## 问题根因

`UserOrderStatusStream` 已通过 Binance User Data Stream 订阅了该账户**全部**订单事件（包括外部工具下的单），但在 `_persist_status` 和 `_handle_close_fill` 中，遇到 `db.get_order_by_exchange_id()` 返回 `None` 时会直接跳过，因为本地 DB 没有该记录。

---

## 核心方案：外部订单自动接管（Order Adoption）

不改变现有业务逻辑流程。检测到 `exchange_order_id` 不在本地 DB 时，将其自动写入 `orders` 表并打上 `source='external'` 标签，之后所有已有逻辑（状态更新、`position_history` 生成、`daily_profile` 统计等）自然复用。

```
现有流程（本系统下单）:
  下单 → orders(source=trade_relay) → WS推送 → 更新状态 → position_history

新增流程（外部工具下单）:
  外部下单 → Binance账户
              ↓ WS ORDER_TRADE_UPDATE 推送到 UserOrderStatusStream
              ↓ _persist_status: DB未找到 → adopt_external_order()
              ↓ orders(source=external) 写入
              ↓ 后续状态更新、position_history 生成 ← 与本系统下单完全一致
```

---

## 变更清单

### 1. DB Schema — `orders` 表新增 `source` 字段

```sql
ALTER TABLE orders
  ADD COLUMN source ENUM('trade_relay', 'external')
    NOT NULL DEFAULT 'trade_relay'
    COMMENT '订单来源: trade_relay=本系统下单, external=外部工具下单'
  AFTER exchange;
```

`positions` 和 `position_history` 已通过 `ACCOUNT_UPDATE` / 持仓推送自动同步，无需修改。

---

### 2. `trade_relay/database.py`

**2a. `create_order()` 新增 `source` 参数**

```python
def create_order(
    ...,
    source: str = 'trade_relay',   # 新增
) -> int:
```

**2b. 新增 `adopt_external_order()`**

从 WebSocket `ORDER_TRADE_UPDATE` 的 `o` 字段直接构造并插入订单记录。
使用 `INSERT ... ON DUPLICATE KEY UPDATE` 而非纯 `INSERT`，避免 WS 重连时重复写入（同时与已有 `updated_at` 幂等规则保持一致）。

WS 字段映射：

| `orders` 字段       | WS `o` 字段            | 说明                              |
|---------------------|------------------------|-----------------------------------|
| `symbol`            | `s`                    |                                   |
| `side`              | `S`                    | BUY / SELL                        |
| `order_type`        | `o`                    | MARKET / LIMIT / STOP_MARKET 等   |
| `quantity`          | `q`                    | 委托数量                          |
| `price`             | `p`                    | 限价价格                          |
| `stop_price`        | `sp`                   | 触发价                            |
| `exchange_order_id` | `i`                    |                                   |
| `client_order_id`   | `c`                    |                                   |
| `status`            | `X`                    |                                   |
| `filled_qty`        | `z`                    | 累计成交量                        |
| `avg_price`         | `ap`                   | 成交均价                          |
| `reduce_only`       | `R`                    |                                   |
| `trade_direction`   | `R==true` → CLOSE, else OPEN | 从 reduce_only 推断         |
| `position_mode`     | `ps` 派生              | LONG/SHORT → DUAL，BOTH → SINGLE  |
| `order_category`    | `o` 派生               | 复用现有 `_normalize_order_category` |
| `source`            | —                      | 固定 `'external'`                 |

---

### 3. `trade_relay/trading/order_status_stream.py`

**3a. `_persist_status()` — 核心改动**

```python
def _persist_status(self, order: dict) -> None:
    exchange_order_id = str(order.get("i") or "")
    db_order = db.get_order_by_exchange_id(self.username, exchange_order_id)
    if db_order is None:
        # 外部工具下的单：自动接管
        db.adopt_external_order(self.username, exchange_order_id, order)
        db_order = db.get_order_by_exchange_id(self.username, exchange_order_id)
        if db_order is None:
            return  # 写入失败则跳过
    # 后续已有状态更新逻辑不变 ...
```

**3b. `_bootstrap_open_orders()` — 新增，在 stream 启动后立即调用**

- 调用 Binance REST `GET /fapi/v1/openOrders`（不带 symbol，获取全账户挂单）；
- 对每个本地 DB 中找不到的订单调用 `db.adopt_external_order()`；
- 同样处理条件单（`GET /fapi/v1/openAlgoOrders`）；
- 目的：系统重启后能立即补齐已有外部挂单，不需要等待下一次 WS 推送。

**3c. `_handle_close_fill()` — 无需改动**

`_persist_status` 在 `_handle_close_fill` 之前被调用，`trade_direction` 已由 `reduce_only` 推断写入，`position_history` 生成路径自然触发。

---

### 4. 前端

- `Order` 类型新增 `source?: 'trade_relay' | 'external'`；
- 当前委托、历史订单列表中对 `source === 'external'` 的订单加"外部"角标，便于用户区分；
- 过滤栏可选增加"来源"筛选维度。

---

## 已知限制

### `trade_direction` 推断精度

`reduce_only=true` → CLOSE 是最可靠的判断。单向持仓模式下，用户用反向市价单平仓但未勾选 reduce_only，此时 `trade_direction` 会被标为 OPEN，`position_history` 不会自动生成。后续可通过持仓数量变化反推方向改善。

### 历史平仓订单不自动回填

外部工具已平仓的历史订单（不在 `openOrders` 中）目前不自动补齐，需要额外 backfill 脚本通过 `GET /fapi/v1/allOrders` 拉取历史，模式与现有 `scripts/backfill_*.py` 一致，可作为后续迭代。

### TP/SL 联动

外部开仓单接管后，系统不会自动为其创建止盈止损（无法得知用户意图）。前端可提供"为此订单补设 TP/SL"的交互入口。

---

## 实施顺序

| 步骤 | 文件 | 内容 |
|------|------|------|
| 1 | `docs/ddl.sql` | 新增 `source` 列及 `ALTER TABLE` 升级语句 |
| 2 | `trade_relay/database.py` | `create_order` 加参数；实现 `adopt_external_order` |
| 3 | `trade_relay/trading/order_status_stream.py` | `_persist_status` 接管逻辑；新增 `_bootstrap_open_orders` |
| 4 | `src/types/` + 订单列表组件 | 前端类型 + "外部"标签 UI |
