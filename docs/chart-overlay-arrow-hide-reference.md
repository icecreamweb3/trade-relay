# Trade Relay — 图表箭头隐藏与 TV 原生绘图保护方案

## 目的

本文总结本次“隐藏成交箭头时，不应删除 TradingView 原生趋势线/绘图元素”的最终技术方案，供后续出现类似问题时快速排查和复用。

适用场景：

- 设置中关闭“图表订单标记”后，箭头没有隐藏。
- 点击清理箭头后，箭头被清掉，但 TradingView 自带趋势线、水平线等也被一起删除。
- 日志中看不到箭头清理链路，无法判断问题出在前端、IPC 还是 Binance BrowserView preload。

---

## 相关文件

- `src/hooks/useMarketData.ts`
- `src/components/ConfigScreen.tsx`
- `src/components/TitleBar.tsx`
- `electron/preload.js`
- `electron/main.js`
- `electron/binance-preload.js`

---

## 背景架构

当前箭头不是 React DOM 元素，而是通过 Electron 注入 Binance 页面的 TradingView 图表 overlay shape。

链路如下：

1. React 前端在 `useMarketData.ts` 中定时调用 `/api/orders/markers`。
2. 前端把 marker 数据映射为 overlay signals。
3. 前端通过 `window.electronAPI.setChartOverlaySignals()` 把 signals 发给 Electron 主进程。
4. `electron/main.js` 将 `overlay-signals` 转发给 Binance BrowserView。
5. `electron/binance-preload.js` 在 TradingView 图表上调用 `createShape()` 绘制 `arrow_up` / `arrow_down` / `text`。

因此，“隐藏箭头”本质上不是前端 UI 显隐，而是要正确清理 BrowserView 内部已经画到 TradingView 上的 overlay shapes。

---

## 问题现象

### 现象 1

关闭“图表订单标记”后，页面设置状态已经变为隐藏，但图表箭头仍然存在。

### 现象 2

使用强制清理时，箭头可以消失，但用户手工画的趋势线、TV 原生绘图元素也一起被删除。

### 现象 3

从 Electron 日志看不到 overlay 的发送、绘制、清理结果，导致无法判断是：

- 没有发送 signals
- signals 发出了但 preload 没收到
- preload 收到了但没有追踪 shape id
- shape 已存在但不在追踪列表内

---

## 根因总结

本次问题不是单一 bug，而是多个问题叠加。

### 根因 1：不能使用全量清理 API

像 `removeAllShapes()` 这种“核弹式”清理会删除整张 TradingView 图表上的所有 shape，包括用户自己画的趋势线、水平线、标注等。

这类 API 只能用于极端调试，不能作为正式的“隐藏箭头”实现。

### 根因 2：overlay shape 追踪列表可能为空

清理时依赖 `_drawnShapes` 删除已知 overlay shape，但实际运行中可能出现以下情况：

- 某些旧箭头仍然留在图表上，但没有进入当前 `_drawnShapes`
- 页面重载或图表重建后，视觉上还存在残留 shape，但当前运行时状态中 `drawnShapeCount = 0`
- 这会导致“清理逻辑执行成功”，但实际上没有删除任何箭头

### 根因 3：删除失败时不能直接丢弃 shape id

早期逻辑里，`removeEntity()` 即使失败，也会把 `_drawnShapes` 清空。结果是：

- 箭头没有真正删除
- 但系统失去了这些 shape 的追踪 id
- 后续再次清理也删不到

### 根因 4：日志不可观测

在补日志之前，Electron 日志无法回答以下关键问题：

- 前端是否成功拉到 `/api/orders/markers`
- 前端是否真的发送了 `set-chart-overlay-signals`
- 主进程是否转发了 `overlay-signals`
- preload 是否真的绘制了 arrows
- clear 时到底删除了几个 tracked shape / residual shape

---

## 最终方案

### 1. 正式隐藏入口只使用设置项

正式逻辑以设置页中的“图表订单标记”开关为准：

- 当 `chartOrderMarkersVisible = false` 时，前端不再继续推送新的 overlay signals。
- 同时触发 `clearChartOverlaySignals()`，要求 BrowserView 清理现存箭头。

这部分在 `src/hooks/useMarketData.ts` 中完成。

### 2. 清理时只删除 overlay 自己的 shape

在 `electron/binance-preload.js` 中，`clearOverlayShapes(chart)` 只应删除以下内容：

- `_drawnShapes` 中记录的 entry arrow / label shape
- `_detailShapes` 中记录的止盈止损辅助线
- `_detailOrderLines` 中记录的附属线对象

不能直接调用任何会影响整张图表所有 shape 的全量删除 API。

### 3. 删除失败时保留失败 id，允许下次重试

新增 `_removeTrackedShapeIds()` 后，清理逻辑会记录：

- `attemptedCount`
- `removedCount`
- `failedCount`
- `failedIds`
- `errors`

如果某个 `removeEntity(id)` 失败，该 id 会继续保留在追踪列表中，而不是被无条件丢弃。这样后续仍可重试删除。

### 4. 增加“残留箭头”兜底清理

