/**
 * Preload script injected into the Binance BrowserView.
 * Intercepts WebSocket messages to extract real-time market data
 * (OHLCV klines, OI, trade volume) and forwards them to the main process.
 */
const { ipcRenderer } = require('electron')

// ─────────────────────────────────────────────────────────────────────────────
// TV Chart Fullscreen — prevent BrowserView from covering React UI panels
// ─────────────────────────────────────────────────────────────────────────────
// TradingView's "maximize chart" button calls requestFullscreen().  In an
// Electron BrowserView the default handling would expand the view to cover the
// entire app window, hiding the Kaia chat and Market Data panels on the right.
//
// Strategy A (renderer-side): Override requestFullscreen on Element.prototype
//   using Object.defineProperty — covers ALL element types (HTMLElement,
//   SVGElement…) and webkit vendor-prefixed variants.  Fakes success so TV's
//   fullscreenchange listener applies its CSS chart-expansion, while the native
//   Chromium fullscreen IPC is never sent.
//
// Strategy B (main-process): enter-html-full-screen handler in main.js acts as
//   a guaranteed safety net — if Chromium somehow bypasses Strategy A it
//   immediately restores the BrowserView to its 75 % bounds.
// ─────────────────────────────────────────────────────────────────────────────
let _fsElement = null

const _fakeRequestFullscreen = function (_options) {
  _fsElement = this
  // Dispatch fullscreenchange asynchronously so TV's listeners see the update.
  // We fire both the standard and webkit-prefixed events for compatibility.
  Promise.resolve().then(() => {
    this.dispatchEvent(new Event('fullscreenchange',       { bubbles: true }))
    this.dispatchEvent(new Event('webkitfullscreenchange', { bubbles: true }))
    document.dispatchEvent(new Event('fullscreenchange'))
    document.dispatchEvent(new Event('webkitfullscreenchange'))
    ipcRenderer.send('chart-expand-state-change', true)
  })
  return Promise.resolve()
}

const _fakeExitFullscreen = function () {
  const prev = _fsElement
  _fsElement = null
  Promise.resolve().then(() => {
    if (prev) {
      prev.dispatchEvent(new Event('fullscreenchange',       { bubbles: true }))
      prev.dispatchEvent(new Event('webkitfullscreenchange', { bubbles: true }))
    }
    document.dispatchEvent(new Event('fullscreenchange'))
    document.dispatchEvent(new Event('webkitfullscreenchange'))
    ipcRenderer.send('chart-expand-state-change', false)
  })
  return Promise.resolve()
}

// Override on Element.prototype (parent of HTMLElement AND SVGElement) using
// Object.defineProperty so the binding cannot be easily shadowed.
for (const method of ['requestFullscreen', 'webkitRequestFullscreen', 'webkitRequestFullScreen',
                      'mozRequestFullScreen', 'msRequestFullscreen']) {
  Object.defineProperty(Element.prototype, method, {
    value: _fakeRequestFullscreen, writable: true, configurable: true,
  })
}

Object.defineProperty(Document.prototype, 'exitFullscreen',       { value: _fakeExitFullscreen, writable: true, configurable: true })
Object.defineProperty(Document.prototype, 'webkitExitFullscreen', { value: _fakeExitFullscreen, writable: true, configurable: true })
Object.defineProperty(Document.prototype, 'webkitCancelFullScreen', { value: _fakeExitFullscreen, writable: true, configurable: true })
Object.defineProperty(Document.prototype, 'mozCancelFullScreen',  { value: _fakeExitFullscreen, writable: true, configurable: true })

// Expose consistent read state so TV/Binance code that checks these sees the
// correct element (needed for TV's fullscreen CSS class to be applied).
Object.defineProperty(document, 'fullscreenElement',       { get: () => _fsElement, configurable: true })
Object.defineProperty(document, 'webkitFullscreenElement', { get: () => _fsElement, configurable: true })
Object.defineProperty(document, 'fullscreen',              { get: () => _fsElement !== null, configurable: true })
Object.defineProperty(document, 'webkitIsFullScreen',      { get: () => _fsElement !== null, configurable: true })

// ─────────────────────────────────────────────────────────────────────────────
// Kline REST cache — intercept TradingView's own fetch() calls for /klines
// endpoints so we can reuse that data for backtesting without extra API calls.
// ─────────────────────────────────────────────────────────────────────────────

/** Map<"BTCUSDT_1h", bar[]>  – keyed by normalised symbol+interval */
const _klineCache = new Map()

// Tracks the symbol_interval key of the most recent large kline fetch
// (i.e. what the chart itself requested — most reliable indicator of current symbol)
let _lastChartKlineKey = null

const _originalFetch = window.fetch
window.fetch = async function (...args) {
  const response = await _originalFetch.apply(this, args)
  try {
    const url = typeof args[0] === 'string' ? args[0] : args[0]?.url ?? ''
    if (url.includes('klines') || url.includes('Klines')) {
      const urlObj = new URL(url, 'https://www.binance.com')
      // Binance futures: pair= (continuousKlines) or symbol= (klines)
      const symbol   = (urlObj.searchParams.get('symbol') || urlObj.searchParams.get('pair') || '').toUpperCase()
      const interval = urlObj.searchParams.get('interval') || ''
      const limit = parseInt(urlObj.searchParams.get('limit') || '0')
      if (symbol && interval) {
        // Initial chart interval detection: the first large klines REST fetch is
        // the primary chart interval (TradingView loads chart bars before indicators).
        if (_CHART_INTERVALS.has(interval) && limit >= 200 && !_lastChartInterval) {
          _lastChartInterval = interval
          ipcRenderer.send('chart-interval-change', interval)
        }
        const clone = response.clone()
        clone.json().then((bars) => {
          if (!Array.isArray(bars) || !Array.isArray(bars[0])) return
          const parsed = bars.map((b) => ({
            openTime:  Number(b[0]),
            open:      parseFloat(b[1]),
            high:      parseFloat(b[2]),
            low:       parseFloat(b[3]),
            close:     parseFloat(b[4]),
            volume:    parseFloat(b[5]),
            closeTime: Number(b[6]),
            isClosed:  true,
          }))
          const key = `${symbol}_${interval}`
          // Accumulate all fetched batches — TV fetches historical pages as user
          // scrolls back, so merging gives us the full history it used for EMA.
          const existing = _klineCache.get(key)
          if (existing?.length) {
            const byTime = new Map(existing.map(b => [b.openTime, b]))
            for (const b of parsed) byTime.set(b.openTime, b)
            const merged = Array.from(byTime.values()).sort((a, b) => a.openTime - b.openTime)
            _klineCache.set(key, merged.length > 3000 ? merged.slice(-3000) : merged)
          } else {
            _klineCache.set(key, parsed)
          }
          // Track latest chart kline key — large fetches (≥100 bars) are the chart
          // itself loading data; smaller ones are usually indicators or ping-polls.
          if (_CHART_INTERVALS.has(interval) && parsed.length >= 100) {
            _lastChartKlineKey = key
          }
        }).catch(() => {})
      }
    }
  } catch { /* never break the real request */ }
  return response
}

/**
 * Return cached klines for a symbol+interval, latest `limit` bars.
 * Called from main.js via executeJavaScript.
 */
function _getCachedKlines(symbol, interval, limit) {
  // normalise: "BTCUSDT" or "BTC" both match "BTCUSDT"
  const sym = symbol.toUpperCase().endsWith('USDT') ? symbol.toUpperCase() : symbol.toUpperCase() + 'USDT'
  const key = `${sym}_${interval}`
  const bars = _klineCache.get(key)
  if (bars && bars.length > 0) return bars.slice(-limit)
  // fallback: try any key containing the symbol + interval
  for (const [k, v] of _klineCache.entries()) {
    if (k.includes(sym.replace('USDT', '')) && k.endsWith(`_${interval}`)) {
      return v.slice(-limit)
    }
  }
  return null
}

// Intercept WebSocket to capture Binance streaming data
const OriginalWebSocket = window.WebSocket
let _ticker24hWs = null
let _ticker24hSymbol = null

// Track the last-sent chart interval so we only push a change event when it
// actually changes.
let _lastChartInterval = null

// Valid chart intervals (≥1m). Sub-minute streams like 1s/3s/15s are used
// internally by Binance for the real-time price ticker and must be ignored.
const _CHART_INTERVALS = new Set([
  '1m','3m','5m','15m','30m',
  '1h','2h','4h','6h','8h','12h',
  '1d','3d','1w','1M'
])

// WebSocket SUBSCRIBE-based interval detection.
// Used as a fast-path before the TV chart object is ready (chart.resolution()
// is the authoritative source once available — see IIFE at end of file).
// TradingView subscribes to ALL needed kline streams on initial load (batch),
// and sends a NEW single SUBSCRIBE only when the user actively changes the
// chart interval. We detect the latter by collecting subscriptions in a 200ms
// debounce window: if exactly ONE new valid interval is subscribed → it's a
// user-initiated interval change.
const _subscribedKlineIntervals = new Set()
const _pendingSubBatch = []  // new valid intervals accumulated in current batch
let _subBatchTimer = null

/**
 * Convert TradingView resolution string to Binance interval string.
 * TV uses minute counts ("60" = 1h) and special strings ("D", "W", "M").
 */
function _tvResolutionToBinance(res) {
  if (!res) return null
  const r = String(res).trim()
  const minuteMap = {
    '1': '1m', '3': '3m', '5': '5m', '15': '15m', '30': '30m',
    '60': '1h', '120': '2h', '240': '4h', '360': '6h',
    '480': '8h', '720': '12h',
  }
  if (minuteMap[r]) return minuteMap[r]
  const stringMap = {
    'D': '1d', '1D': '1d', '3D': '3d',
    'W': '1w', '1W': '1w',
    'M': '1M', '1M': '1M',
  }
  return stringMap[r] ?? null
}

function _emitTicker24h(data) {
  if (!data?.s) return
  const payload = {
    type: 'ticker24h',
    symbol: data.s,
    lastPrice: parseFloat(data.c),
    priceChange: parseFloat(data.p),
    priceChangePercent: parseFloat(data.P),
    openPrice: parseFloat(data.o),
    highPrice: parseFloat(data.h),
    lowPrice: parseFloat(data.l),
    volume: parseFloat(data.v),
    quoteVolume: parseFloat(data.q),
    openTime: data.O,
    closeTime: data.C,
    eventTime: data.E,
  }
  ipcRenderer.send('market-data', payload)
}

function _logTicker24h(level, message, extra) {
  try {
    ipcRenderer.send('log-to-main', level, `[ticker24h] ${message}`, extra || {})
  } catch {}
  try {
    const method = level === 'error' ? 'error' : level === 'warn' ? 'warn' : 'log'
    console[method](`[OmniTrader][ticker24h] ${message}`, extra || {})
  } catch {}
}

