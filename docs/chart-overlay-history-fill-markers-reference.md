# Trade Relay — 历史成交箭头标记实现说明

## 目的

本文总结“在 Binance / TradingView 图表上用箭头标记历史成交”的实现方案，供后续新增功能、修复回归或排查时间对齐问题时参考。

本文关注的是“历史成交如何变成图表上的箭头标记”，不展开说明“隐藏箭头但不删除 TV 原生绘图元素”的清理方案。后者见：

- `docs/chart-overlay-arrow-hide-reference.md`

---

## 相关文件

- `trade_relay/database.py`
- `backend/routers/orders.py`
- `src/api/client.ts`
- `src/hooks/useMarketData.ts`
- `electron/preload.js`
- `electron/main.js`
- `electron/binance-preload.js`

---

## 总体链路

历史成交箭头不是直接由后端推送到图表，而是由前端定时拉取、再通过 Electron IPC 转发到 Binance BrowserView 内部的 TradingView 图表。

完整链路如下：

1. 后端从 `orders` 表中查询当前用户、当前 symbol 的已成交订单。
2. 路由 `/api/orders/markers` 把这些记录裁剪为前端需要的 marker 结构。
3. React `useMarketData.ts` 周期性调用 `api.getOrderMarkers()`。
4. 前端把 marker 行映射为 overlay signal。
5. 前端通过 `window.electronAPI.setChartOverlaySignals()` 发给主进程。
6. `electron/main.js` 将 `overlay-signals` 转发给 Binance BrowserView。
7. `electron/binance-preload.js` 在 TradingView 图表上调用 `createShape()` 绘制箭头和文本标签。

---

## 数据来源

### 数据表

箭头标记的数据来源是 `orders` 表，不是单独的 marker 表。

当前实现依赖以下字段：

- `symbol`
- `side`
- `trade_direction`
- `order_type`
- `order_category`
- `filled_qty`
- `avg_price`
- `created_at`
- `updated_at`
- `filled_at`
- `status`

其中最关键的是：

- `status = 'FILLED'`
- `filled_qty > 0`
- `avg_price IS NOT NULL`

### 数据库查询

`trade_relay/database.py` 中的 `get_user_filled_order_markers()` 负责读取 marker 源数据。

查询策略：

- 只查当前用户自己的数据
- 只查当前 symbol
- 只查 `FILLED` 订单
- 只查 `filled_qty > 0`
- 使用 `idx_user_symbol_status_filled_at` 索引
- 默认按 `filled_at DESC` 排序
- 默认限制 `limit <= 200`

这意味着：

- 图表箭头只显示当前登录用户的历史成交
- 不会混入其他用户的数据
- 不会显示未成交或无均价的订单

---

## 后端 API 结构

### 路由

`backend/routers/orders.py` 提供：

- `GET /api/orders/markers?symbol=BTCUSDC&limit=200`

当前权限规则：

- 使用当前登录用户 `user_id`
- 不走管理员跨用户查询逻辑

也就是说，这个接口天然是“当前用户视角”的图表成交标记接口。

### 输出结构

前端使用的结构是 `ApiOrderMarker`：

- `id`
- `username`
- `symbol`
- `side`
- `trade_direction`
- `order_type`
- `order_category`
- `filled_qty`
- `avg_price`
- `created_at`
- `updated_at`
- `filled_at`

路由中的 `_row_to_marker_out()` 还做了两个重要标准化：

1. `trade_direction` 统一转为大写
2. `order_category == 'Condition'` 时改写为 `Conditional`

这样前端就不需要同时兼容多种大小写和旧字段值。

---

## 前端映射规则

### 拉取时机

`src/hooks/useMarketData.ts` 中有一个专门的 effect 负责 marker 同步。

触发条件：

- 用户已登录
- 当前 `symbol` 存在
- `chartOrderMarkersVisible = true`

同步策略：

- 初始化立即拉取一次
- 每 15 秒刷新一次
- 每次只保留最新请求结果，旧请求由 `requestSequence` 丢弃

### 时间字段优先级

历史成交箭头最容易出错的地方是“应该锚定在哪个时间”。当前实现的优先级是：

1. `filled_at`
2. 条件单且存在 `updated_at` 时，使用 `updated_at`
3. `created_at`
4. 最后兜底 `updated_at`

这段逻辑在 `resolveMarkerTimestamp()` 中实现。

原因：

- 对普通已成交单，`filled_at` 最能代表真实成交时间
- 对部分历史条件单或老数据，可能没有稳定的 `filled_at`，此时用 `updated_at` 更接近触发/成交时刻
- 极端情况下再回退到 `created_at`

### signal 字段映射

`mapOrderMarkersToOverlaySignals()` 会把 `ApiOrderMarker` 转成 `ChartOverlaySignal`。

核心映射规则：

- `side = SELL` 映射为 `direction = SHORT`
- 其他映射为 `direction = LONG`
- `trade_direction = CLOSE` 映射为 `trade_action = CLOSE`
- 其他默认 `trade_action = OPEN`
- `avg_price` 作为 `entry_price`
- `filled_qty` 作为 `quantity`
- `timestamp` 使用 `resolveMarkerTimestamp()` 的结果
- `_overlayBarTimeSec` 为 UTC 秒级时间戳
- `display_time` 为本地时区 `HH:mm`

同时会先过滤掉以下无效行：

- `avg_price` 非数字或 `<= 0`
- 无法解析有效时间

### 排序策略

后端查询按 `filled_at DESC` 返回，但前端会再次按时间升序排序后再绘制。

这是有意设计：