即使 `_drawnShapes` 为空，仍可能有旧 overlay 箭头残留在 TV 图表中。为此增加：

- `_getChartShapes(chart)`
- `_summarizeShape(shape)`
- `_looksLikeResidualOverlayShape(shape)`
- `_removeLikelyResidualOverlayShapes(chart)`

这套兜底逻辑会扫描 `chart.getAllShapes()` 返回结果，并识别明显属于 overlay 的残留 shape，例如：

- `arrow_up`
- `arrow_down`
- 包含 `OPEN` / `CLOSE` / `@` 的 marker 文本 shape

再只删除这些“看起来像 overlay 的残留 shape”，而不是删除所有 TV shape。

这个兜底逻辑是本次最终能稳定隐藏箭头、同时保留趋势线的关键。

### 5. 补齐整条链路日志

为后续排查，补充了三层日志。

#### 前端层

`src/hooks/useMarketData.ts` 记录：

- `chart order markers fetched`
- `chart order markers mapped to empty overlay signals`
- `chart overlay signals sent`

用于确认前端是否拿到 marker 数据，以及是否真正发出了 overlay signals。

#### 主进程层

`electron/main.js` 记录：

- `[OVERLAY_IPC] phase=send { action: 'signals' | 'clear' | 'probe' | 'clear-debug' }`
- `[OVERLAY_IPC] phase=status`
- `[OVERLAY_IPC] phase=timeout`

用于确认 IPC 是否真的发出，以及 preload 是否回了状态。

#### BrowserView preload 层

`electron/binance-preload.js` 记录：

- `[BINANCE_OVERLAY] overlay-signals received`
- `[BINANCE_OVERLAY] overlay-signals drawn`
- `[BINANCE_OVERLAY] clearOverlayShapes completed`
- `[BINANCE_OVERLAY] clearOverlayShapes partial failure`
- `[BINANCE_OVERLAY] clear overlay on known charts`

用于确认箭头是否真的被绘制，以及清理时删掉了哪些对象。

---

## 明确禁止的做法

以下做法不要再作为正式方案使用：

- 在正常用户流程中调用 `removeAllShapes()`。
- 在正式按钮逻辑中调用 `forceClearChartArrows()` 这类核弹调试接口。
- 清理失败后直接清空 `_drawnShapes`。
- 依赖“前端设置值已改变”来推断 TV 图表上的箭头一定已经消失。

---

## 当前保留的调试能力

为了后续排障，调试代码仍然保留，但默认不作为正式入口暴露：

- `debugProbeChartOverlay`
- `debugClearChartOverlaySignals`
- `forceClearChartArrows`
- `window.__tradeRelayDebug.clearAll()`

其中 `forceClearChartArrows` / `clearAll()` 仍可能删除 TV 全部 shape，只能用于最后手段的调试，不可接回正式按钮逻辑。

标题栏中的 `Clear Arrows` 调试按钮已重新隐藏，只保留设置页作为正式入口。

---

## 后续遇到类似问题时的排查顺序

### 第一步：确认前端是否拿到 marker

看日志里是否有：

- `chart order markers fetched`
- `markerCount > 0`

如果没有，优先查 `/api/orders/markers` 数据源、用户登录态、symbol 是否正确。

### 第二步：确认前端是否发送 overlay signals

看日志里是否有：

- `chart overlay signals sent`
- `[OVERLAY_IPC] phase=send { action: 'signals' }`

如果前端拿到了 marker，但没有 send signals，先查 `useMarketData.ts` 中的可见性条件和 requestSequence 逻辑。

### 第三步：确认 preload 是否真的绘制

看日志里是否有：

- `[BINANCE_OVERLAY] overlay-signals received`
- `[BINANCE_OVERLAY] overlay-signals drawn`

如果收到了但没有 drawn，重点查：

- `waitForChartSymbol()`
- `waitForTvChart()`
- `drawSignalsOnChart()`
- `createShape()` 调用是否报错

### 第四步：确认清理时删的是 tracked shape 还是 residual shape

看 `clearOverlayShapes completed` 中的字段：

- `overlayAttemptedCount`
- `overlayRemovedCount`
- `residualCandidateCount`
- `residualRemovedCount`

如果 `overlayAttemptedCount = 0` 但 `residualRemovedCount > 0`，说明问题主要来自“残留 shape 不在追踪列表”。

### 第五步：只有在最后手段时才用核弹调试接口

如果要使用 `forceClearChartArrows()` 或 `window.__tradeRelayDebug.clearAll()`，必须明确知道这会把 TV 原生图形一起删掉，仅用于确认“当前可见箭头是不是 TV shape”。

---

## 结论

本次稳定方案的核心原则只有两条：

1. 隐藏箭头必须基于 overlay 自己的追踪与残留识别，不能用全量清理替代。
2. 任何涉及 BrowserView + TradingView shape 的问题，都必须让“前端拉数、主进程转发、preload 绘制、preload 清理”四段链路可观测。

只要继续遵守这两条，后续即使再出现“箭头隐藏失效”或“趋势线被误删”，定位成本也会显著降低。