function _ensureTicker24hStream(symbol) {
  const normalized = String(symbol || '').trim().toLowerCase()
  if (!normalized) return
  if (_ticker24hSymbol === normalized && _ticker24hWs && _ticker24hWs.readyState <= 1) return

  try {
    _ticker24hWs?.close()
  } catch {}

  _ticker24hSymbol = normalized
  _logTicker24h('info', 'subscribe', { symbol: normalized })
  const ws = new OriginalWebSocket(`wss://fstream.binance.com/ws/${normalized}@ticker`)
  _ticker24hWs = ws
  let firstMessageLogged = false

  ws.addEventListener('open', () => {
    _logTicker24h('info', 'open', { symbol: normalized })
  })

  ws.addEventListener('message', (event) => {
    try {
      const data = JSON.parse(event.data)
      if (data?.e === '24hrTicker' && String(data.s || '').trim().toLowerCase() === _ticker24hSymbol) {
        if (!firstMessageLogged) {
          firstMessageLogged = true
          _logTicker24h('info', 'first-message', {
            symbol: data.s,
            lastPrice: data.c,
            priceChange: data.p,
            priceChangePercent: data.P,
          })
        }
        _emitTicker24h(data)
      }
    } catch (error) {
      _logTicker24h('warn', 'message-parse-failed', { symbol: normalized, error: String(error) })
    }
  })

  ws.addEventListener('close', (event) => {
    _logTicker24h('warn', 'close', { symbol: normalized, code: event.code, reason: event.reason || '' })
    if (_ticker24hWs !== ws) return
    _ticker24hWs = null
    const retrySymbol = _ticker24hSymbol
    setTimeout(() => {
      if (_ticker24hSymbol === retrySymbol) _ensureTicker24hStream(retrySymbol)
    }, 3000)
  })

  ws.addEventListener('error', () => {
    _logTicker24h('error', 'error', { symbol: normalized })
    try { ws.close() } catch {}
  })
}

class InterceptedWebSocket extends OriginalWebSocket {
  constructor(url, protocols) {
    super(url, protocols)
    this._url = url
    this._setupInterception()
  }

  _setupInterception() {
    // Intercept outgoing SUBSCRIBE / UNSUBSCRIBE messages so we know which
    // kline streams are active. A solo new SUBSCRIBE (outside the initial
    // batch) reliably identifies a user-initiated chart interval change.
    const _origSend = this.send.bind(this)
    this.send = (data) => {
      if (typeof data === 'string') {
        try {
          const msg = JSON.parse(data)
          if (msg.method === 'SUBSCRIBE' && Array.isArray(msg.params)) {
            for (const param of msg.params) {
              if (typeof param !== 'string') continue
              const m = param.match(/@kline_(.+)$/)
              if (m && _CHART_INTERVALS.has(m[1]) && !_subscribedKlineIntervals.has(m[1])) {
                _pendingSubBatch.push(m[1])
                _subscribedKlineIntervals.add(m[1])
              }
            }
            clearTimeout(_subBatchTimer)
            _subBatchTimer = setTimeout(() => {
              // Exactly one new subscription in this batch → chart interval change
              if (_pendingSubBatch.length === 1) {
                const iv = _pendingSubBatch[0]
                if (iv !== _lastChartInterval) {
                  _lastChartInterval = iv
                  ipcRenderer.send('chart-interval-change', iv)
                }
              }
              _pendingSubBatch.splice(0)
            }, 200)
          } else if (msg.method === 'UNSUBSCRIBE' && Array.isArray(msg.params)) {
            for (const param of msg.params) {
              if (typeof param === 'string') {
                const m = param.match(/@kline_(.+)$/)
                if (m) _subscribedKlineIntervals.delete(m[1])
              }
            }
          }
        } catch {}
      }
      return _origSend(data)
    }

    this.addEventListener('message', (event) => {
      try {
        const data = JSON.parse(event.data)
        this._handleBinanceMessage(data)
      } catch {
        // Non-JSON messages are ignored
      }
    })
  }

  _handleBinanceMessage(data) {
    if (data?.s) _ensureTicker24hStream(data.s)

    // Kline/Candlestick stream: <symbol>@kline_<interval>
    if (data.e === 'kline' && data.k) {
      const kline = data.k
      const payload = {
        type: 'kline',
        symbol: data.s,
        interval: kline.i,
        openTime: kline.t,
        open: parseFloat(kline.o),
        high: parseFloat(kline.h),
        low: parseFloat(kline.l),
        close: parseFloat(kline.c),
        volume: parseFloat(kline.v),
        closeTime: kline.T,
        isClosed: kline.x,
        quoteVolume: parseFloat(kline.q),
        takerBuyVolume: parseFloat(kline.V),
      }
      ipcRenderer.send('market-data', payload)

      // Keep _klineCache up-to-date with live WebSocket bars.
      // Without this the cache only contains the historical REST batches TV
      // fetched on load, causing EMA values to be hours behind the chart.
      const liveBar = {
        openTime:  kline.t,
        open:      parseFloat(kline.o),
        high:      parseFloat(kline.h),
        low:       parseFloat(kline.l),
        close:     parseFloat(kline.c),
        volume:    parseFloat(kline.v),
        closeTime: kline.T,
        isClosed:  kline.x,
      }
      const liveKey = `${data.s}_${kline.i}`
      const existing = _klineCache.get(liveKey)
      if (existing && existing.length > 0) {
        const last = existing[existing.length - 1]
        if (last.openTime === liveBar.openTime) {
          // Update the current (forming) bar in-place
          existing[existing.length - 1] = liveBar
        } else if (liveBar.openTime > last.openTime) {
          // New bar opened — append and trim to 3000
          existing.push(liveBar)
          if (existing.length > 3000) existing.shift()
        }
        // (older bar arriving out-of-order: ignore)
      }
      // Update _lastChartKlineKey when the WS kline matches the current chart interval
      // so that switching symbols also updates our active-symbol tracking.
      if (_CHART_INTERVALS.has(kline.i) && kline.i === _lastChartInterval) {
        _lastChartKlineKey = liveKey
      }
      // If cache is empty for this key, we wait for the next REST batch to seed it.
    }

    // Mark price stream (includes funding rate)
    if (data.e === 'markPriceUpdate') {
      const payload = {
        type: 'markPrice',
        symbol: data.s,
        markPrice: parseFloat(data.p),
        indexPrice: parseFloat(data.i),
        fundingRate: parseFloat(data.r),
        nextFundingTime: data.T,
        timestamp: data.E,
      }
      ipcRenderer.send('market-data', payload)
    }

    // Aggregate trade stream
    if (data.e === 'aggTrade') {
      const payload = {
        type: 'trade',
        symbol: data.s,
        price: parseFloat(data.p),
        quantity: parseFloat(data.q),
        isBuyerMaker: data.m,
        timestamp: data.T,
      }
      ipcRenderer.send('market-data', payload)
    }

    if (data.e === '24hrTicker') {
      _emitTicker24h(data)
    }

    // Force liquidation order (useful for OI context)
    if (data.e === 'forceOrder') {
      const payload = {
        type: 'liquidation',
        symbol: data.o?.s,
        side: data.o?.S,
        price: parseFloat(data.o?.p || 0),
        quantity: parseFloat(data.o?.q || 0),
        timestamp: data.E,
      }
      ipcRenderer.send('market-data', payload)
    }
  }
}

// Lock our WebSocket override so Binance JS cannot overwrite it after the preload runs.
// Plain assignment (window.WebSocket = ...) can be undone by subsequent page scripts;
// Object.defineProperty with writable:false prevents that.
Object.defineProperty(window, 'WebSocket', {
  value: InterceptedWebSocket,
  writable: false,
  configurable: false,
})

// ─────────────────────────────────────────────────────────────────────────────
// TradingView Chart Overlay Engine
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Probe all known locations where Binance mounts the TradingView widget.
 * Returns the chart object (which has createShape / createOrderLine) or null.
 *
 * Strategy:
 *  1. Direct global names (tvWidget, etc.)
 *  2. React fiber walk on multiple container selectors
 *  3. Broadened window property scan (any object with .chart() returning TV API)
 *  4. Same-origin iframe scan
 *  5. Angular scope (legacy)
 */
function findTvChart() {
  // 1. Direct known global names
  const directNames = [
    'tvWidget', 'tv_chart_widget', 'TradingViewApi', 'tradingViewApi',
    'tvChartWidget', 'chartWidget', 'tv', 'TV',
  ]
  for (const name of directNames) {
    try {
      const obj = window[name]
      if (obj && typeof obj.chart === 'function') {
        const chart = obj.chart()
        if (chart && typeof chart.createShape === 'function') return chart
      }
    } catch { /* try next */ }
  }

  // 2. React fiber walk on multiple container selectors
  const containerSelectors = [
    '#tv-chart-container',
    '[data-tv-widget]',
    '#chart-container',
    '.chart-container',
    '[id^="tradingview_"]',
    '[class*="tv-chart"]',
    '[data-testid="chart-container"]',
    '[data-chart-source-type]',
  ]
  for (const sel of containerSelectors) {
    try {
      const el = document.querySelector(sel)
      if (!el) continue
      const fiberKey = Object.keys(el).find(
        k => k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
      )
      if (!fiberKey) continue
      let fiber = el[fiberKey]
      for (let depth = 0; depth < 50 && fiber; depth++) {
        const probeTargets = [
          fiber?.memoizedProps?.widget,
          fiber?.stateNode?.tvWidget,
          fiber?.stateNode?.widget,
          fiber?.memoizedState?.widget,
        ]
        for (const w of probeTargets) {
          if (w && typeof w.chart === 'function') {
            try {
              const chart = w.chart()
              if (chart && typeof chart.createShape === 'function') return chart
            } catch { /* stale ref */ }
          }
        }
        fiber = fiber.return
      }
    } catch { /* try next */ }
  }

  // 3. Broadened window scan: any top-level object exposing .chart() → TradingView API
  for (const key of Object.keys(window)) {
    if (key.length > 35 || key.startsWith('__')) continue
    try {
      const obj = window[key]
      if (
        obj && typeof obj === 'object' && !Array.isArray(obj) &&
        typeof obj.chart === 'function'
      ) {
        const chart = obj.chart()
        if (chart && typeof chart.createShape === 'function') return chart
      }
    } catch { /* try next */ }
  }

  // 4. Same-origin iframe scan (Binance loads TradingView in an iframe in some builds)
  for (const iframe of document.querySelectorAll('iframe')) {
    try {
      const w = iframe.contentWindow
      if (!w) continue
      for (const name of directNames) {
        try {
          const obj = w[name]
          if (obj && typeof obj.chart === 'function') {
            const chart = obj.chart()
            if (chart && typeof chart.createShape === 'function') return chart
          }
        } catch { /* cross-origin or missing */ }
      }
    } catch { /* cross-origin iframe */ }
  }

  // 5. Angular scope (legacy Binance builds)
  try {
    const el = document.querySelector('.chart-container, .tv-chart')
    if (el && typeof angular !== 'undefined') {
      const chart = angular.element(el).scope?.()?.tvWidget?.chart?.()
      if (chart && typeof chart.createShape === 'function') return chart
    }
  } catch { /* angular not present */ }

  return null
}