- 旧成交先画
- 新成交后画
- 更符合图表上时间从左到右的阅读顺序

---

## 图表绘制方案

### IPC 转发

前端通过 `window.electronAPI.setChartOverlaySignals(signals, locale)` 发起 IPC。

桥接层：

- `electron/preload.js`

主进程转发层：

- `electron/main.js`

BrowserView 接收层：

- `electron/binance-preload.js`

### 画箭头的核心函数

真正绘制发生在 `electron/binance-preload.js`：

- `drawSignalsOnChart(chart, signals)`
- `_redrawArrows(chart)`

绘制形式：

- `LONG` 使用 `arrow_up`
- `SHORT` 使用 `arrow_down`
- 可选额外绘制一个 `text` shape 作为标签

每个 overlay shape 都会通过 `_trackOverlayShapeId()` 进入追踪列表 `_drawnShapes`，供后续更新、重绘和清理。

### 标签策略

标签不是永远全量显示，而是有两层控制：

1. 用户设置 `chartOrderMarkerLabelsVisible`
2. 当前可见区间和数量限制

当前行为：

- 当 `show_label = false` 时，不绘制文本标签
- 当 label 打开时，只对可见区间内的标记或最近一部分标记显示完整标签

这样做是为了避免图表上同时出现大量成交文本，影响可读性。

### 图表时间对齐

绘制前，preload 不会立刻往图上画，而是会先等待图表切换到正确 symbol：

- `waitForChartSymbol(expectedPair)`
- `waitForTvChart()`

只有确认当前 TradingView 图表已经切到对应交易对，并且 bar 数据准备好后，才会真正调用 `drawSignalsOnChart()`。

这是为了避免在 symbol 切换期间把箭头画到旧图上，或者在 bars 还没加载时被 TV 自己后续重绘冲掉。

---

## 与历史成交“详情展示”的关系

当前 entry arrow 只是成交入口标记，不等于完整交易详情。

在 preload 中还存在一套配套机制：

- 点击 marker 后可显示 tooltip
- 选中某个 signal 后可绘制 SL / TP detail lines

相关函数包括：

- `_syncOverlayDrawingEvents()`
- `_showOverlayTooltip()`
- `drawSignalDetail()`

也就是说，历史成交箭头方案本身已经预留了从“只看箭头”扩展到“看该笔成交的细节辅助线”的能力。

---

## 为什么不直接用 websocket 实时成交去画

当前实现选择“定时查询已落库的 FILLED 历史记录”，而不是直接用实时订单 websocket 事件去画，主要有三个原因：

1. 数据口径统一
   前端看到的是后端最终确认并持久化后的成交记录，而不是瞬时事件。

2. 页面重开可恢复
   即使 Electron 重启或前端刷新，只要数据库里还在，marker 就能重新拉取并重绘。

3. 可兼容回填
   通过 `filled_at`、`avg_price`、`filled_qty` 回填历史数据后，旧成交也能重新出现在图上。

代价是：

- 不是毫秒级实时刷新
- 当前同步周期约为 15 秒

这个取舍是有意的，重点是稳定和可恢复，而不是极限实时性。

---

## 已知约束

### 1. 依赖 `filled_at` 质量

如果订单数据没有稳定写入 `filled_at`，前端只能回退到 `updated_at` / `created_at`，这可能让箭头位置偏离真实成交时间。

### 2. 只显示当前 symbol

marker 接口按 symbol 查询，因此切换币对后，图上会重载该币对自己的成交箭头，不会跨 symbol 混显。

### 3. 默认数量上限 200

单个 symbol 只取最近最多 200 条，避免 BrowserView 内 shape 数量无限增长。

### 4. 标签显示经过裁剪

即使 marker 本身存在，也不代表完整文本标签一定显示；标签还受可见范围和数量策略影响。

---

## 后续排查建议

如果以后再次出现“历史成交箭头不显示”或“时间错位”，建议按下面顺序排查。

### 第一步：确认后端接口有数据

检查：

- `/api/orders/markers` 是否返回记录
- 返回行里 `filled_qty`、`avg_price`、`filled_at` 是否正常

### 第二步：确认前端映射后没有被过滤掉

检查：

- `avg_price > 0`
- `resolveMarkerTimestamp()` 是否得到有效时间
- `signals.length` 是否大于 0

### 第三步：确认 signals 已经发往 BrowserView

看日志：

- `chart order markers fetched`
- `chart overlay signals sent`
- `[OVERLAY_IPC] phase=send { action: 'signals' }`

### 第四步：确认 preload 真正完成绘制

看日志：

- `[BINANCE_OVERLAY] overlay-signals received`
- `[BINANCE_OVERLAY] overlay-signals drawn`

### 第五步：如果箭头存在但时间错位

重点检查：

- `filled_at` 的写入来源
- 条件单是否走了 `updated_at` 回退
- `parseUtcTimestamp()` / `_parseTsUtcSec()` 的 UTC 解析是否符合当前数据格式
- symbol 切换时 `waitForChartSymbol()` 是否在正确时机后才绘制

---

## 结论

这套历史成交箭头方案的核心是：

1. 用后端已落库的 FILLED 历史记录作为唯一数据源。
2. 用 `filled_at` 优先的时间规则保证箭头尽量锚定真实成交时刻。
3. 用前端定时同步 + Electron IPC + BrowserView preload 绘制的方式，把业务数据稳定投射到 TradingView 图表。

只要后续继续保持这三个原则，历史成交箭头功能就能同时兼顾：

- 数据口径稳定
- 页面重启可恢复
- 图表绘制行为可控
- 出问题时可逐段排查