function findTvWidget() {
  const directNames = [
    'tvWidget', 'tv_chart_widget', 'TradingViewApi', 'tradingViewApi',
    'tvChartWidget', 'chartWidget', 'tv', 'TV',
  ]
  for (const name of directNames) {
    try {
      const obj = window[name]
      if (obj && typeof obj.chart === 'function' && typeof obj.subscribe === 'function') {
        const chart = obj.chart()
        if (chart && typeof chart.createShape === 'function') return obj
      }
    } catch { /* try next */ }
  }

  const containerSelectors = [
    '#tv-chart-container',
    '[data-tv-widget]',
    '#chart-container',
    '.chart-container',
    '[id^="tradingview_"]',
    '[class*="tv-chart"]',
    '[data-testid="chart-container"]',
    '[data-chart-source-type]',
  ]
  for (const sel of containerSelectors) {
    try {
      const el = document.querySelector(sel)
      if (!el) continue
      const fiberKey = Object.keys(el).find(
        k => k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
      )
      if (!fiberKey) continue
      let fiber = el[fiberKey]
      for (let depth = 0; depth < 50 && fiber; depth++) {
        const probeTargets = [
          fiber?.memoizedProps?.widget,
          fiber?.stateNode?.tvWidget,
          fiber?.stateNode?.widget,
          fiber?.memoizedState?.widget,
        ]
        for (const widget of probeTargets) {
          if (
            widget && typeof widget.chart === 'function' && typeof widget.subscribe === 'function'
          ) {
            try {
              const chart = widget.chart()
              if (chart && typeof chart.createShape === 'function') return widget
            } catch { /* stale ref */ }
          }
        }
        fiber = fiber.return
      }
    } catch { /* try next */ }
  }

  for (const key of Object.keys(window)) {
    if (key.length > 35 || key.startsWith('__')) continue
    try {
      const obj = window[key]
      if (
        obj && typeof obj === 'object' && !Array.isArray(obj) &&
        typeof obj.chart === 'function' && typeof obj.subscribe === 'function'
      ) {
        const chart = obj.chart()
        if (chart && typeof chart.createShape === 'function') return obj
      }
    } catch { /* try next */ }
  }

  for (const iframe of document.querySelectorAll('iframe')) {
    try {
      const w = iframe.contentWindow
      if (!w) continue
      for (const name of directNames) {
        try {
          const obj = w[name]
          if (
            obj && typeof obj.chart === 'function' && typeof obj.subscribe === 'function'
          ) {
            const chart = obj.chart()
            if (chart && typeof chart.createShape === 'function') return obj
          }
        } catch { /* cross-origin or missing */ }
      }
    } catch { /* cross-origin iframe */ }
  }

  try {
    const el = document.querySelector('.chart-container, .tv-chart')
    if (el && typeof angular !== 'undefined') {
      const widget = angular.element(el).scope?.()?.tvWidget
      const chart = widget?.chart?.()
      if (
        widget && typeof widget.subscribe === 'function' &&
        chart && typeof chart.createShape === 'function'
      ) {
        return widget
      }
    }
  } catch { /* angular not present */ }

  return null
}

/**
 * Retry findTvChart every 800 ms for up to maxMs milliseconds.
 * Resolves with the chart object or null on timeout.
 */
function waitForTvChart(maxMs = 30000) {
  return new Promise((resolve) => {
    const start = Date.now()
    const tick = () => {
      const chart = findTvChart()
      if (chart) {
        resolve(chart)
        return
      }
      if (Date.now() - start >= maxMs) {
        resolve(null)
        return
      }
      setTimeout(tick, 800)
    }
    tick()
  })
}

// Shape colour palette
const PALETTE = {
  LONG:   { arrow: '#26a69a', entry: '#26a69a', sl: '#ef5350', tp: '#26a69a' },
  SHORT:  { arrow: '#ef5350', entry: '#ef5350', sl: '#26a69a', tp: '#ef5350' },
}

const TRADE_ACTION_STYLE = {
  OPEN: {
    textColor: null,
    fontSize: 13,
    longShift: 0.9975,
    shortShift: 1.0025,
  },
  CLOSE: {
    textColor: '#f5c542',
    fontSize: 14,
    longShift: 0.9965,
    shortShift: 1.0035,
  },
}

function _markerGlyph(direction) {
  return direction === 'LONG' ? '▴' : '▾'
}

function _formatMarkerNumber(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '--'
  if (Math.abs(num) >= 1000) return num.toFixed(0)
  if (Math.abs(num) >= 1) return num.toFixed(2).replace(/\.00$/, '').replace(/(\.\d*[1-9])0+$/, '$1')
  return num.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')
}

function _formatMarkerTime(tsStr) {
  if (!tsStr) return '--:--'
  const ms = (/[TZ]/.test(tsStr) || tsStr.includes('+'))
    ? Date.parse(tsStr)
    : Date.parse(tsStr.replace(' ', 'T') + 'Z')
  if (isNaN(ms)) return '--:--'
  try {
    const timeZone = _getLocalTimeZoneLabel()
    return new Intl.DateTimeFormat('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      timeZone,
    }).format(new Date(ms))
  } catch {
    const date = new Date(ms)
    const hh = String(date.getHours()).padStart(2, '0')
    const mm = String(date.getMinutes()).padStart(2, '0')
    return `${hh}:${mm}`
  }
}

function _getMarkerDisplayTime(sig) {
  return _formatMarkerTime(sig?.timestamp)
}

function _getLocalTimeZoneLabel() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'Local'
  } catch {
    return 'Local'
  }
}

/**
 * Read the UI locale stored by the React app (falls back to zh-CN).
 * Runs inside the page context so localStorage is available.
 */
function _getUiLocale() {
  return _uiLocale || 'en'
}

const _TOOLTIP_I18N = {
  'zh-CN': {
    buy:      '买入',
    sell:     '卖出',
    open:     '开仓',
    close:    '平仓',
    qty:      '数量',
    price:    '价格',
    notional: '名义',
    time:     '时间',
  },
  en: {
    buy:      'Buy',
    sell:     'Sell',
    open:     'Open',
    close:    'Close',
    qty:      'Qty',
    price:    'Price',
    notional: 'Notional',
    time:     'Time',
  },
}

function _tooltipT(key) {
  const locale = _getUiLocale()
  const dict = _TOOLTIP_I18N[locale] || _TOOLTIP_I18N['en']
  return dict[key] || _TOOLTIP_I18N['en'][key] || key
}

function _formatMarkerLabel(sig) {
  const action = sig.trade_action === 'CLOSE' ? 'C' : 'O'
  const qty = _formatMarkerNumber(sig.quantity)
  const price = _formatMarkerNumber(sig.entry_price)
  const time = _getMarkerDisplayTime(sig)
  return `${action} ${qty}@${price} ${time}`
}

function _formatMarkerCompactLabel(sig) {
  return sig.trade_action === 'CLOSE' ? 'C' : 'O'
}

const MARKER_FULL_LABEL_LIMIT = 12
const OVERLAY_VISIBLE_RANGE_POLL_MS = 1200
const OVERLAY_MARKER_BASE_OFFSET_RATIO = 0.018
const OVERLAY_MARKER_CLOSE_EXTRA_RATIO = 0.004
const OVERLAY_MARKER_STACK_GAP_RATIO = 0.07

// Keep references so we can clear them before re-drawing
let _drawnShapes    = []    // shape IDs from createShape() (may be falsy)
let _orderLines     = []    // objects from createOrderLine() — need .remove()
let _cachedSignals  = []    // full signal list cached for redraw after detail clear
let _lastOverlayVisibleRangeKey = null
let _overlayShapeSignals = new Map()
let _shapeGeneration = 0   // incremented each redraw; stale promises self-delete
let _overlayTooltipEl = null
let _tvWidget = null
let _overlayDrawingEventHandler = null
let _overlayCrosshairSubscription = null
let _overlayCrosshairHandler = null
let _lastPointerEvent = null
let _overlayPinnedSignal = null

function _escapeOverlayHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function _ensureOverlayTooltipEl() {
  if (_overlayTooltipEl?.isConnected) return _overlayTooltipEl
  const el = document.createElement('div')
  el.id = '__trade_relay_marker_tooltip'
  el.style.cssText = [
    'position:fixed',
    'left:0',
    'top:0',
    'display:none',
    'min-width:160px',
    'max-width:240px',
    'padding:10px 12px',
    'border-radius:10px',
    'border:1px solid rgba(255,255,255,0.12)',
    'background:rgba(12,18,28,0.96)',
    'box-shadow:0 10px 30px rgba(0,0,0,0.35)',
    'color:#f4f7fb',
    'font:12px/1.45 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif',
    'pointer-events:none',
    'z-index:2147483646',
    'white-space:normal',
  ].join(';')
  ;(document.body || document.documentElement).appendChild(el)
  _overlayTooltipEl = el
  return el
}

function _hideOverlayTooltip() {
  if (!_overlayTooltipEl) return
  _overlayTooltipEl.style.display = 'none'
  _overlayPinnedSignal = null
}

function _showOverlayTooltip(sig, pointer) {
  _overlayPinnedSignal = sig
  const el = _ensureOverlayTooltipEl()
  const side = sig.direction === 'LONG' ? _tooltipT('buy') : _tooltipT('sell')
  const action = sig.trade_action === 'CLOSE' ? _tooltipT('close') : _tooltipT('open')
  const color = sig.direction === 'LONG' ? '#26a69a' : '#ef5350'
  const time = _getMarkerDisplayTime(sig)
  const timeZone = _getLocalTimeZoneLabel()
  const notional = (Number.isFinite(sig.quantity) && Number.isFinite(sig.entry_price))
    ? _formatMarkerNumber(sig.quantity * sig.entry_price)
    : '--'
  el.innerHTML = [
    `<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:6px;">`,
    `<strong style="font-size:12px;color:${color};">${_escapeOverlayHtml(side)}</strong>`,
    `<span style="font-size:11px;color:#9db0c7;">${_escapeOverlayHtml(action)}</span>`,
    `</div>`,
    `<div style="display:grid;grid-template-columns:auto 1fr;gap:4px 10px;">`,
    `<span style="color:#8fa3ba;">${_tooltipT('qty')}</span><span>${_escapeOverlayHtml(_formatMarkerNumber(sig.quantity))}</span>`,
    `<span style="color:#8fa3ba;">${_tooltipT('price')}</span><span>${_escapeOverlayHtml(_formatMarkerNumber(sig.entry_price))}</span>`,
    `<span style="color:#8fa3ba;">${_tooltipT('notional')}</span><span>${_escapeOverlayHtml(notional)}</span>`,
    `<span style="color:#8fa3ba;">${_tooltipT('time')}</span><span>${_escapeOverlayHtml(time)} ${_escapeOverlayHtml(timeZone)}</span>`,
    `</div>`,
  ].join('')

  el.style.display = 'block'
  el.style.visibility = 'hidden'

  const margin = 14
  const pointerX = Number.isFinite(pointer?.clientX) ? pointer.clientX : Math.round(window.innerWidth * 0.5)
  const pointerY = Number.isFinite(pointer?.clientY) ? pointer.clientY : Math.round(window.innerHeight * 0.35)
  let left = pointerX + margin
  let top = pointerY + margin
  const width = el.offsetWidth || 200
  const height = el.offsetHeight || 110

  if (left + width > window.innerWidth - 8) left = Math.max(8, pointerX - width - margin)
  if (top + height > window.innerHeight - 8) top = Math.max(8, pointerY - height - margin)

  el.style.left = `${left}px`
  el.style.top = `${top}px`
  el.style.visibility = 'visible'
}

function _trackOverlayPointer(event) {
  _lastPointerEvent = {
    clientX: event.clientX,
    clientY: event.clientY,
  }
}

function _trackOverlayShapeId(idOrPromise, sig) {
  if (!idOrPromise) return
  const gen = _shapeGeneration
  if (typeof idOrPromise?.then === 'function') {
    idOrPromise.then((resolvedId) => {
      if (!resolvedId) return
      if (gen !== _shapeGeneration) {
        // Generation has advanced — this shape belongs to a stale redraw; delete it immediately.
        try { (_tvChart || findTvChart())?.removeEntity(resolvedId) } catch { /* */ }
        return
      }
      _drawnShapes.push(resolvedId)
      _overlayShapeSignals.set(resolvedId, sig)
    }).catch(() => {})
    return
  }
  _drawnShapes.push(idOrPromise)
  _overlayShapeSignals.set(idOrPromise, sig)
}

function _getOverlayVisiblePriceRange() {
  try {
    const chartApi = _tvChart || findTvChart()
    const pane = chartApi?.getPanes?.()?.[0]
    const priceScale = pane?.getMainSourcePriceScale?.()
    const range = priceScale?.getVisiblePriceRange?.()
    if (Number.isFinite(range?.from) && Number.isFinite(range?.to)) return range
  } catch { /* ignore */ }
  return null
}

function _getOverlaySignalDisplayPrice(sig) {
  if (Number.isFinite(sig?._overlayDisplayPrice)) return sig._overlayDisplayPrice
  const dir = sig.direction === 'LONG' ? 'LONG' : 'SHORT'
  const baseLow = Number(sig?.bar_low ?? sig?.entry_price) || 0
  const baseHigh = Number(sig?.bar_high ?? sig?.entry_price) || 0
  return dir === 'LONG' ? baseLow : baseHigh
}

function _getOverlaySignalTimeSec(sig) {
  if (Number.isFinite(sig?._overlayBarTimeSec)) return sig._overlayBarTimeSec
  return _parseTsUtcSec(sig?.timestamp) ?? sig?.bar_index ?? 0
}

function _prepareOverlaySignalLayout() {
  const interval = _getTvCurrentSymbolInterval()?.interval || _lastChartInterval || '1m'
  const intervalMs = Math.max(_parseIntervalMs(interval), 60000)
  const groupCounts = new Map()
  const visiblePriceRange = _getOverlayVisiblePriceRange()
  const priceSpan = Number.isFinite(visiblePriceRange?.to) && Number.isFinite(visiblePriceRange?.from)
    ? Math.abs(visiblePriceRange.to - visiblePriceRange.from)
    : null

  // Build a sorted kline array from the cache so we can binary-search for each
  // signal's bar by time.  This is more reliable than computing barBucket via
  // intervalMs because it does not depend on the TV interval being detected
  // correctly; two trades within the same real K-line always share the same
  // barTimeSec regardless of any interval mismatch.
  let klineSorted = null
  if (_lastChartKlineKey) {
    const cached = _klineCache.get(_lastChartKlineKey)
    if (cached && cached.length > 0) {
      klineSorted = cached.map(b => ({
        openTimeSec: Math.round(b.openTime / 1000),
        low: b.low,
        high: b.high,
      }))
      // Ensure ascending order (cache is normally sorted but be defensive)
      if (klineSorted.length > 1 && klineSorted[0].openTimeSec > klineSorted[klineSorted.length - 1].openTimeSec) {
        klineSorted.sort((a, b) => a.openTimeSec - b.openTimeSec)
      }
    }
  }

  // Binary search: find the last kline bar whose openTimeSec ≤ tradeSec.
  function _findKlineBar(tradeSec) {
    if (!klineSorted || klineSorted.length === 0) return null
    let lo = 0, hi = klineSorted.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (klineSorted[mid].openTimeSec <= tradeSec) lo = mid
      else hi = mid - 1
    }
    return klineSorted[lo].openTimeSec <= tradeSec ? klineSorted[lo] : null
  }

  _cachedSignals.forEach((sig) => {
    const timeSec = Number.isFinite(sig?._overlayBarTimeSec)
      ? sig._overlayBarTimeSec
      : (_parseTsUtcSec(sig.timestamp) ?? sig.bar_index ?? 0)
    const dir = sig.direction === 'LONG' ? 'LONG' : 'SHORT'
    const tradeAction = sig.trade_action === 'CLOSE' ? 'CLOSE' : 'OPEN'

    // Prefer real kline bar open time as the bucket key — this ensures that all
    // trades within the same real candle share the same groupKey even when the
    // detected interval is slightly off.
    const klineBar = _findKlineBar(timeSec)
    const barTimeSec = klineBar
      ? klineBar.openTimeSec
      : Math.floor(timeSec * 1000 / intervalMs) * (intervalMs / 1000)

    const groupKey = `${barTimeSec}:${dir}`
    const stackIndex = groupCounts.get(groupKey) ?? 0
    groupCounts.set(groupKey, stackIndex + 1)

    const entryPrice = Number(sig.entry_price) || 0
    const baseLow = klineBar ? klineBar.low : (Number(sig.bar_low ?? sig.entry_price) || entryPrice)
    const baseHigh = klineBar ? klineBar.high : (Number(sig.bar_high ?? sig.entry_price) || entryPrice)
    const baseOffset = priceSpan != null
      ? Math.max(priceSpan * OVERLAY_MARKER_BASE_OFFSET_RATIO, 0.08)
      : Math.max(Math.abs(entryPrice) * 0.0018, 0.08)
    const closeExtraOffset = tradeAction === 'CLOSE'
      ? (priceSpan != null
          ? Math.max(priceSpan * OVERLAY_MARKER_CLOSE_EXTRA_RATIO, 0.03)
          : Math.max(Math.abs(entryPrice) * 0.0004, 0.03))
      : 0
    const stackGap = priceSpan != null
      ? Math.max(priceSpan * OVERLAY_MARKER_STACK_GAP_RATIO, 0.05)
      : Math.max(Math.abs(entryPrice) * 0.0022, 0.05)
    const totalOffset = baseOffset + closeExtraOffset + stackIndex * stackGap

    sig._overlayStackIndex = stackIndex
    if (!Number.isFinite(sig?._overlayBarTimeSec)) {
      // arrow_up/arrow_down shapes are anchored at their visual center on the time axis;
      // use barTimeSec + half interval so the arrow sits over the candle body center.
      const intervalSec = intervalMs / 1000
      sig._overlayBarTimeSec = barTimeSec + intervalSec * 0.5
    }
    sig._overlayDisplayPrice = dir === 'LONG'
      ? baseLow - totalOffset
      : baseHigh + totalOffset
  })
}

function _getOverlaySignalPointerHit(event) {
  if (!_cachedSignals.length) return null
  const visibleRange = _getOverlayVisibleRange()
  if (!visibleRange) return null
  const x = Number(event?.clientX)
  const y = Number(event?.clientY)
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null
  if (
    x < visibleRange.chartLeft ||
    x > visibleRange.chartRight ||
    y < (visibleRange.chartTop ?? 0) ||
    y > (visibleRange.chartBottom ?? window.innerHeight)
  ) {
    return null
  }

  const chartWidth = Math.max(1, visibleRange.chartRight - visibleRange.chartLeft)
  const hoveredTimeMs = visibleRange.fromMs +
    ((x - visibleRange.chartLeft) / chartWidth) * (visibleRange.toMs - visibleRange.fromMs)
  const timeToleranceMs = Math.max(30_000, ((visibleRange.toMs - visibleRange.fromMs) / chartWidth) * 28)

  const priceRange = _getOverlayVisiblePriceRange()
  let hoveredPrice = null
  let priceTolerance = null
  if (priceRange) {
    const chartHeight = Math.max(1, (visibleRange.chartBottom ?? window.innerHeight) - (visibleRange.chartTop ?? 0))
    const yRatio = (y - (visibleRange.chartTop ?? 0)) / chartHeight
    hoveredPrice = priceRange.to - yRatio * (priceRange.to - priceRange.from)
    priceTolerance = Math.max(Math.abs(priceRange.to - priceRange.from) * 0.08, 0.12)
  }

  let bestSignal = null
  let bestScore = Infinity
  for (const sig of _cachedSignals) {
    const signalTimeSec = _getOverlaySignalTimeSec(sig)
    if (!signalTimeSec) continue
    const timeDeltaMs = Math.abs(signalTimeSec * 1000 - hoveredTimeMs)
    if (timeDeltaMs > timeToleranceMs) continue

    let score = timeDeltaMs / timeToleranceMs
    if (Number.isFinite(hoveredPrice)) {
      const signalPrice = Number(_getOverlaySignalDisplayPrice(sig))
      if (Number.isFinite(signalPrice) && Number.isFinite(priceTolerance)) {
        const priceDelta = Math.abs(signalPrice - hoveredPrice)
        if (priceDelta > priceTolerance && timeDeltaMs > timeToleranceMs * 0.35) continue
        score += priceDelta / priceTolerance
      }
    }

    if (score < bestScore) {
      bestScore = score
      bestSignal = sig
    }
  }

  return bestSignal
}

function _handleOverlayPointerMove(event) {
  _trackOverlayPointer(event)
  if (_overlayPinnedSignal) return
  const sig = _getOverlaySignalPointerHit(event)
  if (!sig) {
    _hideOverlayTooltip()
    return
  }
  _showOverlayTooltip(sig, event)
  _overlayPinnedSignal = null
}

function _handleOverlayPointerClick(event) {
  _trackOverlayPointer(event)
  const sig = _getOverlaySignalPointerHit(event)
  if (!sig) {
    _hideOverlayTooltip()
    return
  }
  _showOverlayTooltip(sig, event)
}

function _unsubscribeOverlayCrosshair() {
  if (!_overlayCrosshairSubscription || !_overlayCrosshairHandler) return
  try {
    _overlayCrosshairSubscription.unsubscribe(null, _overlayCrosshairHandler)
  } catch {
    try {
      _overlayCrosshairSubscription.unsubscribe(_overlayCrosshairHandler)
    } catch { /* ignore */ }
  }
  _overlayCrosshairSubscription = null
  _overlayCrosshairHandler = null
}

function _getOverlayHoverPriceTolerance(crosshairPrice) {
  try {
    const widget = _tvWidget || findTvWidget()
    const chartApi = widget?.activeChart?.() || widget?.chart?.() || _tvChart || findTvChart()
    const pane = chartApi?.getPanes?.()?.[0]
    const priceScale = pane?.getMainSourcePriceScale?.()
    const range = priceScale?.getVisiblePriceRange?.()
    if (Number.isFinite(range?.from) && Number.isFinite(range?.to)) {
      return Math.max(Math.abs(range.to - range.from) * 0.06, Math.abs(Number(crosshairPrice) || 0) * 0.0015, 0.08)
    }
  } catch { /* ignore */ }
  return Math.max(Math.abs(Number(crosshairPrice) || 0) * 0.0025, 0.08)
}

function _findHoveredOverlaySignal(params) {
  if (!_cachedSignals.length || !Number.isFinite(params?.time)) return null
  const visibleRange = _getOverlayVisibleRange()
  if (!visibleRange) return null

  const chartWidth = Math.max(1, visibleRange.chartRight - visibleRange.chartLeft)
  const visibleSpanMs = Math.max(1, visibleRange.toMs - visibleRange.fromMs)
  const timeToleranceMs = Math.max(15_000, (visibleSpanMs / chartWidth) * 14)
  const crosshairTimeMs = Number(params.time) * 1000
  const crosshairPrice = Number(params.price)
  const priceTolerance = Number.isFinite(crosshairPrice)
    ? _getOverlayHoverPriceTolerance(crosshairPrice)
    : null

  let bestSignal = null
  let bestScore = Infinity

  for (const sig of _cachedSignals) {
    const signalTimeSec = _getOverlaySignalTimeSec(sig)
    if (!signalTimeSec) continue
    const timeDeltaMs = Math.abs(signalTimeSec * 1000 - crosshairTimeMs)
    if (timeDeltaMs > timeToleranceMs) continue

    let score = timeDeltaMs / timeToleranceMs
    if (Number.isFinite(crosshairPrice)) {
      const signalPrice = Number(_getOverlaySignalDisplayPrice(sig))
      if (Number.isFinite(signalPrice) && Number.isFinite(priceTolerance)) {
        const priceDelta = Math.abs(signalPrice - crosshairPrice)
        if (priceDelta > priceTolerance && timeDeltaMs > timeToleranceMs * 0.35) continue
        score += priceDelta / priceTolerance
      }
    }

    if (score < bestScore) {
      bestScore = score
      bestSignal = sig
    }
  }

  return bestSignal
}

function _getOverlayTooltipPointerFromCrosshair(params) {
  const range = _getTvVisibleRangeMs()
  return {
    clientX: Number.isFinite(params?.offsetX)
      ? (range?.chartLeft ?? 0) + params.offsetX
      : _lastPointerEvent?.clientX,
    clientY: Number.isFinite(params?.offsetY)
      ? (range?.chartTop ?? 0) + params.offsetY
      : _lastPointerEvent?.clientY,
  }
}

function _handleOverlayCrosshairMoved(params) {
  if (!_cachedSignals.length || !Number.isFinite(params?.time)) {
    _hideOverlayTooltip()
    return
  }
  const sig = _findHoveredOverlaySignal(params)
  if (!sig) {
    _hideOverlayTooltip()
    return
  }
  _showOverlayTooltip(sig, _getOverlayTooltipPointerFromCrosshair(params))
}

document.addEventListener('mousemove', _handleOverlayPointerMove, true)
document.addEventListener('pointerdown', (event) => {
  _trackOverlayPointer(event)
  if (_overlayTooltipEl?.contains(event.target)) return
  if (_getOverlaySignalPointerHit(event)) return
  _hideOverlayTooltip()
}, true)
document.addEventListener('click', _handleOverlayPointerClick, true)
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') _hideOverlayTooltip()
}, true)

function _syncOverlayDrawingEvents() {
  const widget = findTvWidget()
  if (!widget) return
  if (_tvWidget === widget && _overlayDrawingEventHandler && _overlayCrosshairSubscription) return

  if (_tvWidget && _overlayDrawingEventHandler && typeof _tvWidget.unsubscribe === 'function') {
    try {
      _tvWidget.unsubscribe('drawing_event', _overlayDrawingEventHandler)
    } catch { /* stale widget */ }
  }
  _unsubscribeOverlayCrosshair()

  _tvWidget = widget
  _overlayDrawingEventHandler = (sourceId, drawingEventType) => {
    if (drawingEventType !== 'click') return
    const sig = _overlayShapeSignals.get(sourceId)
    if (!sig) return
    _showOverlayTooltip(sig, _lastPointerEvent)
  }

  try {
    widget.subscribe('drawing_event', _overlayDrawingEventHandler)
  } catch {
    _overlayDrawingEventHandler = null
  }

  try {
    const chartApi = widget.activeChart?.() || widget.chart?.()
    const crosshair = chartApi?.crossHairMoved?.()
    if (crosshair?.subscribe) {
      _overlayCrosshairHandler = (params) => {
        _handleOverlayCrosshairMoved(params || {})
      }
      crosshair.subscribe(null, _overlayCrosshairHandler)
      _overlayCrosshairSubscription = crosshair
    }
  } catch {
    _unsubscribeOverlayCrosshair()
  }
}

function _getOverlayVisibleRange() {
  const range = _getTvVisibleRangeMs()
  if (!range || !Number.isFinite(range.fromMs) || !Number.isFinite(range.toMs)) return null
  return range
}

function _getOverlayVisibleRangeKey() {
  const range = _getOverlayVisibleRange()
  if (!range) return null
  return `${Math.round(range.fromMs / 1000)}:${Math.round(range.toMs / 1000)}`
}

function _isSignalInVisibleRange(sig, visibleRange) {
  if (!visibleRange) return false
  const timeSec = _getOverlaySignalTimeSec(sig)
  if (!timeSec) return false
  const timeMs = timeSec * 1000
  return timeMs >= visibleRange.fromMs && timeMs <= visibleRange.toMs
}

// Detail shapes for the currently selected / replayed signal (SL, TP, orderLine)
let _detailShapes     = []
let _detailOrderLines = []

/** Redraw only the entry arrows from the cached signal list.
 * Assumes clearOverlayShapes() has already been called — does NOT
 * delete existing shapes or touch _shapeGeneration.
 */
function _redrawArrows(chart) {
  _prepareOverlaySignalLayout()
  const visibleRange = _getOverlayVisibleRange()
  const fullLabelStartIndex = Math.max(0, _cachedSignals.length - MARKER_FULL_LABEL_LIMIT)

  _cachedSignals.forEach((sig, index) => {
    const dir = sig.direction === 'LONG' ? 'LONG' : 'SHORT'
    const colors = PALETTE[dir]
    const tradeAction = sig.trade_action === 'CLOSE' ? 'CLOSE' : 'OPEN'
    const actionStyle = TRADE_ACTION_STYLE[tradeAction]
    const glyph = _markerGlyph(dir)
    const showLabel = sig.show_label !== false
    const showFullLabel = showLabel && (visibleRange
      ? _isSignalInVisibleRange(sig, visibleRange)
      : index >= fullLabelStartIndex
    )
    const timeSec = _getOverlaySignalTimeSec(sig)
    const priceHint = _getOverlaySignalDisplayPrice(sig)
    const arrowShape = dir === 'LONG' ? 'arrow_up' : 'arrow_down'
    const arrowColor = actionStyle.textColor || colors.arrow
    try {
      const id = chart.createShape(
        { time: timeSec, price: priceHint },
        {
          shape: arrowShape,
          lock: false, disableSelection: false, zOrder: 'top',
          overrides: {
            color: arrowColor,
          },
        }
      )
      _trackOverlayShapeId(id, sig)
    } catch { /* */ }
    // Label text drawn separately so arrow anchor stays precise
    if (showLabel && showFullLabel) {
      const labelText = _formatMarkerLabel(sig)
      try {
        const labelId = chart.createShape(
          { time: timeSec, price: priceHint },
          {
            shape: 'text',
            text: labelText,
            lock: false, disableSelection: false, zOrder: 'top',
            overrides: {
              color: arrowColor,
              fontsize: actionStyle.fontSize - 2,
              bold: true,
              'linetooltext.color': arrowColor,
              'linetooltext.fontsize': actionStyle.fontSize - 2,
              'linetooltext.bold': true,
              'linetooltext.fillBackground': false,
              'linetooltext.drawBorder': false,
              'linetooltext.wordWrap': false,
            },
          }
        )
        _trackOverlayShapeId(labelId, sig)
      } catch { /* */ }
    }
  })
}

function clearDetailShapes(chart) {
  // Remove only the SL/TP detail shapes that were individually tracked.
  // Entry arrows are managed separately and are NOT touched here.
  _detailShapes.forEach(id => { try { chart.removeEntity(id) } catch { /* already gone */ } })
  _detailOrderLines.forEach(ol => { try { ol.remove() } catch {} })
  _detailOrderLines = []
  _detailShapes = []
}

function clearOverlayShapes(chart) {
  _hideOverlayTooltip()

  // Increment generation FIRST so any in-flight createShape() Promises
  // (from the previous draw) self-delete when they resolve instead of
  // re-adding orphaned IDs to _drawnShapes.
  _shapeGeneration++

  // Remove only the shapes that belong to our overlay. Bulk chart-level clears
  // also delete user-authored drawings such as trend lines.
  _detailShapes.forEach(id => { try { chart.removeEntity(id) } catch { /* already gone */ } })
  _detailOrderLines.forEach(ol => { try { ol.remove() } catch { /* */ } })
  _detailOrderLines = []
  _detailShapes = []

  _drawnShapes.forEach(id => { try { chart.removeEntity(id) } catch { /* already gone */ } })
  _drawnShapes = []
  _cachedSignals = []
  _overlayShapeSignals = new Map()
}

function _getOverlayDebugState() {
  return {
    chartFound: Boolean(_tvChart || findTvChart()),
    drawnShapeCount: _drawnShapes.length,
    detailShapeCount: _detailShapes.length,
    detailOrderLineCount: _detailOrderLines.length,
    cachedSignalCount: _cachedSignals.length,
  }
}

/** Parse a bare "YYYY-MM-DD HH:MM:SS" timestamp from the backend as UTC. */
function _parseTsUtcSec(tsStr) {
  if (!tsStr) return null
  const ms = (/[TZ]/.test(tsStr) || tsStr.includes('+'))
    ? Date.parse(tsStr)
    : Date.parse(tsStr.replace(' ', 'T') + 'Z')
  return isNaN(ms) ? null : Math.floor(ms / 1000)
}

/**
 * Draw SL / TP horizontal lines + orderLine for a single signal.
 * Clears any previous detail before drawing.
 */
function drawSignalDetail(chart, sig) {
  clearDetailShapes(chart)

  const dir = sig.direction === 'LONG' ? 'LONG' : 'SHORT'
  const colors = PALETTE[dir]
  const timeSec = _getOverlaySignalTimeSec(sig)

  // SL line
  try {
    const slId = chart.createShape(
      { time: timeSec, price: sig.stop_loss },
      {
        shape: 'horizontal_line',
        lock: true,
        overrides: {
          linecolor: '#ef5350',
          linewidth: 1,
          linestyle: 2,
          showLabel: true,
          text: `SL ${sig.stop_loss}`,
          fontsize: 10,
          textcolor: '#ef5350',
        },
      }
    )
    if (slId) _detailShapes.push(slId)
  } catch { /* */ }

  // TP line
  try {
    const tpId = chart.createShape(
      { time: timeSec, price: sig.take_profit },
      {
        shape: 'horizontal_line',
        lock: true,
        overrides: {
          linecolor: '#26a69a',
          linewidth: 1,
          linestyle: 2,
          showLabel: true,
          text: `TP ${sig.take_profit}`,
          fontsize: 10,
          textcolor: '#26a69a',
        },
      }
    )
    if (tpId) _detailShapes.push(tpId)
  } catch { /* */ }
}

/**
 * Draw entry arrows only for all signals.
 * SL / TP lines are drawn on demand via drawSignalDetail() when a signal is selected.
 */
  _lastOverlayVisibleRangeKey = null

function drawSignalsOnChart(chart, signals) {
  clearOverlayShapes(chart)
  _cachedSignals = signals.slice()  // cache for redraw after detail clear
  _lastOverlayVisibleRangeKey = _getOverlayVisibleRangeKey()
  _redrawArrows(chart)
}

function _refreshOverlayForVisibleRangeChange() {
  if (!_tvChart || !_cachedSignals.length) return
  const nextKey = _getOverlayVisibleRangeKey()
  if (!nextKey || nextKey === _lastOverlayVisibleRangeKey) return
  _lastOverlayVisibleRangeKey = nextKey
  // Only update the key; do NOT redraw here to avoid stacking shapes
  // and clearing user-drawn objects. Labels update on the next 15 s sync.
}
setInterval(() => {
  _refreshOverlayForVisibleRangeChange()
}, OVERLAY_VISIBLE_RANGE_POLL_MS)

// ── IPC: receive signals from main process ──────────────────────────────────
// The React renderer sends signals to main, main forwards here.

let _tvChart = null  // cached chart reference

/**
 * Wait until the TV chart is showing a symbol that contains `expectedPair`.
 * After a pushState symbol switch the chart re-mounts asynchronously; we must
 * not draw signals until it is displaying the correct pair.
 * Resolves when the pair matches OR after maxMs (draws anyway as best-effort).
 */
function waitForChartSymbol(expectedPair, maxMs = 8000) {
  const pair = expectedPair.toUpperCase().endsWith('USDT')
    ? expectedPair.toUpperCase()
    : expectedPair.toUpperCase() + 'USDT'
  return new Promise((resolve) => {
    const start = Date.now()
    const tick = () => {
      // URL path is updated synchronously by pushState — most reliable check
      if (window.location.pathname.toUpperCase().includes('/' + pair)) {
        // Also verify the TV chart itself has switched (chart.symbol contains pair)
        try {
          const c = findTvChart()
          if (c) {
            const sym = (c.symbol() || '').toUpperCase()
            if (sym.includes(pair)) {
              // Critical: also wait for bar data to be fetched for this symbol.
              // After a pushState switch, TV updates chart.symbol() almost immediately
              // but bar data arrives a moment later via REST klines fetch.
              // Drawing shapes before bars load causes them to vanish when bars render.
              // _lastChartKlineKey is reset by switchSymbol and only updated once
              // a large (>=100 bar) REST fetch or live WS kline arrives for this pair.
              const hasBarData = _lastChartKlineKey &&
                _lastChartKlineKey.toUpperCase().startsWith(pair)
              if (hasBarData) { resolve(c); return }
            }
          }
        } catch { /* chart still mounting */ }
      }
      if (Date.now() - start >= maxMs) {
        // Timeout: resolve with whatever chart we can find as best-effort
        resolve(findTvChart())
        return
      }
      setTimeout(tick, 400)
    }
    tick()
  })
}

let _uiLocale = 'en'
let _overlayMessageVersion = 0

ipcRenderer.on('overlay-signals', async (event, signals, locale) => {
  const messageVersion = ++_overlayMessageVersion
  if (locale) _uiLocale = locale
  if (!signals || signals.length === 0) return

  // After a pushState symbol switch, wait for the chart to display the correct
  // symbol before drawing.  Infer the expected pair from the current URL path.
  const pathMatch = window.location.pathname.match(/\/futures\/([A-Z0-9]+)/i)
  const expectedPair = pathMatch ? pathMatch[1].toUpperCase() : null

  if (expectedPair) {
    // waitForChartSymbol probes the TV chart symbol — resolves once it matches
    const matchedChart = await waitForChartSymbol(expectedPair, 8000)
    if (matchedChart) {
      // Chart is confirmed on the right symbol — update cached ref
      _tvChart = matchedChart
    } else {
      // Timeout: chart symbol didn't match in time; fall back to basic probe
      _tvChart = await waitForTvChart(6000)
    }
  } else if (!_tvChart) {
    _tvChart = await waitForTvChart(15000)
  }

  if (messageVersion !== _overlayMessageVersion) {
    return
  }

  if (!_tvChart) {
    ipcRenderer.send('overlay-status', { ok: false, reason: 'tv_chart_not_found' })
    return
  }

  try {
    _syncOverlayDrawingEvents()
    if (messageVersion !== _overlayMessageVersion) {
      return
    }
    drawSignalsOnChart(_tvChart, signals)
    ipcRenderer.send('overlay-status', { ok: true, count: signals.length })
  } catch (err) {
    // Chart reference may have gone stale (e.g. symbol change); reset and retry once
    _tvChart = null
    const fresh = await waitForTvChart(8000)
    if (messageVersion !== _overlayMessageVersion) {
      return
    }
    if (fresh) {
      _tvChart = fresh
      _syncOverlayDrawingEvents()
      if (messageVersion !== _overlayMessageVersion) {
        return
      }
      drawSignalsOnChart(fresh, signals)
      ipcRenderer.send('overlay-status', { ok: true, count: signals.length, retried: true })
    } else {
      ipcRenderer.send('overlay-status', { ok: false, reason: 'stale_chart' })
    }
  }
})

// IPC: clear all drawn shapes
ipcRenderer.on('overlay-clear', async () => {
  _overlayMessageVersion += 1
  const chart = _tvChart || findTvChart()
  if (chart) {
    clearOverlayShapes(chart)
  }
})

ipcRenderer.on('overlay-clear-debug', async () => {
  _overlayMessageVersion += 1
  const before = _getOverlayDebugState()
  const chart = _tvChart || findTvChart()
  if (chart) {
    clearOverlayShapes(chart)
  }
  const after = _getOverlayDebugState()
  ipcRenderer.send('overlay-status', {
    action: 'clear-debug',
    ok: Boolean(chart),
    reason: chart ? 'cleared' : 'tv_chart_not_found',
    before,
    after,
  })
})

// IPC: show SL/TP detail for a clicked/replayed signal
ipcRenderer.on('overlay-signal-detail', (event, sig) => {
  const chart = _tvChart || findTvChart()
  if (chart && sig) drawSignalDetail(chart, sig)
})

// IPC: probe chart and report back (useful for debugging from React DevTools)
ipcRenderer.on('overlay-probe', async () => {
  const chart = findTvChart()
  ipcRenderer.send('overlay-status', {
    action: 'probe',
    ok: !!chart,
    reason: chart ? 'chart_found' : 'not_found',
    state: _getOverlayDebugState(),
  })
})

// IPC: scroll the chart viewport to centre on a specific timestamp (used by replay)
ipcRenderer.on('overlay-scroll-to', (event, timeSec, intervalSec) => {
  const chart = _tvChart || findTvChart()
  if (!chart) return
  try {
    const span = (intervalSec || 300) * 60  // show ~60 bars around target
    chart.setVisibleRange({
      from: timeSec - Math.floor(span * 0.67),
      to:   timeSec + Math.floor(span * 0.33),
    })
  } catch { /* setVisibleRange may be unavailable on this TV build */ }
})

// ── K-line range selection overlay ───────────────────────────────────────
// When the renderer activates K-line selection mode, we inject a full-page
// transparent overlay into the BrowserView so the user can drag-select a
// horizontal time range on the TradingView chart.  On mouseup we:
//   1. Convert the pixel x-positions to timestamps using TradingView's
//      getVisibleRange() API (falls back to kline cache boundaries).
//   2. Filter the kline cache to just the selected bars.
//   3. Send the bars back to the main process → renderer via IPC.

ipcRenderer.on('start-kline-selection', (event, opts) => {
  _startKlineSelection(opts || {})
})

function _startKlineSelection(opts) {
  const existing = document.getElementById('__omni_kline_sel')
  if (existing) { existing.remove(); return }

  const overlay = document.createElement('div')
  overlay.id = '__omni_kline_sel'
  overlay.style.cssText = [
    'position:fixed', 'inset:0', 'z-index:2147483647', 'cursor:crosshair',
  ].join(';')

  // Instruction hint bar
  const hint = document.createElement('div')
  hint.style.cssText = [
    'position:absolute', 'top:14px', 'left:50%', 'transform:translateX(-50%)',
    'background:rgba(10,10,10,0.88)', 'color:#4ec9b0',
    'padding:7px 18px', 'border-radius:20px',
    'font:13px/1.4 -apple-system,sans-serif',
    'pointer-events:none', 'white-space:nowrap',
    'border:1.5px solid #4ec9b0',
    'box-shadow:0 2px 16px rgba(0,0,0,0.5)',
  ].join(';')
  hint.textContent = opts.hint || '拖拽选取K线区间 · Esc 取消'
  overlay.appendChild(hint)

  // Selection band (full height, vertical boundaries only)
  const band = document.createElement('div')
  band.style.cssText = [
    'position:absolute', 'top:0', 'bottom:0',
    'border-left:2px solid #4ec9b0', 'border-right:2px solid #4ec9b0',
    'background:rgba(78,201,176,0.13)', 'display:none', 'pointer-events:none',
  ].join(';')
  overlay.appendChild(band)

  // Bar-count badge shown above the right edge of selection
  const badge = document.createElement('div')
  badge.style.cssText = [
    'position:absolute', 'top:44px',
    'background:#4ec9b0', 'color:#000',
    'padding:2px 8px', 'border-radius:10px',
    'font:bold 11px monospace', 'pointer-events:none', 'display:none',
  ].join(';')
  overlay.appendChild(badge)

  let startX = 0, dragging = false

  function cleanup(result) {
    overlay.remove()
    document.removeEventListener('keydown', onKey)
    ipcRenderer.send('kline-selection-result', result)
  }

  overlay.addEventListener('mousedown', e => {
    startX = e.clientX
    dragging = true
    band.style.display = 'block'
    band.style.left = startX + 'px'
    band.style.width = '0'
    badge.style.display = 'none'
    e.preventDefault()
  })

  overlay.addEventListener('mousemove', e => {
    if (!dragging) return
    const x = Math.min(e.clientX, startX)
    const w = Math.abs(e.clientX - startX)
    band.style.left = x + 'px'
    band.style.width = w + 'px'
    // Estimate bars selected (rough: assume ~1 bar per 12 px)
    const est = Math.max(1, Math.round(w / 12))
    badge.textContent = `~${est} bars`
    badge.style.left = (x + w + 4) + 'px'
    badge.style.display = 'block'
  })

  overlay.addEventListener('mouseup', e => {
    if (!dragging) return
    dragging = false

    const x1 = Math.min(e.clientX, startX)
    const x2 = Math.max(e.clientX, startX)
    if (x2 - x1 < 5) { cleanup(null); return }

    const x1Pct = x1 / window.innerWidth
    const x2Pct = x2 / window.innerWidth

    // ── Resolve active symbol + interval ─────────────────────────────────
    // Priority order (most → least reliable):
    //  1. TV widget chart.symbol() / chart.resolution()  (when TV API is accessible)
    //  2. _lastChartKlineKey — the symbol_interval of the most recent large
    //     kline REST fetch made by the chart itself (updated every time the user
    //     switches symbol or interval)
    //  3. Cache entry with the most recent openTime (least reliable — BTCUSDT
    //     wins here because it has a live WS stream, so only use as last resort)
    const tvSym = _getTvCurrentSymbolInterval()
    let activeKey = null, allBars = null

    // 1. TV widget
    if (tvSym?.symbol && tvSym?.interval) {
      const k = `${tvSym.symbol}_${tvSym.interval}`
      const cached = _klineCache.get(k)
      if (cached && cached.length > 0) { activeKey = k; allBars = cached }
    }

    // 2. Last chart fetch key
    if (!allBars && _lastChartKlineKey) {
      const cached = _klineCache.get(_lastChartKlineKey)
      if (cached && cached.length > 0) { activeKey = _lastChartKlineKey; allBars = cached }
    }

    // 3. Fallback: most-recently-fetched entry (not highest live WS timestamp)
    if (!allBars) {
      let bestFetchTime = -1
      // _klineCache doesn't store fetch time, so use openTime of the SECOND-TO-LAST
      // bar as a tiebreak (less affected by live WS updates than the very last bar)
      for (const [k, v] of _klineCache.entries()) {
        if (!v || v.length < 2) continue
        const t = v[v.length - 2].openTime   // penultimate bar — more stable
        if (t > bestFetchTime) { bestFetchTime = t; activeKey = k; allBars = v }
      }
    }

    const activeSymbol   = tvSym?.symbol   ?? (activeKey ? activeKey.split('_')[0] : null)
    const activeInterval = tvSym?.interval ?? (activeKey ? activeKey.split('_').slice(1).join('_') : null)

    // ── Convert pixel range → timestamps ─────────────────────────────────
    // Method 1: TradingView widget getVisibleRange() + chart element pixel bounds
    let startMs = null, endMs = null
    try {
      const range = _getTvVisibleRangeMs()
      if (range) {
        const { fromMs, toMs, chartLeft, chartRight } = range
        const span = toMs - fromMs
        startMs = Math.round(fromMs + ((x1 - chartLeft) / (chartRight - chartLeft)) * span)
        endMs   = Math.round(fromMs + ((x2 - chartLeft) / (chartRight - chartLeft)) * span)
      }
    } catch { /* ignore */ }

    // Method 2: interpolate from the kline cache's own time extent
    if ((startMs === null) && allBars && allBars.length >= 2) {
      const cacheFrom = allBars[0].openTime
      const cacheTo   = allBars[allBars.length - 1].openTime
      const span = cacheTo - cacheFrom
      startMs = Math.round(cacheFrom + x1Pct * span)
      endMs   = Math.round(cacheFrom + x2Pct * span)
    }

    // ── Filter bars to the selected time window ────────────────────────────
    let selectedBars = null
    if (allBars && allBars.length > 0 && startMs !== null && endMs !== null) {
      selectedBars = allBars.filter(b => b.openTime >= startMs && b.openTime <= endMs)
      if (selectedBars.length === 0) {
        // Edge: visible range ends before cache, expand window slightly
        const margin = (endMs - startMs) * 0.1
        selectedBars = allBars.filter(b => b.openTime >= startMs - margin && b.openTime <= endMs + margin)
      }
    }

    cleanup({
      startMs, endMs, x1Pct, x2Pct,
      symbol:       activeSymbol,
      interval:     activeInterval,
      selectedBars: selectedBars && selectedBars.length > 0 ? selectedBars : null,
    })
  })

  function onKey(e) { if (e.key === 'Escape') cleanup(null) }
  document.addEventListener('keydown', onKey)
  document.body.appendChild(overlay)
}

/**
 * Try to read the symbol and interval currently displayed on the TradingView
 * chart.  Returns { symbol, interval } or null.
 * Uses the comprehensive findTvChart() helper so all Binance TV build variants work.
 * symbol is like "CYSUSDT", interval is a Binance interval string like "15m".
 */
function _getTvCurrentSymbolInterval() {
  try {
    const chart = findTvChart()
    if (!chart) return null
    const sym = typeof chart.symbol === 'function' ? chart.symbol() : null
    const res = typeof chart.resolution === 'function' ? chart.resolution() : null
    if (!sym) return null
    const interval = res ? _tvResolutionToBinance(res) : null
    // Normalise:
    //  1. Remove exchange prefix  "BINANCE:CYSUSDT"        → "CYSUSDT"
    //  2. Remove TV price-type    "CYSUSDT@PRICETYPE=LAST" → "CYSUSDT"
    let cleanSym = sym.includes(':') ? sym.split(':')[1] : sym
    cleanSym = cleanSym.split('@')[0].toUpperCase()
    return { symbol: cleanSym, interval }
  } catch { return null }
}

/**
 * Try to read TradingView's currently visible time range and the chart area
 * pixel boundaries.  Returns { fromMs, toMs, chartLeft, chartRight } or null.
 * Uses the comprehensive findTvChart() helper so all Binance TV build variants work.
 */
function _getTvVisibleRangeMs() {
  try {
    const chart = findTvChart()
    if (!chart) return null
    const range = chart.getVisibleRange?.()
    if (!range || !range.from) return null
    // Find chart container element for pixel bounds
    const containers = [
      '#trading_chart_area', '.chart-container-border',
      '.chart-widget', '#chart-container', '.layout__area--center',
      '[data-tv-widget]', '[data-testid="chart-container"]',
    ]
    for (const sel of containers) {
      const el = document.querySelector(sel)
      if (!el) continue
      const b = el.getBoundingClientRect()
      if (b.width < 100) continue
      return {
        fromMs: range.from * 1000,
        toMs:   range.to   * 1000,
        chartLeft:  b.left,
        chartRight: b.right,
        chartTop: b.top,
        chartBottom: b.bottom,
      }
    }
    // No container found — use viewport width as fallback
    return {
      fromMs: range.from * 1000,
      toMs:   range.to   * 1000,
      chartLeft: 0,
      chartRight: window.innerWidth,
      chartTop: 0,
      chartBottom: window.innerHeight,
    }
  } catch { return null }
}

// ── TV chart expand/collapse button relay ─────────────────────────────────
// Triggered by our own TitleBar button in the React UI.  We try several
// known TradingView toolbar selectors in priority order, then fall back to
// calling requestFullscreen() on the chart root element (which is intercepted
// by our fake implementation above so the BrowserView bounds stay fixed).
ipcRenderer.on('chart-toggle-fullscreen', () => {
  const selectors = [
    '[data-name="header-toolbar-fullscreen"]',          // TV standalone
    'button[aria-label*="ullscreen" i]',                // aria label variant
    'button[aria-label*="xpand" i]',
    'button[title*="ullscreen" i]',
    'button[title*="xpand" i]',
    '.chart-toolbar button:last-of-type',               // last toolbar button
  ]
  for (const sel of selectors) {
    const btn = document.querySelector(sel)
    if (btn) {
      console.log('[OmniTrader] chart-toggle-fullscreen: clicking', sel)
      btn.click()
      return
    }
  }
  // Final fallback: directly invoke requestFullscreen on chart container
  // (hits our interceptor, so no OS-level fullscreen side effect)
  const container =
    document.querySelector('.chart-container') ||
    document.querySelector('#chart-container') ||
    document.querySelector('[class*="chart-container"]') ||
    document.documentElement
  console.log('[OmniTrader] chart-toggle-fullscreen: fallback requestFullscreen on', container.tagName)
  container.requestFullscreen?.()
})

// ─────────────────────────────────────────────────────────────────────────────

// ─────────────────────────────────────────────────────────────────────────────
// EMA values — dual-mode:
//   1. _tryReadTvStudyEmas() : reads values directly from TV's rendered chart
//      studies via internal model probing — exact match with chart display.
//   2. _computeEmaJs() fallback : computes from accumulated kline cache using
//      TV-compatible SMA-seed EWM algorithm.
// ─────────────────────────────────────────────────────────────────────────────

function _computeEmaJs(closes, period) {
  const result = new Array(closes.length).fill(null)
  if (closes.length < period) return result
  // Seed: SMA of first `period` bars (TV-compatible initialisation)
  let ema = 0
  for (let i = 0; i < period; i++) ema += closes[i]
  ema /= period
  result[period - 1] = ema
  const k = 2 / (period + 1)
  for (let i = period; i < closes.length; i++) {
    ema = closes[i] * k + ema * (1 - k)
    result[i] = ema
  }
  return result
}

/**
 * Best-effort: read EMA values directly from TradingView's rendered chart studies.
 * Probes TV Charting Library's internal pane model datasources.
 * Returns [{openTime, ema7, ema20, ...}] aligned to klines, or null on failure.
 */
function _tryReadTvStudyEmas(symbol, interval, limit, periods) {
  try {
    const chart = _tvChart
    if (!chart || typeof chart.getAllStudies !== 'function') return null
    const allStudies = chart.getAllStudies()
    if (!Array.isArray(allStudies) || !allStudies.length) return null

    // Match EMA / Moving Average Exponential studies
    const emaStudies = allStudies.filter(s => /moving.?average.?exp|\bema\b/i.test(s.name || ''))
    if (!emaStudies.length) return null

    // Probe chart object's properties to locate internal pane datasources.
    // TV Charting Library v20+ stores study results in pane model datasources.
    const _tryGetSources = (obj) => {
      if (!obj || typeof obj !== 'object') return null
      try {
        if (typeof obj.paneModels === 'function') {
          const panes = obj.paneModels()
          if (Array.isArray(panes) && panes[0]) {
            const ds = typeof panes[0].dataSources === 'function'
              ? panes[0].dataSources() : panes[0]._dataSources
            if (Array.isArray(ds) && ds.length) return ds
          }
        }
        if (Array.isArray(obj._paneModels) && obj._paneModels[0]) {
          const ds = typeof obj._paneModels[0].dataSources === 'function'
            ? obj._paneModels[0].dataSources() : obj._paneModels[0]._dataSources
          if (Array.isArray(ds) && ds.length) return ds
        }
      } catch {}
      return null
    }

    let sources = null
    for (const k of Object.keys(chart)) {
      try {
        const v = chart[k]
        if (!v || typeof v !== 'object') continue
        sources = _tryGetSources(v)
          || _tryGetSources(v._model)
          || (typeof v.model === 'function' ? _tryGetSources(v.model()) : null)
        if (sources) break
      } catch {}
    }
    if (!sources) return null

    // For each EMA study, locate its datasource and extract the values series.
    const emaData = new Map() // period -> number[]
    for (const study of emaStudies) {
      const src = sources.find(s => {
        try {
          const id = typeof s.id === 'function' ? s.id() : (s._id ?? s.id)
          return String(id) === String(study.id)
        } catch { return false }
      })
      if (!src) continue

      // Determine EMA period from study inputs
      let period = null
      try {
        const props = typeof src.properties === 'function' ? src.properties() : src._properties
        if (props?.inputs) {
          const first = Object.values(props.inputs)[0]
          period = Number(first?.defval ?? first?.value)
        }
      } catch {}
      if (!period || isNaN(period)) {
        const m = study.name.match(/\d+/)
        if (m) period = parseInt(m[0])
      }
      if (!period || !periods.includes(period)) continue

      // Extract values — TV stores study data in several known internal formats
      try {
        const d = typeof src.data === 'function' ? src.data() : src._data
        const vals =
          d?.m_data?.values?.[1] ||  // {m_data:{values:[times[],data[]]}} format
          (Array.isArray(d?.bars) && d.bars.map(b => typeof b === 'number' ? b : (b?.[1] ?? b?.value))) ||
          (Array.isArray(d?.values) && typeof d.values[0] !== 'object' ? d.values : null)
        if (vals?.length) emaData.set(period, vals)
      } catch {}
    }
    if (!emaData.size) return null

    // Align TV study series with cached klines (latest N bars, same underlying data)
    const klines = _getCachedKlines(symbol, interval, limit || 1500)
    if (!klines?.length) return null
    const sliced = klines.slice(-limit)
    const result = sliced.map((bar, i) => {
      const row = { openTime: bar.openTime }
      for (const [p, vals] of emaData) {
        const off = vals.length - sliced.length
        const idx = off + i
        row[`ema${p}`] = (idx >= 0 && idx < vals.length && vals[idx] != null)
          ? Number(vals[idx]) : null
      }
      return row
    })
    if (result.some(r => periods.some(p => r[`ema${p}`] != null))) return result
    return null
  } catch {
    return null
  }
}

/** Convert an interval string ("1m","15m","1h","4h","1d") to milliseconds. */
function _parseIntervalMs(interval) {
  const m = interval.match(/^(\d+)([mhd])$/)
  if (!m) return 60000
  const n = parseInt(m[1])
  switch (m[2]) {
    case 'm': return n * 60000
    case 'h': return n * 3600000
    case 'd': return n * 86400000
    default:  return 60000
  }
}

/**
 * Fetch the latest 500 bars from Binance Futures REST and merge them into
 * _klineCache.  Only runs when the most-recent cached bar is more than
 * 2 intervals old — i.e. when the TradingView UDF feed has stopped updating
 * the cache (TV uses its own proprietary WebSocket, not Binance's @kline_Xm
 * stream, so the WebSocket interceptor alone cannot keep the cache fresh).
 *
 * Uses _originalFetch to bypass our own fetch override and avoid recursion.
 */
async function _refreshKlineCacheIfStale(symbol, interval) {
  const sym = symbol.toUpperCase().endsWith('USDT')
    ? symbol.toUpperCase() : symbol.toUpperCase() + 'USDT'
  const key = `${sym}_${interval}`
  const existing = _klineCache.get(key)
  const intervalMs = _parseIntervalMs(interval)

  // Skip if cache is fresh (last bar < 2 intervals ago)
  if (existing && existing.length > 0) {
    const lastTs = existing[existing.length - 1].openTime
    if (Date.now() - lastTs < intervalMs * 2) return
  }

  console.log(`[OmniTrader EMA] cache stale for ${sym}_${interval} — refreshing via Binance REST`)
  try {
    // continuousKlines works for all perpetual futures symbols
    const url =
      `https://fapi.binance.com/fapi/v1/continuousKlines` +
      `?pair=${sym}&contractType=PERPETUAL&interval=${interval}&limit=500`
    const resp = await _originalFetch(url)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    if (!Array.isArray(data) || !Array.isArray(data[0])) return
    const parsed = data.map(b => ({
      openTime:  Number(b[0]),
      open:      parseFloat(b[1]),
      high:      parseFloat(b[2]),
      low:       parseFloat(b[3]),
      close:     parseFloat(b[4]),
      volume:    parseFloat(b[5]),
      closeTime: Number(b[6]),
      isClosed:  true,
    }))
    if (existing && existing.length > 0) {
      const byTime = new Map(existing.map(b => [b.openTime, b]))
      for (const b of parsed) byTime.set(b.openTime, b)
      const merged = Array.from(byTime.values()).sort((a, b) => a.openTime - b.openTime)
      _klineCache.set(key, merged.length > 3000 ? merged.slice(-3000) : merged)
    } else {
      _klineCache.set(key, parsed)
    }
    const updated = _klineCache.get(key)
    const newLast = updated ? new Date(updated[updated.length - 1].openTime).toISOString() : 'n/a'
    console.log(`[OmniTrader EMA] cache refreshed  ${sym}_${interval}  total=${updated?.length}  last_bar=${newLast}`)
  } catch (e) {
    console.warn(`[OmniTrader EMA] cache refresh failed for ${sym}_${interval}:`, e.message)
  }
}

/**
 * Return EMA values for the given symbol/interval.
 * Priority 1: live TV study data (exact match with chart display).
 * Priority 2: computed from kline cache, refreshed from Binance REST if stale.
 *
 * This function is async — Electron's executeJavaScript automatically awaits
 * the returned Promise, so the IPC caller receives the resolved array.
 *
 * @param {string}   symbol   e.g. "BTCUSDT"
 * @param {string}   interval e.g. "15m"
 * @param {number}   limit    max bars to include
 * @param {number[]} periods  EMA periods (default [7,20,60,100,200])
 */
async function _getCachedEmas(symbol, interval, limit, periods) {
  const perList = periods || [7, 20, 60, 100, 200]
  const lim = limit || 1500

  // Priority 1: live TV study values (synchronous probe)
  const tvResult = _tryReadTvStudyEmas(symbol, interval, lim, perList)
  if (tvResult) {
    const last = tvResult[tvResult.length - 1]
    const lastTime = last ? new Date(last.openTime).toISOString() : 'n/a'
    console.log(
      `[OmniTrader EMA] source=TV_STUDY  symbol=${symbol} interval=${interval}` +
      ` rows=${tvResult.length}  last_bar=${lastTime}` +
      ` ema7=${last?.ema7?.toFixed(8)}  ema20=${last?.ema20?.toFixed(8)}` +
      ` ema60=${last?.ema60?.toFixed(8)}  ema100=${last?.ema100?.toFixed(8)}` +
      ` ema200=${last?.ema200?.toFixed(8)}`
    )
    return tvResult
  }

  // Priority 2: compute from kline cache — but first refresh if stale.
  // TradingView uses its own UDF WebSocket (not Binance's @kline_Xm stream),
  // so the WebSocket interceptor alone cannot keep the cache current.
  // A direct REST call here guarantees the last bar is at most 1 interval old.
  await _refreshKlineCacheIfStale(symbol, interval)

  // Always compute from the full cache so EMA200 has enough warmup history.
  const maxPeriod = Math.max(...perList)
  const warmup = maxPeriod * 5   // 5× longest period → < 0.001% seed error
  const fetchLim = Math.max(lim + warmup, 3000)
  const allBars = _getCachedKlines(symbol, interval, fetchLim)
  if (!allBars || allBars.length === 0) {
    console.warn(`[OmniTrader EMA] source=MISS  symbol=${symbol} interval=${interval}  no kline cache`)
    return null
  }
  const closes = allBars.map(b => b.close)
  const emaValues = {}
  for (const p of perList) {
    emaValues[p] = _computeEmaJs(closes, p)
  }
  const allResult = allBars.map((bar, i) => {
    const row = { openTime: bar.openTime }
    for (const p of perList) {
      row[`ema${p}`] = emaValues[p][i]
    }
    return row
  })
  // Slice to the latest `lim` rows (fully-converged values)
  const result = allResult.slice(-lim)
  const last2 = result[result.length - 1]
  const lastTime2 = last2 ? new Date(last2.openTime).toISOString() : 'n/a'
  console.log(
    `[OmniTrader EMA] source=COMPUTED  symbol=${symbol} interval=${interval}` +
    ` all_bars=${allBars.length}  warmup=${warmup}  rows_returned=${result.length}  last_bar=${lastTime2}` +
    ` ema7=${last2?.ema7?.toFixed(8)}  ema20=${last2?.ema20?.toFixed(8)}` +
    ` ema200=${last2?.ema200?.toFixed(8)}`
  )
  return result
}

/**
 * Async wrapper around _getCachedKlines.
 * Refreshes the kline cache from Binance REST first (if stale) so that
 * both getTvKlines and getTvEmas IPC calls always work on the same
 * up-to-date dataset.  executeJavaScript in Electron automatically awaits
 * the returned Promise.
 */
async function _getCachedKlinesWithRefresh(symbol, interval, limit) {
  await _refreshKlineCacheIfStale(symbol, interval)
  return _getCachedKlines(symbol, interval, limit)
}

// ─────────────────────────────────────────────────────────────────────────────
// Primary interval detection: poll chart.resolution() via the TV chart object.
// This is authoritative — it reads directly from the chart's internal state,
// so it correctly reflects what the user sees regardless of how many kline
// streams Binance has open (chart + indicators may all have different intervals).
// Fires as soon as waitForTvChart() resolves, then checks every 1.5 s.
// The WebSocket SUBSCRIBE path above provides fast-path coverage during the
// window before the TV chart object becomes available.
// ─────────────────────────────────────────────────────────────────────────────
;(async () => {
  const chart = await waitForTvChart(30000)
  if (!chart) return
  // Seed the shared chart reference used by the overlay engine
  if (!_tvChart) _tvChart = chart

  let _lastPolledInterval = null
  const _pollResolution = () => {
    try {
      const res = chart.resolution()
      const iv = _tvResolutionToBinance(res)
      if (iv && iv !== _lastPolledInterval) {
        _lastPolledInterval = iv
        _lastChartInterval = iv   // keep SUBSCRIBE tracker in sync
        ipcRenderer.send('chart-interval-change', iv)
      }
    } catch { /* chart ref may be temporarily stale — keep polling */ }
  }

  _pollResolution()           // fire immediately on chart ready
  setInterval(_pollResolution, 1500)
})()

// Expose a minimal debug API to console (accessible since contextIsolation is off)
// Also expose a __tradeRelayDebug object so executeJavaScript can call clearAll() directly
window.__tradeRelayDebug = {
  clearAll: () => {
    const results = []
    const chart = _tvChart || findTvChart()
    if (!chart) return 'no_chart'

    // Try removeAllShapes on chart API object
    try { chart.removeAllShapes(); results.push('chart.removeAllShapes:ok') }
    catch (e) { results.push('chart.removeAllShapes:err:' + e.message) }

    // Try getAllShapes + removeEntity
    try {
      const all = chart.getAllShapes?.()
      if (Array.isArray(all) && all.length > 0) {
        all.forEach(s => { try { chart.removeEntity(s.id) } catch {} })
        results.push('getAllShapes+removeEntity:ok:' + all.length)
      } else {
        results.push('getAllShapes:empty_or_unavail')
      }
    } catch (e) { results.push('getAllShapes:err:' + e.message) }

    // clearOverlayShapes from preload
    try { clearOverlayShapes(chart); results.push('clearOverlayShapes:ok') }
    catch (e) { results.push('clearOverlayShapes:err:' + e.message) }

    // Try widget-level removeAllShapes (widget, not chart API)
    const directNames = ['tvWidget','tv_chart_widget','tvChartWidget','chartWidget','tv','TV']
    for (const name of directNames) {
      try {
        const w = window[name]
        if (!w) continue
        if (typeof w.removeAllShapes === 'function') { w.removeAllShapes(); results.push(name+'.removeAllShapes:ok') }
        const ac = w.activeChart?.() || w.chart?.()
        if (ac && typeof ac.removeAllShapes === 'function') { ac.removeAllShapes(); results.push(name+'.activeChart.removeAllShapes:ok') }
      } catch (e) { results.push(name+':err:' + e.message) }
    }

    return results.join(' | ')
  },
}

window.__omnitrader = {
  version: '0.1.0',
  status: 'intercepting',
  findTvChart,
  clearOverlay: () => { if (_tvChart) clearOverlayShapes(_tvChart) },
  getCachedKlines: _getCachedKlinesWithRefresh,   // async — refreshes stale cache first
  getCachedEmas: _getCachedEmas,                   // async — also refreshes stale cache
  listKlineCache: () => Array.from(_klineCache.keys()),
  getLastChartKey: () => _lastChartKlineKey,
  // Used by kline-selection fallback: return most recent bars across ALL cached keys
  _getRawCache: () => {
    let best = null
    for (const bars of _klineCache.values()) {
      if (!best || bars.length > best.length) best = bars
    }
    return best || []
  },
  /**
   * Switch the Binance futures chart to a new symbol WITHOUT a full page reload.
   * Uses Binance's React SPA router: pushState the new /futures/<SYMBOL> path and
   * dispatch 'popstate' so the router loads the new pair in-place.
   * Chart stays mounted and maximized. Returns true on success, false if the
   * current path is not a /futures/ page (caller falls back to full reload).
   */
  switchSymbol: function(newBaseSymbol) {
    const newPair = newBaseSymbol.toUpperCase().endsWith('USDT')
      ? newBaseSymbol.toUpperCase()
      : newBaseSymbol.toUpperCase() + 'USDT'
    try {
      const cur = window.location.pathname   // e.g. /zh-CN/futures/BTCUSDT
      const newPath = cur.replace(/\/[A-Z0-9]+$/, '/' + newPair)
      if (!newPath.includes('/futures/') || newPath === cur) return false
      // Invalidate cached chart ref — Binance will remount the TV chart for the
      // new pair; overlay-signals must wait for the new chart, not draw on the old one.
      _tvChart = null
      // Reset the kline-key tracker so waitForChartSymbol can confirm bar data
      // for the new symbol has been fetched before drawing any shapes.
      _lastChartKlineKey = null
      _ensureTicker24hStream(newPair)
      history.pushState({}, '', newPath)
      window.dispatchEvent(new PopStateEvent('popstate', { state: {} }))
      console.log('[OmniTrader] switchSymbol:', cur, '→', newPath)
      return true
    } catch (e) {
      console.warn('[OmniTrader] switchSymbol failed:', e)
      return false
    }
  },
}

// Remove ipcRenderer from window so Binance page JS cannot reach it.
// With contextIsolation: false the preload shares page's window, so we clean up
// the require() result immediately after our listeners are registered.
delete window.require
