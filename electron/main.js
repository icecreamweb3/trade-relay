/**
 * Trade Relay — Electron Main Process (Electron + React + BrowserView architecture)
 * Replaced Qt6 + TCP NDJSON bridge with standard Electron IPC.
 */
const { app, BrowserWindow, BrowserView, ipcMain, safeStorage, screen, shell, dialog } = require('electron')
const fs = require('fs')
const path = require('path')
const { execFile, exec } = require('child_process')
const http = require('http')
const https = require('https')
const crypto = require('crypto')
const { ProxyAgent } = require('proxy-agent')
const {
  applyQuickOrderPreference,
  buildQuickLimit,
  buildQuickStop,
  normalizeQuickOrderPreference,
  resolveBaseQuantity,
  splitTradeSymbol,
} = require('./chart-quick-orders')
function resolveEnvPath() {
  const candidates = [
    path.join(path.dirname(process.execPath), '.env.production'),
    path.join(path.dirname(process.execPath), '.env'),
    path.join(process.cwd(), '.env.production'),
    path.join(process.cwd(), '.env'),
    path.join(process.resourcesPath || '', '.env.production'),
    path.join(process.resourcesPath || '', '.env'),
    path.join(__dirname, '../.env.production'),
    path.join(__dirname, '../.env'),
  ].filter(Boolean)

  const seen = new Set()
  for (const candidate of candidates) {
    const normalized = path.resolve(candidate)
    if (seen.has(normalized)) continue
    seen.add(normalized)
    if (fs.existsSync(normalized)) return normalized
  }
  return null
}

const envPath = resolveEnvPath()
require('dotenv').config(envPath ? { path: envPath } : {})
const { logger } = require('./logger')

if (envPath) {
  logger.info('Loaded Electron env file', { envPath })
} else {
  logger.warn('No .env.production/.env file found for Electron runtime; using process env/defaults')
}

const isDev = process.env.NODE_ENV === 'development'

let mainWindow = null
let orderKlineWindow = null
let orderKlinePayload = null
let binanceView = null
let _autoExpandDone = false
let _splitRatio = 0.60   // default left panel 60% horizontal
let _chartRatio = 0.65   // default chart 65% vertical within left panel
let _binanceViewVisible = true
let _binanceViewAttached = false
let _lastBinanceViewBoundsKey = null
let _activeOrderLinesState = null
let _activeOrderLinesRequestSequence = 0
let _activeOrderLinesLayoutReplayGeneration = 0
const _chartTpMenuTokens = new Map()
const _chartTpOptionsClaims = new Map()
const _chartQuickOrderSettings = new Map()
let _chartQuickOrderPreferences = null
const _chartCurrentPrices = new Map()

function logBinanceView(action, extra = undefined) {
  logger.info(`[BINANCE_VIEW] action=${action}`, extra)
}

// Map TRADE_RELAY_LANG (zh|en) → Binance locale path segment
const _trLang = (process.env.TRADE_RELAY_LANG || '').toLowerCase()
const _defaultBinanceLang = _trLang === 'en' ? 'en' : _trLang === 'zh' ? 'zh-CN' : 'zh-CN'
const BINANCE_LANG   = process.env.BINANCE_LANG   || _defaultBinanceLang
const UI_LANG        = process.env.UI_LANG        || _defaultBinanceLang
let runtimeUiLocale  = UI_LANG === 'en' ? 'en' : 'zh-CN'
const BINANCE_SYMBOL = process.env.BINANCE_SYMBOL || 'BTCUSDC'
const BACKEND_PORT   = process.env.BACKEND_PORT   || '8000'
const BACKEND_BASE_URL = normalizeBaseUrl(
  process.env.TRADE_RELAY_API_BASE_URL
  || process.env.BACKEND_BASE_URL
  || `http://127.0.0.1:${BACKEND_PORT}`
)
const DEV_SERVER_URL = process.env.DEV_SERVER_URL || `http://127.0.0.1:${process.env.DEV_SERVER_PORT || '5173'}`
const BINANCE_URL    = `https://www.binance.com/${BINANCE_LANG}/futures/${BINANCE_SYMBOL}`

function normalizeBinanceFuturesPair(symbol) {
  const normalized = String(symbol || '').trim().toUpperCase()
  if (!normalized) return ''
  return /(USDT|USDC|FDUSD|BUSD)$/.test(normalized)
    ? normalized
    : `${normalized}USDT`
}

function normalizeBaseUrl(url) {
  return String(url || '').trim().replace(/\/+$/, '')
}

function summarizeError(error) {
  if (!error) return { message: 'Unknown error' }
  return {
    name: error.name,
    message: error.message,
    code: error.code,
    errno: error.errno,
    syscall: error.syscall,
    address: error.address,
    port: error.port,
  }
}

const _proxyAgentCache = new Map()

function shouldBypassProxy(targetUrl) {
  const hostname = String(targetUrl.hostname || '').trim().toLowerCase()
  if (!hostname) return true
  if (hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1') return true

  const noProxy = String(process.env.NO_PROXY || process.env.no_proxy || '').trim()
  if (!noProxy) return false

  const entries = noProxy.split(',').map((item) => item.trim().toLowerCase()).filter(Boolean)
  return entries.some((entry) => {
    if (entry === '*') return true
    const normalized = entry.startsWith('.') ? entry.slice(1) : entry
    return hostname === normalized || hostname.endsWith(`.${normalized}`)
  })
}

function getProxyUrlForTarget(targetUrl) {
  if (shouldBypassProxy(targetUrl)) return null
  if (targetUrl.protocol === 'https:') {
    return process.env.HTTPS_PROXY || process.env.HTTP_PROXY || process.env.ALL_PROXY || process.env.PROXY || process.env.BACKEND_PROXY_URL || null
  }
  return process.env.HTTP_PROXY || process.env.ALL_PROXY || process.env.PROXY || process.env.BACKEND_PROXY_URL || null
}

function getProxyAgent(targetUrl) {
  const proxyUrl = getProxyUrlForTarget(targetUrl)
  if (!proxyUrl) return undefined
  const cacheKey = `${targetUrl.protocol}|${proxyUrl}`
  if (!_proxyAgentCache.has(cacheKey)) {
    _proxyAgentCache.set(cacheKey, new ProxyAgent(proxyUrl))
    logger.info('[ELECTRON_BACKEND_PROXY] agent-created', { proxyUrl, target: targetUrl.origin })
  }
  return _proxyAgentCache.get(cacheKey)
}

// ── JWT token storage (in-memory + safeStorage) ──────────────────────────────
let _tokenStore = null

function decodeJwtPayload(token) {
  if (!token || typeof token !== 'string') return null
  const segments = token.split('.')
  if (segments.length < 2) return null

  try {
    const base64 = segments[1]
      .replace(/-/g, '+')
      .replace(/_/g, '/')
      .padEnd(Math.ceil(segments[1].length / 4) * 4, '=')
    return JSON.parse(Buffer.from(base64, 'base64').toString('utf8'))
  } catch {
    return null
  }
}

function isTokenExpired(token) {
  const payload = decodeJwtPayload(token)
  const exp = Number(payload?.exp)
  if (!Number.isFinite(exp) || exp <= 0) return false
  return exp <= Math.floor(Date.now() / 1000)
}

function storeToken(token) {
  if (safeStorage.isEncryptionAvailable()) {
    try { _tokenStore = safeStorage.encryptString(token).toString('base64') } catch { _tokenStore = token }
  } else {
    _tokenStore = token
  }
}

function getToken() {
  if (!_tokenStore) return null
  let token = _tokenStore
  if (safeStorage.isEncryptionAvailable()) {
    try { token = safeStorage.decryptString(Buffer.from(_tokenStore, 'base64')) } catch { token = _tokenStore }
  }
  if (isTokenExpired(token)) {
    clearToken()
    logger.info('[ELECTRON_AUTH] action=token phase=expired_cleared')
    return null
  }
  return token
}

function clearToken() { _tokenStore = null }

// ── HTTP helper for backend API ───────────────────────────────────────────────
function httpRequest(method, path, body, token) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null
    const headers = { 'Content-Type': 'application/json' }
    if (token) headers['Authorization'] = `Bearer ${token}`
    if (data) headers['Content-Length'] = Buffer.byteLength(data)

    const targetUrl = new URL(path, `${BACKEND_BASE_URL}/`)
    const transport = targetUrl.protocol === 'https:' ? https : http
    const agent = getProxyAgent(targetUrl)
    const requestId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
    const startedAt = Date.now()
    const proxyUrl = getProxyUrlForTarget(targetUrl)

    logger.info('[ELECTRON_BACKEND_HTTP] phase=request', {
      requestId,
      method,
      url: targetUrl.toString(),
      proxy: proxyUrl || 'DIRECT',
      hasToken: Boolean(token),
    })

    const req = transport.request(
      {
        protocol: targetUrl.protocol,
        hostname: targetUrl.hostname,
        port: targetUrl.port || undefined,
        path: `${targetUrl.pathname}${targetUrl.search}`,
        method,
        headers,
        agent,
      },
      (res) => {
        let chunks = ''
        res.on('data', d => (chunks += d))
        res.on('end', () => {
          const durationMs = Date.now() - startedAt
          logger.info('[ELECTRON_BACKEND_HTTP] phase=response', {
            requestId,
            method,
            url: targetUrl.toString(),
            status: res.statusCode,
            durationMs,
            bytes: Buffer.byteLength(chunks || '', 'utf8'),
          })
          try { resolve({ status: res.statusCode, body: JSON.parse(chunks) }) }
          catch { resolve({ status: res.statusCode, body: chunks }) }
        })
      }
    )
    req.on('error', (error) => {
      logger.error('[ELECTRON_BACKEND_HTTP] phase=error', {
        requestId,
        method,
        url: targetUrl.toString(),
        durationMs: Date.now() - startedAt,
        details: summarizeError(error),
      })
      reject(error)
    })
    if (data) req.write(data)
    req.end()
  })
}

function downloadServerLog(filename, destinationPath, token) {
  return new Promise((resolve) => {
    const requestPath = `/api/server-logs/${encodeURIComponent(filename)}/download`
    const targetUrl = new URL(requestPath, `${BACKEND_BASE_URL}/`)
    const transport = targetUrl.protocol === 'https:' ? https : http
    const headers = token ? { Authorization: `Bearer ${token}` } : {}
    const temporaryPath = `${destinationPath}.trade-relay-download-${process.pid}-${Date.now()}`
    const request = transport.request({
      protocol: targetUrl.protocol,
      hostname: targetUrl.hostname,
      port: targetUrl.port || undefined,
      path: `${targetUrl.pathname}${targetUrl.search}`,
      method: 'GET',
      headers,
      agent: getProxyAgent(targetUrl),
    }, (response) => {
      if ((response.statusCode || 500) < 200 || (response.statusCode || 500) >= 300) {
        let message = ''
        response.setEncoding('utf8')
        response.on('data', chunk => {
          if (message.length < 4096) message += chunk
        })
        response.on('end', () => {
          try {
            const parsed = JSON.parse(message)
            resolve({ ok: false, error: parsed.detail || `HTTP ${response.statusCode}` })
          } catch {
            resolve({ ok: false, error: message || `HTTP ${response.statusCode}` })
          }
        })
        return
      }

      const output = fs.createWriteStream(temporaryPath, { flags: 'wx' })
      response.pipe(output)
      output.on('finish', () => {
        output.close(() => {
          try {
            fs.copyFileSync(temporaryPath, destinationPath)
            fs.unlinkSync(temporaryPath)
            resolve({ ok: true, path: destinationPath })
          } catch (error) {
            try { fs.unlinkSync(temporaryPath) } catch {}
            resolve({ ok: false, error: error?.message || String(error) })
          }
        })
      })
      output.on('error', (error) => {
        response.destroy()
        try { fs.unlinkSync(temporaryPath) } catch {}
        resolve({ ok: false, error: error?.message || String(error) })
      })
    })
    request.on('error', (error) => {
      try { fs.unlinkSync(temporaryPath) } catch {}
      resolve({ ok: false, error: error?.message || String(error) })
    })
    request.end()
  })
}

function buildBackendPath(pathname, query) {
  if (!query || typeof query !== 'object') return pathname
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value == null) continue
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item != null) search.append(key, String(item))
      }
      continue
    }
    search.append(key, String(value))
  }
  const suffix = search.toString()
  return suffix ? `${pathname}?${suffix}` : pathname
}

function waitForOverlayStatus(action, timeoutMs = 4000) {
  return new Promise((resolve) => {
    if (!binanceView) {
      resolve({ action, ok: false, reason: 'no_view' })
      return
    }

    const targetWebContents = binanceView.webContents
    let settled = false

    const cleanup = () => {
      clearTimeout(timer)
      ipcMain.removeListener('overlay-status', handler)
    }

    const finish = (payload) => {
      if (settled) return
      settled = true
      cleanup()
      logger.info('[OVERLAY_IPC] phase=status', { action, payload })
      resolve(payload)
    }

    const handler = (event, payload) => {
      if (event.sender !== targetWebContents) return
      if (!payload || payload.action !== action) return
      finish(payload)
    }

    const timer = setTimeout(() => {
      logger.warn('[OVERLAY_IPC] phase=timeout', { action, timeoutMs })
      finish({ action, ok: false, reason: 'timeout' })
    }, timeoutMs)

    ipcMain.on('overlay-status', handler)
  })
}

function waitForActiveOrderLinesStatus(requestId, timeoutMs = 12000) {
  return new Promise((resolve) => {
    if (!binanceView || binanceView.webContents.isDestroyed()) {
      resolve({ ok: false, reason: 'no_view' })
      return
    }

    const targetWebContents = binanceView.webContents
    let settled = false

    const cleanup = () => {
      clearTimeout(timer)
      ipcMain.removeListener('active-order-lines-status', handler)
    }

    const finish = (payload) => {
      if (settled) return
      settled = true
      cleanup()
      resolve(payload)
    }

    const handler = (event, payload) => {
      if (event.sender !== targetWebContents) return
      if (!payload || payload.requestId !== requestId) return
      finish(payload)
    }

    const timer = setTimeout(() => {
      logger.warn('[ACTIVE_ORDER_LINES] phase=timeout', { requestId, timeoutMs })
      finish({ ok: false, reason: 'timeout' })
    }, timeoutMs)

    ipcMain.on('active-order-lines-status', handler)
  })
}

async function sendActiveOrderLinesToChart(orders, locale, reason) {
  if (!binanceView || binanceView.webContents.isDestroyed()) {
    return { ok: false, reason: 'no_view' }
  }

  const requestId = `${Date.now()}-${++_activeOrderLinesRequestSequence}`
  try {
    const statusPromise = waitForActiveOrderLinesStatus(requestId)
    binanceView.webContents.send('active-order-lines', orders, locale, requestId)
    const status = await statusPromise
    logger.info('[ACTIVE_ORDER_LINES] phase=status', {
      reason,
      requestId,
      count: orders.length,
      status,
    })
    return status
  } catch (error) {
    logger.warn('[ACTIVE_ORDER_LINES] phase=send-failed', {
      reason,
      requestId,
      details: error?.message || 'send_failed',
    })
    return { ok: false, reason: error?.message || 'send_failed' }
  }
}

function scheduleActiveOrderLinesLayoutReplay(reason) {
  const generation = ++_activeOrderLinesLayoutReplayGeneration
  if (!_activeOrderLinesState) return

  // BrowserView resizing returns before Binance/TradingView has finished
  // rebuilding its drawing layer. Replay once after the immediate resize and
  // once after layout settles; the generation guard collapses resize storms.
  for (const delayMs of [250, 1_500]) {
    setTimeout(() => {
      if (generation !== _activeOrderLinesLayoutReplayGeneration) return
      if (!mainWindow || mainWindow.isDestroyed() || mainWindow.isMinimized()) return
      if (!binanceView || binanceView.webContents.isDestroyed()) return
      if (!_binanceViewVisible || !_binanceViewAttached) return
      if (!_activeOrderLinesState) return
      const { orders, locale } = _activeOrderLinesState
      void sendActiveOrderLinesToChart(orders, locale, `layout-${reason}-${delayMs}ms`)
    }, delayMs)
  }
}

// ── Retryable load ────────────────────────────────────────────────────────────
const RETRYABLE_ERRORS = new Set([-21, -2, -7, -100, -101, -102, -105, -106])

function loadBinanceWithRetry(url, retries = 5, delayMs = 2000) {
  if (!binanceView) return
  binanceView.webContents.loadURL(url).catch(() => {})
  binanceView.webContents.removeAllListeners('did-fail-load')
  binanceView.webContents.on('did-fail-load', (_event, errorCode, _errorDesc, _url, isMainFrame) => {
    if (!isMainFrame) return
    if (RETRYABLE_ERRORS.has(errorCode) && retries > 0) {
      logger.warn(`Binance retry in ${delayMs}ms (${retries} left)`)
      setTimeout(() => loadBinanceWithRetry(url, retries - 1, Math.min(delayMs * 1.5, 10000)), delayMs)
    }
  })
}

// ── Main window ───────────────────────────────────────────────────────────────
function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1920,
    height: 1200,
    minWidth: 1280,
    minHeight: 800,
    backgroundColor: '#1e1e1e',
    titleBarStyle: 'hidden',
    frame: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
      backgroundThrottling: false,
    },
  })

  if (isDev) {
    mainWindow.loadURL(DEV_SERVER_URL)
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
  }

  mainWindow.on('closed', () => {
    _activeOrderLinesLayoutReplayGeneration += 1
    if (orderKlineWindow && !orderKlineWindow.isDestroyed()) orderKlineWindow.close()
    orderKlineWindow = null
    orderKlinePayload = null
    mainWindow = null
  })
  mainWindow.webContents.on('did-finish-load', () => updateBinanceViewBounds())
  mainWindow.on('resize', () => {
    updateBinanceViewBounds()
    scheduleActiveOrderLinesLayoutReplay('resize')
  })
  mainWindow.on('maximize', () => scheduleActiveOrderLinesLayoutReplay('maximize'))
  mainWindow.on('unmaximize', () => scheduleActiveOrderLinesLayoutReplay('unmaximize'))
  mainWindow.on('restore', () => scheduleActiveOrderLinesLayoutReplay('restore'))

  mainWindow.webContents.on('before-input-event', (_event, input) => {
    if (input.type === 'keyDown' && input.key === 'F12') {
      if (mainWindow.webContents.isDevToolsOpened()) mainWindow.webContents.closeDevTools()
      else mainWindow.webContents.openDevTools({ mode: 'detach' })
    }
  })
}

function getOrderKlineWindowBounds(sourceWindow, requestedWidth, requestedHeight) {
  const anchorWindow = sourceWindow && !sourceWindow.isDestroyed() ? sourceWindow : mainWindow
  const display = anchorWindow && !anchorWindow.isDestroyed()
    ? screen.getDisplayMatching(anchorWindow.getBounds())
    : screen.getPrimaryDisplay()
  const workArea = display.workArea
  const topInset = 45
  const width = Math.min(Math.round(requestedWidth), workArea.width)
  const height = Math.min(Math.round(requestedHeight), workArea.height - topInset)
  return {
    x: Math.round(workArea.x + (workArea.width - width) / 2),
    y: workArea.y + topInset,
    width,
    height,
  }
}

function openOrderKlineWindow(payload, sourceWindow) {
  orderKlinePayload = payload
  if (orderKlineWindow && !orderKlineWindow.isDestroyed()) {
    const currentBounds = orderKlineWindow.getBounds()
    const wasMaximized = orderKlineWindow.isMaximized()
    if (orderKlineWindow.isMinimized()) orderKlineWindow.restore()
    if (wasMaximized) orderKlineWindow.unmaximize()
    orderKlineWindow.setBounds(getOrderKlineWindowBounds(sourceWindow, currentBounds.width, currentBounds.height))
    if (wasMaximized) orderKlineWindow.maximize()
    orderKlineWindow.webContents.send('order-kline-payload', orderKlinePayload)
    orderKlineWindow.show()
    orderKlineWindow.focus()
    return
  }

  const initialBounds = getOrderKlineWindowBounds(sourceWindow, 1500, 1238)
  orderKlineWindow = new BrowserWindow({
    ...initialBounds,
    minWidth: 760,
    minHeight: 576,
    show: false,
    frame: false,
    resizable: true,
    backgroundColor: '#101318',
    title: 'Position Candles',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
      backgroundThrottling: false,
    },
  })

  if (isDev) {
    const separator = DEV_SERVER_URL.includes('?') ? '&' : '?'
    orderKlineWindow.loadURL(`${DEV_SERVER_URL}${separator}window=order-kline`)
  } else {
    orderKlineWindow.loadFile(path.join(__dirname, '../dist/index.html'), {
      query: { window: 'order-kline' },
    })
  }

  orderKlineWindow.webContents.on('did-finish-load', () => {
    orderKlineWindow?.webContents.send('order-kline-payload', orderKlinePayload)
  })
  orderKlineWindow.once('ready-to-show', () => {
    orderKlineWindow?.show()
    orderKlineWindow?.focus()
  })
  orderKlineWindow.on('closed', () => {
    orderKlineWindow = null
    orderKlinePayload = null
  })
}

// ── Binance BrowserView ───────────────────────────────────────────────────────
function createBinanceView() {
  binanceView = new BrowserView({
    webPreferences: {
      preload: path.join(__dirname, 'binance-preload.js'),
      contextIsolation: false,
      nodeIntegration: false,
      webSecurity: false,
      allowRunningInsecureContent: false,
      backgroundThrottling: false,
    },
  })

  if (_binanceViewVisible) {
    mainWindow.addBrowserView(binanceView)
    _binanceViewAttached = true
  } else {
    _binanceViewAttached = false
  }
  const rawUA = binanceView.webContents.getUserAgent()
  binanceView.webContents.setUserAgent(rawUA.replace(/\s*Electron\/[\d.]+/, ''))

  loadBinanceWithRetry(BINANCE_URL)

  function notifySymbolFromUrl(url) {
    if (!mainWindow) return
    try {
      const m = url.match(/\/futures\/([A-Z0-9]+)/i)
      if (m) mainWindow.webContents.send('binance-symbol-change', m[1].toUpperCase())
    } catch {}
  }
  binanceView.webContents.on('did-navigate', (_e, url) => notifySymbolFromUrl(url))
  binanceView.webContents.on('did-navigate-in-page', (_e, url) => notifySymbolFromUrl(url))
  binanceView.webContents.on('frame-created', (_event, details) => {
    const frame = details?.frame
    if (!frame) return
    frame.once('dom-ready', () => { void installChartTpWatcherInFrame(frame) })
  })
  binanceView.webContents.on('did-frame-finish-load', () => {
    installChartTpWatchersInChildFrames()
  })

  binanceView.webContents.on('enter-html-full-screen', () => {
    setImmediate(() => {
      if (mainWindow?.isFullScreen()) mainWindow.setFullScreen(false)
      updateBinanceViewBounds()
    })
  })
  binanceView.webContents.on('leave-html-full-screen', () => {
    setImmediate(() => updateBinanceViewBounds())
  })

  // Auto-expand TradingView chart on first load
  binanceView.webContents.on('did-finish-load', () => {
    // Renderer state can arrive before the Binance preload/chart is ready.
    // Replay it after every navigation so terminal restarts and chart reloads
    // rebuild unchanged active-order lines as well.
    if (_activeOrderLinesState) {
      const { orders, locale } = _activeOrderLinesState
      void sendActiveOrderLinesToChart(orders, locale, 'view-loaded')
    }

    if (_autoExpandDone) return
    _autoExpandDone = true
    const MAX_ATTEMPTS = 30
    let attempts = 0

    const tryClick = async () => {
      attempts++
      try {
        const frames = binanceView.webContents.mainFrame.framesInSubtree
        for (const frame of frames) {
          try {
            const pos = await frame.executeJavaScript(`
              (() => {
                const svg = document.querySelector('svg.chart-fullscreen-icon')
                if (!svg) return null
                const target = svg.closest('button,[role="button"],[class*="fullscreen"]') || svg
                const rect = target.getBoundingClientRect()
                return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
              })()
            `, true)
            if (!pos) continue

            let ox = 0, oy = 0
            if (frame !== binanceView.webContents.mainFrame) {
              const frameUrl = frame.url || ''
              const offset = await binanceView.webContents.mainFrame.executeJavaScript(`
                (() => {
                  const iframes = [...document.querySelectorAll('iframe')]
                  const target = ${JSON.stringify(frameUrl)}
                  for (const f of iframes) {
                    if (target && f.src && f.src === target) { const r = f.getBoundingClientRect(); return { x: r.left, y: r.top } }
                  }
                  let best = null, bestArea = 0
                  for (const f of iframes) { const r = f.getBoundingClientRect(); const area = r.width * r.height; if (area > bestArea) { bestArea = area; best = r } }
                  return best ? { x: best.left, y: best.top } : { x: 0, y: 0 }
                })()
              `, true).catch(() => ({ x: 0, y: 0 }))
              ox = offset.x; oy = offset.y
            }

            const absX = Math.round(pos.x + ox), absY = Math.round(pos.y + oy)
            binanceView.webContents.sendInputEvent({ type: 'mouseMove', x: absX, y: absY })
            binanceView.webContents.sendInputEvent({ type: 'mouseDown', x: absX, y: absY, button: 'left', clickCount: 1 })
            await new Promise(r => setTimeout(r, 50))
            binanceView.webContents.sendInputEvent({ type: 'mouseUp', x: absX, y: absY, button: 'left', clickCount: 1 })
            logger.info(`auto-expand: clicked at ${absX} ${absY}`)
            if (mainWindow) mainWindow.webContents.send('chart-expand-state-change', true)
            return
          } catch {}
        }
      } catch {}
      if (attempts < MAX_ATTEMPTS) setTimeout(tryClick, 1000)
    }

    setTimeout(tryClick, 3000)
  })
}

function ensureBinanceViewAttached() {
  if (!binanceView || !mainWindow || _binanceViewAttached) return
  logBinanceView('attach')
  mainWindow.addBrowserView(binanceView)
  _binanceViewAttached = true
}

function detachBinanceView() {
  if (!binanceView || !mainWindow || !_binanceViewAttached) return
  logBinanceView('detach')
  mainWindow.removeBrowserView(binanceView)
  _binanceViewAttached = false
}

function hideBinanceView() {
  if (!binanceView) return
  if (_binanceViewAttached) {
    logBinanceView('hide', { visible: _binanceViewVisible, attached: _binanceViewAttached })
  }
  binanceView.setBounds({ x: -9999, y: -9999, width: 1, height: 1 })
  _lastBinanceViewBoundsKey = null
  detachBinanceView()
}

function updateBinanceViewBounds() {
  if (!binanceView || !mainWindow) return
  if (!_binanceViewVisible) {
    hideBinanceView()
    return
  }
  const bounds = mainWindow.getBounds()
  // Renderer chrome above the trade workspace: 32px title bar + 36px tab bar.
  const TITLEBAR_H = 68
  const STATUSBAR_H = 24
  const availH = bounds.height - TITLEBAR_H - STATUSBAR_H
  const panelWidth = Math.floor(bounds.width * _splitRatio)
  const chartHeight = Math.floor(availH * _chartRatio)
  ensureBinanceViewAttached()
  const nextBounds = {
    x: 0,
    y: TITLEBAR_H,
    width: panelWidth,
    height: chartHeight,
  }
  const boundsKey = JSON.stringify(nextBounds)
  if (_lastBinanceViewBoundsKey !== boundsKey) {
    logBinanceView('update-bounds', {
      visible: true,
      windowBounds: bounds,
      viewBounds: nextBounds,
      splitRatio: _splitRatio,
      chartRatio: _chartRatio,
    })
    _lastBinanceViewBoundsKey = boundsKey
  }
  binanceView.setBounds({
    ...nextBounds,
  })
}

// ── Auth IPC ──────────────────────────────────────────────────────────────────
ipcMain.handle('auth-login', async (_event, { username, password }) => {
  logger.info('[ELECTRON_AUTH] action=login phase=request', { username, backend: BACKEND_BASE_URL })
  try {
    const res = await httpRequest('POST', '/api/auth/login', { username, password }, null)
    if (res.status === 200 && res.body.access_token) {
      storeToken(res.body.access_token)
      logger.info('[ELECTRON_AUTH] action=login phase=success', { username })
      return { ok: true, user: res.body.user }
    }
    logger.warn('[ELECTRON_AUTH] action=login phase=failed', {
      username,
      status: res.status,
      detail: res.body?.detail || 'Login failed',
    })
    return { ok: false, error: res.body?.detail || 'Login failed' }
  } catch (e) {
    logger.error('[ELECTRON_AUTH] action=login phase=error', { username, details: summarizeError(e) })
    return { ok: false, error: `Backend unavailable: ${e.message}` }
  }
})

ipcMain.handle('auth-logout', () => {
  clearToken()
  _chartQuickOrderSettings.clear()
  return { ok: true }
})
ipcMain.handle('auth-get-token', () => getToken())

ipcMain.handle('backend-request', async (_event, { method = 'GET', path, body = null, query = null }) => {
  const token = getToken()
  const requestPath = buildBackendPath(path, query)
  return httpRequest(method, requestPath, body, token)
})

ipcMain.handle('download-server-log', async (_event, filename) => {
  const safeName = String(filename || '').trim()
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]*\.log(?:\.\d+)?$/.test(safeName)) {
    return { ok: false, error: 'Invalid log filename' }
  }
  const token = getToken()
  if (!token) return { ok: false, error: 'Not authenticated' }

  const selection = await dialog.showSaveDialog(mainWindow, {
    title: 'Download server log',
    defaultPath: safeName,
    filters: [{ name: 'Log files', extensions: ['log'] }, { name: 'All files', extensions: ['*'] }],
  })
  if (selection.canceled || !selection.filePath) return { ok: false, canceled: true }
  return downloadServerLog(safeName, selection.filePath, token)
})

ipcMain.handle('auth-get-status', async () => {
  const token = getToken()
  if (!token) return { authenticated: false }
  try {
    const res = await httpRequest('GET', '/api/auth/me', null, token)
    if (res.status === 200) return { authenticated: true, user: res.body }
    clearToken()
    return { authenticated: false }
  } catch {
    return { authenticated: false }
  }
})

// ── Standard IPC ──────────────────────────────────────────────────────────────
// Renderer-to-main log forwarding — writes renderer WS logs into the Electron log file
ipcMain.on('log-to-main', (_event, level, msg, extra) => {
  const fn = logger[level] || logger.info
  fn.call(logger, `[FRONTEND] ${msg}`, extra)
})

ipcMain.handle('get-ui-lang', () => runtimeUiLocale)
ipcMain.on('get-ui-lang-sync', (event) => { event.returnValue = runtimeUiLocale })
ipcMain.handle('set-ui-lang', (_event, locale) => {
  runtimeUiLocale = locale === 'en' ? 'en' : 'zh-CN'
  return runtimeUiLocale
})
ipcMain.handle('get-backend-base-url', () => BACKEND_BASE_URL)
ipcMain.on('get-backend-base-url-sync', (event) => { event.returnValue = BACKEND_BASE_URL })

ipcMain.handle('set-chart-quick-order-settings', (event, payload = {}) => {
  if (!mainWindow || event.sender !== mainWindow.webContents) return { ok: false, reason: 'invalid_sender' }
  const tokenPayload = decodeJwtPayload(getToken())
  const username = String(payload.username || '').trim()
  const symbol = normalizeChartTradeSymbol(payload.symbol)
  const quantity = Number(payload.quantity)
  const leverage = Number(payload.leverage)
  const positionMode = String(payload.positionMode || '').toUpperCase()
  if (!tokenPayload || username !== String(tokenPayload.username || '')) {
    return { ok: false, reason: 'not_authenticated' }
  }
  if (!symbol) return { ok: false, reason: 'invalid_symbol' }
  _chartQuickOrderSettings.set(symbol, {
    username,
    symbol,
    quantity: Number.isFinite(quantity) && quantity > 0 ? quantity : null,
    inputQuantity: Number(payload.inputQuantity),
    sizeUnit: payload.sizeUnit === 'BASE' ? 'BASE' : 'QUOTE',
    leverage: Number.isInteger(leverage) && leverage >= 1 && leverage <= 125 ? leverage : 10,
    positionMode: positionMode === 'SINGLE' || positionMode === 'DUAL' ? positionMode : null,
  })
  return { ok: true }
})

ipcMain.handle('get-order-size-preference', (event, payload = {}) => {
  if (!mainWindow || event.sender !== mainWindow.webContents) return { ok: false, reason: 'invalid_sender' }
  const tokenPayload = decodeJwtPayload(getToken())
  const username = String(payload.username || '').trim()
  const symbol = normalizeChartTradeSymbol(payload.symbol)
  if (!tokenPayload || username !== String(tokenPayload.username || '')) {
    return { ok: false, reason: 'not_authenticated' }
  }
  if (!symbol) return { ok: false, reason: 'invalid_symbol' }
  return {
    ok: true,
    preference: getChartQuickOrderPreference(username, symbol),
  }
})

ipcMain.handle('save-order-size-preference', (event, payload = {}) => {
  if (!mainWindow || event.sender !== mainWindow.webContents) return { ok: false, reason: 'invalid_sender' }
  const tokenPayload = decodeJwtPayload(getToken())
  const username = String(payload.username || '').trim()
  const symbol = normalizeChartTradeSymbol(payload.symbol)
  if (!tokenPayload || username !== String(tokenPayload.username || '')) {
    return { ok: false, reason: 'not_authenticated' }
  }
  if (!symbol) return { ok: false, reason: 'invalid_symbol' }
  return saveChartQuickOrderPreference(
    username,
    symbol,
    payload.inputQuantity,
    payload.sizeUnit,
  )
    ? { ok: true }
    : { ok: false, reason: 'preference_save_failed' }
})

ipcMain.handle('resize-binance-panel', (_event, splitRatio, chartRatio) => {
  if (chartRatio != null) _chartRatio = Math.max(0.1, Math.min(1, chartRatio))
  if (splitRatio === 0) {
    // Hide BrowserView by moving it off-screen
    _activeOrderLinesLayoutReplayGeneration += 1
    _splitRatio = 0
    hideBinanceView()
    return
  }
  _splitRatio = Math.max(0.1, Math.min(0.95, splitRatio))
  if (!binanceView || !mainWindow) return
  updateBinanceViewBounds()
  scheduleActiveOrderLinesLayoutReplay('panel-resize')
})

ipcMain.handle('navigate-binance', (_event, symbol) => {
  if (!binanceView) return
  const pair = normalizeBinanceFuturesPair(symbol)
  if (!pair) return
  _autoExpandDone = false
  loadBinanceWithRetry(`https://www.binance.com/${BINANCE_LANG}/futures/${pair}`)
})

ipcMain.handle('switch-chart-symbol', async (_event, symbol) => {
  if (!binanceView) return false
  try {
    return await binanceView.webContents.executeJavaScript(`window.__omnitrader.switchSymbol(${JSON.stringify(symbol)})`)
  } catch { return false }
})

ipcMain.handle('binance-go-back', () => { if (binanceView?.webContents.canGoBack()) binanceView.webContents.goBack() })
ipcMain.handle('binance-go-forward', () => { if (binanceView?.webContents.canGoForward()) binanceView.webContents.goForward() })
ipcMain.handle('binance-reload', () => { binanceView?.webContents.reload() })

ipcMain.handle('set-binance-view-visible', (_event, visible) => {
  const nextVisible = Boolean(visible)
  const isNoOp = nextVisible === _binanceViewVisible
    && ((nextVisible && _binanceViewAttached) || (!nextVisible && !_binanceViewAttached))
  if (isNoOp) return

  logBinanceView('set-visible', {
    from: _binanceViewVisible,
    to: nextVisible,
    hasView: Boolean(binanceView),
    attached: _binanceViewAttached,
  })
  _binanceViewVisible = nextVisible
  if (!binanceView || !mainWindow) return
  if (nextVisible) {
    updateBinanceViewBounds()
    scheduleActiveOrderLinesLayoutReplay('view-shown')
  } else {
    _activeOrderLinesLayoutReplayGeneration += 1
    // Move off-screen to hide without destroying the view
    hideBinanceView()
  }
})

ipcMain.on('market-data', (event, ...args) => {
  if (!binanceView || !mainWindow || event.sender !== binanceView.webContents) return
  const marketEvent = args[0]
  const symbol = normalizeChartTradeSymbol(marketEvent?.symbol)
  const price = marketEvent?.type === 'trade'
    ? Number(marketEvent.price)
    : marketEvent?.type === 'kline'
      ? Number(marketEvent.close)
      : NaN
  if (symbol && Number.isFinite(price) && price > 0) _chartCurrentPrices.set(symbol, price)
  mainWindow.webContents.send('market-data', ...args)
})

ipcMain.on('chart-interval-change', (event, interval) => {
  if (!binanceView || !mainWindow || event.sender !== binanceView.webContents) return
  mainWindow.webContents.send('binance-interval-change', interval)
})

ipcMain.on('chart-expand-state-change', (event, expanded) => {
  if (!binanceView || !mainWindow || event.sender !== binanceView.webContents) return
  mainWindow.webContents.send('chart-expand-state-change', expanded)
  scheduleActiveOrderLinesLayoutReplay(expanded ? 'chart-expand' : 'chart-collapse')
})

ipcMain.handle('chart-toggle-fullscreen', async () => {
  if (!binanceView) return { ok: false, reason: 'no_view' }
  try {
    const mainFrame = binanceView.webContents.mainFrame
    for (const frame of mainFrame.framesInSubtree) {
      try {
        const pos = await frame.executeJavaScript(`
          (() => {
            const svg = document.querySelector('svg.chart-fullscreen-icon')
            if (!svg) return null
            const target = svg.closest('button,[role="button"],[class*="fullscreen"]') || svg
            const rect = target.getBoundingClientRect()
            return { x: rect.left + rect.width/2, y: rect.top + rect.height/2 }
          })()
        `, true)
        if (!pos) continue

        let ox = 0, oy = 0
        if (frame !== mainFrame) {
          const frameUrl = frame.url || ''
          const offset = await mainFrame.executeJavaScript(`
            (() => {
              const iframes = [...document.querySelectorAll('iframe')]
              const target = ${JSON.stringify(frameUrl)}
              for (const f of iframes) {
                if (target && f.src && f.src === target) { const r = f.getBoundingClientRect(); return { x: r.left, y: r.top } }
              }
              let best = null, bestArea = 0
              for (const f of iframes) { const r = f.getBoundingClientRect(); const area = r.width * r.height; if (area > bestArea) { bestArea = area; best = r } }
              return best ? { x: best.left, y: best.top } : { x: 0, y: 0 }
            })()
          `, true).catch(() => ({ x: 0, y: 0 }))
          ox = offset.x; oy = offset.y
        }

        const absX = Math.round(pos.x + ox), absY = Math.round(pos.y + oy)
        binanceView.webContents.sendInputEvent({ type: 'mouseMove', x: absX, y: absY })
        binanceView.webContents.sendInputEvent({ type: 'mouseDown', x: absX, y: absY, button: 'left', clickCount: 1 })
        await new Promise(r => setTimeout(r, 50))
        binanceView.webContents.sendInputEvent({ type: 'mouseUp', x: absX, y: absY, button: 'left', clickCount: 1 })
        return { ok: true }
      } catch {}
    }
    return { ok: false, reason: 'not_found' }
  } catch (e) { return { ok: false, reason: e.message } }
})

function isBinanceViewSender(event) {
  return Boolean(binanceView && event.sender === binanceView.webContents)
}

function normalizeChartTradeSymbol(symbol) {
  return String(symbol || '')
    .trim()
    .toUpperCase()
    .replace(/\.P$/, '')
    .replace(/[^A-Z0-9]/g, '')
}

function chartQuickOrderPreferencesPath() {
  return path.join(app.getPath('userData'), 'chart-quick-order-preferences.json')
}

function chartQuickOrderPreferenceKey(username, symbol) {
  return JSON.stringify([String(username || ''), normalizeChartTradeSymbol(symbol)])
}

function loadChartQuickOrderPreferences() {
  if (_chartQuickOrderPreferences) return _chartQuickOrderPreferences
  const preferences = new Map()
  try {
    const parsed = JSON.parse(fs.readFileSync(chartQuickOrderPreferencesPath(), 'utf8'))
    for (const entry of Array.isArray(parsed?.preferences) ? parsed.preferences : []) {
      const username = String(entry?.username || '')
      const symbol = normalizeChartTradeSymbol(entry?.symbol)
      const preference = normalizeQuickOrderPreference(entry?.inputQuantity, entry?.sizeUnit)
      if (username && symbol && preference) {
        preferences.set(chartQuickOrderPreferenceKey(username, symbol), {
          username,
          symbol,
          ...preference,
        })
      }
    }
  } catch (error) {
    if (error?.code !== 'ENOENT') {
      logger.warn('[CHART_QUICK_ORDER] phase=preferences-load-failed', {
        reason: error?.message || 'unknown_error',
      })
    }
  }
  _chartQuickOrderPreferences = preferences
  return preferences
}

function getChartQuickOrderPreference(username, symbol) {
  return loadChartQuickOrderPreferences().get(chartQuickOrderPreferenceKey(username, symbol)) || null
}

function saveChartQuickOrderPreference(username, symbol, inputQuantity, sizeUnit) {
  const normalizedUsername = String(username || '')
  const normalizedSymbol = normalizeChartTradeSymbol(symbol)
  const preference = normalizeQuickOrderPreference(inputQuantity, sizeUnit)
  if (!normalizedUsername || !normalizedSymbol || !preference) return false

  const preferences = loadChartQuickOrderPreferences()
  const preferenceKey = chartQuickOrderPreferenceKey(normalizedUsername, normalizedSymbol)
  const previousPreference = preferences.get(preferenceKey)
  preferences.set(preferenceKey, {
    username: normalizedUsername,
    symbol: normalizedSymbol,
    ...preference,
  })
  try {
    const targetPath = chartQuickOrderPreferencesPath()
    const temporaryPath = `${targetPath}.tmp`
    fs.mkdirSync(path.dirname(targetPath), { recursive: true })
    fs.writeFileSync(temporaryPath, JSON.stringify({
      version: 1,
      preferences: Array.from(preferences.values()),
    }, null, 2))
    fs.renameSync(temporaryPath, targetPath)
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('order-size-preference-changed', {
        username: normalizedUsername,
        symbol: normalizedSymbol,
        ...preference,
      })
    }
    return true
  } catch (error) {
    if (previousPreference) preferences.set(preferenceKey, previousPreference)
    else preferences.delete(preferenceKey)
    logger.warn('[CHART_QUICK_ORDER] phase=preferences-save-failed', {
      symbol: normalizedSymbol,
      reason: error?.message || 'unknown_error',
    })
    return false
  }
}

async function getChartTakeProfitContext(symbol, price) {
  const normalizedSymbol = normalizeChartTradeSymbol(symbol)
  const limitPrice = Number(price)
  const token = getToken()
  if (!token) return { ok: false, reason: 'not_authenticated' }
  if (!normalizedSymbol || !Number.isFinite(limitPrice) || limitPrice <= 0) {
    return { ok: false, reason: 'invalid_request' }
  }

  const [positionsResponse, markResponse] = await Promise.all([
    httpRequest('GET', buildBackendPath('/api/positions', { status: 'OPEN' }), null, token),
    httpRequest('GET', buildBackendPath('/api/account/mark-price', { symbol: normalizedSymbol }), null, token),
  ])
  if (positionsResponse.status !== 200) {
    return { ok: false, reason: positionsResponse.body?.detail || 'positions_unavailable' }
  }
  if (markResponse.status !== 200) {
    return { ok: false, reason: markResponse.body?.detail || 'mark_price_unavailable' }
  }

  const markPrice = Number(markResponse.body?.mark_price)
  if (!Number.isFinite(markPrice) || markPrice <= 0) {
    return { ok: false, reason: 'mark_price_unavailable' }
  }

  const positions = Array.isArray(positionsResponse.body) ? positionsResponse.body : []
  const eligiblePositions = positions.filter((position) => {
    if (normalizeChartTradeSymbol(position?.symbol) !== normalizedSymbol) return false
    if (String(position?.status || 'OPEN').toUpperCase() !== 'OPEN') return false
    if (!(Number(position?.quantity) > 0)) return false
    const side = String(position?.side || '').toUpperCase()
    return (side === 'LONG' && limitPrice > markPrice)
      || (side === 'SHORT' && limitPrice < markPrice)
  })

  // Binance has at most one live position per symbol and side. Collapse stale
  // duplicate DB rows defensively so one menu action is rendered per direction.
  const positionsBySide = new Map()
  for (const position of eligiblePositions) {
    const side = String(position.side || '').toUpperCase()
    const existing = positionsBySide.get(side)
    if (!existing || Number(position.id) > Number(existing.id)) positionsBySide.set(side, position)
  }

  const tokenPayload = decodeJwtPayload(token)
  const currentPrice = Number(_chartCurrentPrices.get(normalizedSymbol)) > 0
    ? Number(_chartCurrentPrices.get(normalizedSymbol))
    : markPrice
  const quickSettings = applyQuickOrderPreference(
    _chartQuickOrderSettings.get(normalizedSymbol),
    getChartQuickOrderPreference(tokenPayload?.username, normalizedSymbol),
    tokenPayload?.username,
    currentPrice,
  )
  const quickStop = buildQuickStop(
    limitPrice,
    currentPrice,
    quickSettings,
    tokenPayload?.username,
  )
  const quickLimit = buildQuickLimit(
    limitPrice,
    currentPrice,
    quickSettings,
    tokenPayload?.username,
  )
  const { baseAsset, quoteAsset } = splitTradeSymbol(normalizedSymbol)

  return {
    ok: true,
    symbol: normalizedSymbol,
    price: limitPrice,
    markPrice,
    currentPrice,
    baseAsset,
    quoteAsset,
    positions: Array.from(positionsBySide.values()).map((position) => ({
      id: Number(position.id),
      symbol: normalizedSymbol,
      side: String(position.side || '').toUpperCase(),
      quantity: Number(position.quantity),
    })),
    quickLimit,
    quickStop,
  }
}

function issueChartTpMenuToken(context) {
  const now = Date.now()
  for (const [token, record] of _chartTpMenuTokens) {
    if (record.expiresAt <= now) _chartTpMenuTokens.delete(token)
  }
  const token = crypto.randomUUID()
  _chartTpMenuTokens.set(token, {
    symbol: context.symbol,
    price: context.price,
    positionIds: new Set(context.positions.map((position) => position.id)),
    quickLimit: context.quickLimit ? { ...context.quickLimit } : null,
    quickStop: context.quickStop ? { ...context.quickStop } : null,
    expiresAt: now + 60_000,
  })
  return token
}

function chartTpFrameWatcherScript() {
  return `(() => {
    if (window.__tradeRelayChartTpWatcherInstalled) return true
    window.__tradeRelayChartTpWatcherInstalled = true
    const menuAttr = 'data-trade-relay-tp-menu'
    let timers = []

    const findMenu = () => {
      const pattern = /(?:Copy price|复制价格)\\s*([\\d,]+(?:\\.\\d+)?)/i
      const candidates = Array.from(document.querySelectorAll(
        '[role="menu"], [data-name*="menu" i], [class*="context-menu" i], [class*="menuWrap" i]'
      ))
      let menu = candidates
        .filter((element) => pattern.test(String(element.innerText || '')))
        .sort((a, b) => String(a.innerText || '').length - String(b.innerText || '').length)[0] || null
      if (!menu && document.body) {
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
        let node
        while ((node = walker.nextNode())) {
          if (!pattern.test(String(node.nodeValue || ''))) continue
          let element = node.parentElement
          let best = null
          for (let depth = 0; element && depth < 9; depth += 1, element = element.parentElement) {
            const text = String(element.innerText || '')
            if (pattern.test(text) && text.length < 1600) best = element
            if (element.getAttribute('role') === 'menu') { best = element; break }
          }
          if (best) { menu = best; break }
        }
      }
      if (!menu) return null
      const match = String(menu.innerText || '').match(pattern)
      const price = match ? Number(match[1].replace(/,/g, '')) : NaN
      return Number.isFinite(price) && price > 0 ? { menu, price } : null
    }

    const toast = (message, success) => {
      document.getElementById('trade-relay-chart-tp-frame-toast')?.remove()
      const node = document.createElement('div')
      node.id = 'trade-relay-chart-tp-frame-toast'
      node.textContent = message
      Object.assign(node.style, {
        position: 'fixed', zIndex: '2147483647', right: '18px', top: '18px', maxWidth: '420px',
        padding: '10px 14px', borderRadius: '6px',
        border: '1px solid ' + (success ? '#0ECB81' : '#F6465D'), background: '#1E2329',
        color: success ? '#8ee8c2' : '#ff9aa8',
        font: '13px/1.4 -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif',
        boxShadow: '0 8px 24px rgba(0,0,0,.4)'
      })
      document.body.appendChild(node)
      setTimeout(() => node.remove(), 4000)
    }

    const requestTop = (type, payload, responseType) => new Promise((resolve) => {
      const requestId = 'chart-tp-' + Date.now() + '-' + Math.random().toString(36).slice(2)
      const timeout = setTimeout(() => {
        window.removeEventListener('message', onMessage)
        resolve({ ok: false, reason: 'request_timeout' })
      }, 8000)
      const onMessage = (event) => {
        const response = event.data
        if (!response || response.type !== responseType || response.requestId !== requestId) return
        clearTimeout(timeout)
        window.removeEventListener('message', onMessage)
        resolve(response.result)
      }
      window.addEventListener('message', onMessage)
      window.top.postMessage({ type, requestId, payload }, '*')
    })

    const fitMenuInViewport = (menu) => {
      if (!menu?.isConnected) return
      menu.style.setProperty('z-index', '2147483646', 'important')
      const fit = () => {
        if (!menu.isConnected) return
        const margin = 8
        const rect = menu.getBoundingClientRect()
        const viewportHeight = document.documentElement.clientHeight || window.innerHeight
        const overflowBottom = Math.ceil(rect.bottom - (viewportHeight - margin))
        if (overflowBottom <= 0) return
        const shift = Math.min(overflowBottom, Math.max(0, rect.top - margin))
        if (shift <= 0) return
        const currentMarginTop = Number.parseFloat(getComputedStyle(menu).marginTop) || 0
        menu.style.setProperty('margin-top', (currentMarginTop - shift) + 'px', 'important')
      }
      requestAnimationFrame(() => {
        fit()
        requestAnimationFrame(fit)
      })
    }

    const enhance = async () => {
      const found = findMenu()
      if (!found || found.menu.querySelector('[' + menuAttr + ']')) return
      const { menu, price } = found
      const marker = document.createElement('span')
      marker.setAttribute(menuAttr, 'frame-loading')
      marker.style.display = 'none'
      menu.appendChild(marker)
      const options = await requestTop(
        'trade-relay-chart-tp-options', { price }, 'trade-relay-chart-tp-options-result'
      )
      if (!menu.isConnected || !/(?:Copy price|复制价格)\\s*[\\d,]+/i.test(String(menu.innerText || ''))) {
        marker.remove()
        return
      }
      if (!options?.ok || !Array.isArray(options.positions) || (options.positions.length === 0 && !options.quickLimit && !options.quickStop)) {
        marker.remove()
        return
      }
      marker.remove()
      const group = document.createElement('div')
      group.setAttribute(menuAttr, 'frame-watcher')
      Object.assign(group.style, {
        borderTop: '1px solid #363a45', borderBottom: '1px solid #363a45', padding: '4px 0'
      })
      let lifetimeCheck
      let maxLifetime
      const onOutsidePointer = (event) => { if (!group.contains(event.target)) setTimeout(cleanup, 0) }
      const onEscape = (event) => { if (event.key === 'Escape') cleanup() }
      const cleanup = () => {
        clearInterval(lifetimeCheck)
        clearTimeout(maxLifetime)
        window.removeEventListener('pointerdown', onOutsidePointer, true)
        window.removeEventListener('keydown', onEscape, true)
        group.remove()
      }
      lifetimeCheck = setInterval(() => {
        if (!group.isConnected || !/(?:Copy price|复制价格)\\s*[\\d,]+/i.test(String(menu.innerText || ''))) cleanup()
      }, 100)
      maxLifetime = setTimeout(cleanup, 15000)
      window.addEventListener('pointerdown', onOutsidePointer, true)
      window.addEventListener('keydown', onEscape, true)
      for (const position of options.positions) {
        const locale = options.locale === 'en' ? 'en' : 'zh-CN'
        const item = document.createElement('div')
        item.setAttribute('role', 'menuitem')
        item.tabIndex = 0
        const actionLabel = locale === 'en'
          ? (position.side === 'LONG' ? 'Sell Take-Profit Limit' : 'Buy Take-Profit Limit')
          : (position.side === 'LONG' ? '卖出限价止盈' : '买入限价止盈')
        item.textContent = '◎  ' + actionLabel + ' · ' + position.quantity + ' ' + options.symbol + ' @ ' + price
        Object.assign(item.style, {
          display: 'flex', alignItems: 'center', minHeight: '42px', padding: '0 16px',
          color: '#d1d4dc', background: '#1e1e1e', cursor: 'pointer', whiteSpace: 'nowrap',
          font: '14px/1.3 -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif'
        })
        item.addEventListener('mouseenter', () => { item.style.background = '#2a2e39' })
        item.addEventListener('mouseleave', () => { item.style.background = '#1e1e1e' })
        item.addEventListener('mousedown', (event) => { event.preventDefault(); event.stopPropagation() })
        item.addEventListener('click', async (event) => {
          event.preventDefault()
          event.stopPropagation()
          const confirmation = await requestTop('trade-relay-chart-tp-confirm', {
            locale,
            side: position.side,
            quantity: position.quantity,
            symbol: options.symbol,
            price,
            actionLabel
          }, 'trade-relay-chart-tp-confirm-result')
          if (!confirmation?.confirmed) return
          const placed = await requestTop('trade-relay-chart-tp-place', {
            positionId: position.id,
            symbol: options.symbol,
            price,
            menuToken: options.menuToken
          }, 'trade-relay-chart-tp-result')
          const message = placed?.ok
            ? (locale === 'en' ? 'Take-profit limit placed at ' + price : '止盈限价单已挂出：' + price)
            : (locale === 'en' ? 'Failed: ' + (placed?.reason || 'unknown error') : '下单失败：' + (placed?.reason || '未知错误'))
          toast(message, Boolean(placed?.ok))
          menu.remove()
        })
        group.appendChild(item)
      }
      if (options.quickLimit) {
        const limit = options.quickLimit
        const locale = options.locale === 'en' ? 'en' : 'zh-CN'
        const actionLabel = limit.side === 'BUY' ? 'Buy Limit' : 'Sell Limit'
        const displayedQuantity = Number(limit.inputQuantity) > 0 ? limit.inputQuantity : limit.quantity
        const displayedAsset = limit.sizeUnit === 'BASE' ? options.baseAsset : options.quoteAsset
        const item = document.createElement('div')
        item.setAttribute('role', 'menuitem')
        item.tabIndex = 0
        item.textContent = '◎  ' + actionLabel
          + (displayedQuantity ? ' · ' + displayedQuantity + ' ' + displayedAsset : '')
          + ' @ ' + price
        Object.assign(item.style, {
          display: 'flex', alignItems: 'center', minHeight: '42px', padding: '0 16px',
          color: '#d1d4dc', background: '#1e1e1e', cursor: 'pointer', whiteSpace: 'nowrap',
          font: '14px/1.3 -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif'
        })
        item.addEventListener('mouseenter', () => { item.style.background = '#2a2e39' })
        item.addEventListener('mouseleave', () => { item.style.background = '#1e1e1e' })
        item.addEventListener('mousedown', (event) => { event.preventDefault(); event.stopPropagation() })
        item.addEventListener('click', async (event) => {
          event.preventDefault()
          event.stopPropagation()
          const confirmation = await requestTop('trade-relay-chart-tp-confirm', {
            locale,
            side: limit.side,
            quantity: limit.quantity,
            inputQuantity: limit.inputQuantity,
            sizeUnit: limit.sizeUnit,
            currentPrice: price,
            baseAsset: options.baseAsset,
            quoteAsset: options.quoteAsset,
            symbol: options.symbol,
            price,
            actionLabel,
            kind: 'limit'
          }, 'trade-relay-chart-tp-confirm-result')
          if (!confirmation?.confirmed) return
          const placed = await requestTop('trade-relay-chart-limit-place', {
            side: limit.side,
            symbol: options.symbol,
            price,
            menuToken: options.menuToken,
            inputQuantity: confirmation.inputQuantity,
            sizeUnit: confirmation.sizeUnit
          }, 'trade-relay-chart-limit-result')
          const message = placed?.ok
            ? (locale === 'en' ? actionLabel + ' placed at ' + price : actionLabel + ' 已挂出：' + price)
            : (locale === 'en' ? 'Failed: ' + (placed?.reason || 'unknown error') : '下单失败：' + (placed?.reason || '未知错误'))
          toast(message, Boolean(placed?.ok))
          menu.remove()
        })
        group.appendChild(item)
      }
      if (options.quickStop) {
        const stop = options.quickStop
        const locale = options.locale === 'en' ? 'en' : 'zh-CN'
        const actionLabel = stop.side === 'BUY' ? 'Buy Stop' : 'Sell Stop'
        const displayedQuantity = Number(stop.inputQuantity) > 0 ? stop.inputQuantity : stop.quantity
        const displayedAsset = stop.sizeUnit === 'BASE' ? options.baseAsset : options.quoteAsset
        const item = document.createElement('div')
        item.setAttribute('role', 'menuitem')
        item.tabIndex = 0
        item.textContent = '◎  ' + actionLabel
          + (displayedQuantity ? ' · ' + displayedQuantity + ' ' + displayedAsset : '')
          + ' @ ' + price
        Object.assign(item.style, {
          display: 'flex', alignItems: 'center', minHeight: '42px', padding: '0 16px',
          color: '#d1d4dc', background: '#1e1e1e', cursor: 'pointer', whiteSpace: 'nowrap',
          font: '14px/1.3 -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif'
        })
        item.addEventListener('mouseenter', () => { item.style.background = '#2a2e39' })
        item.addEventListener('mouseleave', () => { item.style.background = '#1e1e1e' })
        item.addEventListener('mousedown', (event) => { event.preventDefault(); event.stopPropagation() })
        item.addEventListener('click', async (event) => {
          event.preventDefault()
          event.stopPropagation()
          const confirmation = await requestTop('trade-relay-chart-tp-confirm', {
            locale,
            side: stop.side,
            quantity: stop.quantity,
            inputQuantity: stop.inputQuantity,
            sizeUnit: stop.sizeUnit,
            currentPrice: options.currentPrice,
            baseAsset: options.baseAsset,
            quoteAsset: options.quoteAsset,
            symbol: options.symbol,
            price,
            actionLabel,
            kind: 'stopMarket'
          }, 'trade-relay-chart-tp-confirm-result')
          if (!confirmation?.confirmed) return
          const placed = await requestTop('trade-relay-chart-stop-place', {
            side: stop.side,
            symbol: options.symbol,
            price,
            menuToken: options.menuToken,
            inputQuantity: confirmation.inputQuantity,
            sizeUnit: confirmation.sizeUnit
          }, 'trade-relay-chart-stop-result')
          const message = placed?.ok
            ? (locale === 'en' ? actionLabel + ' placed at ' + price : actionLabel + ' 已挂出：' + price)
            : (locale === 'en' ? 'Failed: ' + (placed?.reason || 'unknown error') : '下单失败：' + (placed?.reason || '未知错误'))
          toast(message, Boolean(placed?.ok))
          menu.remove()
        })
        group.appendChild(item)
      }
      const orderItems = Array.from(menu.querySelectorAll('[role="menuitem"]'))
      const anchor = orderItems.find((node) => /Add order|添加订单/i.test(String(node.innerText || '')))
      if (anchor) anchor.after(group)
      else menu.appendChild(group)
      fitMenuInViewport(menu)
    }

    window.addEventListener('contextmenu', () => {
      for (const stale of document.querySelectorAll('[' + menuAttr + ']')) stale.remove()
      for (const timer of timers) clearTimeout(timer)
      timers = [30, 80, 160, 320].map((delay) => setTimeout(() => { void enhance() }, delay))
    }, true)
    return true
  })()`
}

async function installChartTpWatcherInFrame(frame) {
  if (!frame || !frame.parent) return
  try {
    await frame.executeJavaScript(chartTpFrameWatcherScript(), true)
    logger.info('[CHART_TP_LIMIT] phase=frame-watcher-installed', { url: frame.url || null })
  } catch (error) {
    logger.debug('[CHART_TP_LIMIT] phase=frame-watcher-install-failed', {
      url: frame?.url || null,
      reason: error?.message || 'unknown_error',
    })
  }
}

function installChartTpWatchersInChildFrames() {
  if (!binanceView || binanceView.webContents.isDestroyed()) return
  const mainFrame = binanceView.webContents.mainFrame
  for (const frame of mainFrame.framesInSubtree) {
    if (frame.parent) void installChartTpWatcherInFrame(frame)
  }
}

ipcMain.handle('chart-take-profit-limit-options', async (event, payload = {}) => {
  if (!isBinanceViewSender(event)) return { ok: false, reason: 'invalid_sender' }
  try {
    const now = Date.now()
    for (const [key, claimedAt] of _chartTpOptionsClaims) {
      if (now - claimedAt > 1_000) _chartTpOptionsClaims.delete(key)
    }
    const claimKey = `${normalizeChartTradeSymbol(payload.symbol)}:${Number(payload.price)}`
    const existingClaim = _chartTpOptionsClaims.get(claimKey)
    if (existingClaim && now - existingClaim <= 1_000) {
      return { ok: false, reason: 'duplicate_menu_request', locale: runtimeUiLocale }
    }
    _chartTpOptionsClaims.set(claimKey, now)

    const context = await getChartTakeProfitContext(payload.symbol, payload.price)
    const menuToken = context.ok && (context.positions.length > 0 || context.quickLimit || context.quickStop)
      ? issueChartTpMenuToken(context)
      : null
    return { ...context, menuToken, locale: runtimeUiLocale }
  } catch (error) {
    logger.warn('[CHART_TP_LIMIT] phase=options-failed', { reason: error?.message || 'unknown_error' })
    return { ok: false, reason: error?.message || 'options_failed', locale: runtimeUiLocale }
  }
})

ipcMain.handle('chart-place-take-profit-limit', async (event, payload = {}) => {
  if (!isBinanceViewSender(event)) return { ok: false, reason: 'invalid_sender' }
  try {
    const menuToken = String(payload.menuToken || '')
    const tokenRecord = _chartTpMenuTokens.get(menuToken)
    const requestedPositionId = Number(payload.positionId)
    const requestedSymbol = normalizeChartTradeSymbol(payload.symbol)
    const requestedPrice = Number(payload.price)
    if (
      !tokenRecord
      || tokenRecord.expiresAt <= Date.now()
      || tokenRecord.symbol !== requestedSymbol
      || tokenRecord.price !== requestedPrice
      || !tokenRecord.positionIds.has(requestedPositionId)
    ) {
      _chartTpMenuTokens.delete(menuToken)
      return { ok: false, reason: 'menu_request_expired' }
    }
    _chartTpMenuTokens.delete(menuToken)

    const context = await getChartTakeProfitContext(payload.symbol, payload.price)
    if (!context.ok) return context
    const positionId = requestedPositionId
    const selected = context.positions.find((position) => position.id === positionId)
    if (!selected) return { ok: false, reason: 'position_or_price_no_longer_valid' }

    const token = getToken()
    const positionsResponse = await httpRequest(
      'GET',
      buildBackendPath('/api/positions', { status: 'OPEN' }),
      null,
      token,
    )
    const currentPosition = Array.isArray(positionsResponse.body)
      ? positionsResponse.body.find((position) => Number(position?.id) === positionId)
      : null
    if (!currentPosition || normalizeChartTradeSymbol(currentPosition.symbol) !== context.symbol) {
      return { ok: false, reason: 'position_not_found' }
    }

    const response = await httpRequest(
      'POST',
      `/api/positions/${positionId}/tpsl`,
      {
        tp_price: context.price,
        sl_price: Number(currentPosition.sl_price) > 0 ? Number(currentPosition.sl_price) : null,
        tp_order_type: 'LIMIT',
      },
      token,
    )
    if (response.status < 200 || response.status >= 300) {
      return { ok: false, reason: response.body?.detail || `request_failed_${response.status}` }
    }
    logger.info('[CHART_TP_LIMIT] phase=placed', {
      positionId,
      symbol: context.symbol,
      side: selected.side,
      price: context.price,
      quantity: selected.quantity,
    })
    return { ok: true, position: selected, price: context.price }
  } catch (error) {
    logger.warn('[CHART_TP_LIMIT] phase=place-failed', { reason: error?.message || 'unknown_error' })
    return { ok: false, reason: error?.message || 'place_failed' }
  }
})

ipcMain.handle('chart-place-limit', async (event, payload = {}) => {
  if (!isBinanceViewSender(event)) return { ok: false, reason: 'invalid_sender' }
  try {
    const menuToken = String(payload.menuToken || '')
    const tokenRecord = _chartTpMenuTokens.get(menuToken)
    const requestedSymbol = normalizeChartTradeSymbol(payload.symbol)
    const requestedPrice = Number(payload.price)
    const requestedSide = String(payload.side || '').toUpperCase()
    const quickLimit = tokenRecord?.quickLimit
    if (
      !tokenRecord
      || tokenRecord.expiresAt <= Date.now()
      || tokenRecord.symbol !== requestedSymbol
      || tokenRecord.price !== requestedPrice
      || !quickLimit
      || quickLimit.side !== requestedSide
    ) {
      _chartTpMenuTokens.delete(menuToken)
      return { ok: false, reason: 'menu_request_expired' }
    }
    _chartTpMenuTokens.delete(menuToken)

    const context = await getChartTakeProfitContext(requestedSymbol, requestedPrice)
    if (!context.ok) return context
    if (!context.quickLimit || context.quickLimit.side !== requestedSide) {
      return { ok: false, reason: 'limit_price_no_longer_valid' }
    }
    const sizeUnit = payload.sizeUnit === 'BASE' ? 'BASE' : payload.sizeUnit === 'QUOTE' ? 'QUOTE' : null
    const quantity = sizeUnit
      ? resolveBaseQuantity(payload.inputQuantity, sizeUnit, context.price)
      : null
    if (!(Number(quantity) > 0)) return { ok: false, reason: 'invalid_order_quantity' }
    const token = getToken()
    const response = await httpRequest(
      'POST',
      '/api/orders',
      {
        symbol: context.symbol,
        side: requestedSide,
        order_type: 'LIMIT',
        quantity,
        price: context.price,
        leverage: Number(quickLimit.leverage) || 10,
        position_direction: 'OPEN',
        position_mode: quickLimit.positionMode || undefined,
      },
      token,
    )
    if (response.status < 200 || response.status >= 300) {
      return { ok: false, reason: response.body?.detail || `request_failed_${response.status}` }
    }
    saveChartQuickOrderPreference(
      decodeJwtPayload(token)?.username,
      context.symbol,
      payload.inputQuantity,
      sizeUnit,
    )
    logger.info('[CHART_LIMIT] phase=placed', {
      symbol: context.symbol,
      side: requestedSide,
      price: context.price,
      quantity,
      inputQuantity: Number(payload.inputQuantity),
      sizeUnit,
      orderId: response.body?.order_id || null,
    })
    return {
      ok: true,
      side: requestedSide,
      symbol: context.symbol,
      price: context.price,
      quantity,
      orderId: response.body?.order_id || null,
    }
  } catch (error) {
    logger.warn('[CHART_LIMIT] phase=place-failed', { reason: error?.message || 'unknown_error' })
    return { ok: false, reason: error?.message || 'place_failed' }
  }
})

ipcMain.handle('chart-place-stop-market', async (event, payload = {}) => {
  if (!isBinanceViewSender(event)) return { ok: false, reason: 'invalid_sender' }
  try {
    const menuToken = String(payload.menuToken || '')
    const tokenRecord = _chartTpMenuTokens.get(menuToken)
    const requestedSymbol = normalizeChartTradeSymbol(payload.symbol)
    const requestedPrice = Number(payload.price)
    const requestedSide = String(payload.side || '').toUpperCase()
    const quickStop = tokenRecord?.quickStop
    if (
      !tokenRecord
      || tokenRecord.expiresAt <= Date.now()
      || tokenRecord.symbol !== requestedSymbol
      || tokenRecord.price !== requestedPrice
      || !quickStop
      || quickStop.side !== requestedSide
    ) {
      _chartTpMenuTokens.delete(menuToken)
      return { ok: false, reason: 'menu_request_expired' }
    }
    _chartTpMenuTokens.delete(menuToken)

    const context = await getChartTakeProfitContext(requestedSymbol, requestedPrice)
    if (!context.ok) return context
    if (!context.quickStop || context.quickStop.side !== requestedSide) {
      return { ok: false, reason: 'trigger_price_no_longer_valid' }
    }
    const sizeUnit = payload.sizeUnit === 'BASE' ? 'BASE' : payload.sizeUnit === 'QUOTE' ? 'QUOTE' : null
    const quantity = sizeUnit
      ? resolveBaseQuantity(payload.inputQuantity, sizeUnit, context.currentPrice)
      : null
    if (!(Number(quantity) > 0)) return { ok: false, reason: 'invalid_order_quantity' }
    const token = getToken()
    const response = await httpRequest(
      'POST',
      '/api/orders',
      {
        symbol: context.symbol,
        side: requestedSide,
        order_type: 'STOP_MARKET',
        quantity,
        stop_price: context.price,
        leverage: Number(quickStop.leverage) || 10,
        position_direction: 'OPEN',
      },
      token,
    )
    if (response.status < 200 || response.status >= 300) {
      return { ok: false, reason: response.body?.detail || `request_failed_${response.status}` }
    }
    saveChartQuickOrderPreference(
      decodeJwtPayload(token)?.username,
      context.symbol,
      payload.inputQuantity,
      sizeUnit,
    )
    logger.info('[CHART_STOP_MARKET] phase=placed', {
      symbol: context.symbol,
      side: requestedSide,
      triggerPrice: context.price,
      quantity,
      inputQuantity: Number(payload.inputQuantity),
      sizeUnit,
      orderId: response.body?.order_id || null,
    })
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('chart-active-orders-refresh', context.symbol)
    }
    return {
      ok: true,
      side: requestedSide,
      symbol: context.symbol,
      triggerPrice: context.price,
      quantity,
      orderId: response.body?.order_id || null,
    }
  } catch (error) {
    logger.warn('[CHART_STOP_MARKET] phase=place-failed', { reason: error?.message || 'unknown_error' })
    return { ok: false, reason: error?.message || 'place_failed' }
  }
})

ipcMain.handle('chart-cancel-active-order', async (event, payload = {}) => {
  if (!isBinanceViewSender(event)) return { ok: false, reason: 'invalid_sender' }
  try {
    const cancelKind = payload.cancelKind === 'conditional' ? 'conditional' : 'basic'
    const orderId = Number(payload.orderId)
    const algoId = Number(payload.algoId)
    const symbol = normalizeChartTradeSymbol(payload.symbol)
    const exchangeOrderId = String(payload.exchangeOrderId || '').trim()
    if (
      !symbol
      || (cancelKind === 'basic' && (!Number.isInteger(orderId) || orderId <= 0 || !exchangeOrderId))
      || (cancelKind === 'conditional' && (!Number.isInteger(algoId) || algoId <= 0))
    ) {
      return { ok: false, reason: 'invalid_request' }
    }

    // Only permit cancellation of an order that the authenticated renderer
    // recently supplied for the active chart. Never trust an arbitrary order ID
    // originating from the remote Binance page.
    const activeOrders = Array.isArray(_activeOrderLinesState?.orders)
      ? _activeOrderLinesState.orders
      : []
    const activeOrder = activeOrders.find((order) => {
      if (normalizeChartTradeSymbol(order?.symbol) !== symbol) return false
      if (cancelKind === 'conditional') {
        return order?.cancel_kind === 'conditional' && Number(order?.algo_id) === algoId
      }
      return order?.cancel_kind !== 'conditional'
        && Number(order?.id) === orderId
        && String(order?.exchange_order_id || '').trim() === exchangeOrderId
    })
    if (!activeOrder) return { ok: false, reason: 'order_no_longer_active' }

    const token = getToken()
    if (!token) return { ok: false, reason: 'not_authenticated' }
    const response = cancelKind === 'conditional'
      ? await httpRequest('POST', '/api/orders/conditional/cancel', { algo_id: algoId }, token)
      : await httpRequest(
        'POST',
        `/api/orders/${orderId}/cancel`,
        { symbol, exchange_order_id: exchangeOrderId },
        token,
      )
    if (response.status < 200 || response.status >= 300) {
      return { ok: false, reason: response.body?.detail || `request_failed_${response.status}` }
    }

    _activeOrderLinesState = {
      ..._activeOrderLinesState,
      orders: activeOrders.filter((order) => order !== activeOrder),
    }
    logger.info('[ACTIVE_ORDER_LINES] phase=chart-cancel-success', {
      orderId,
      algoId: cancelKind === 'conditional' ? algoId : null,
      cancelKind,
      symbol,
      exchangeOrderId,
    })
    return { ok: true, orderId, algoId: cancelKind === 'conditional' ? algoId : null }
  } catch (error) {
    logger.warn('[ACTIVE_ORDER_LINES] phase=chart-cancel-failed', {
      orderId: Number(payload.orderId) || null,
      reason: error?.message || 'unknown_error',
    })
    return { ok: false, reason: error?.message || 'cancel_failed' }
  }
})

ipcMain.handle('get-tv-klines', async (_event, symbol, interval, limit) => {
  if (!binanceView) return null
  try {
    return await binanceView.webContents.executeJavaScript(
      `window.__omnitrader.getCachedKlines(${JSON.stringify(symbol)}, ${JSON.stringify(interval)}, ${Number(limit) || 500})`
    )
  } catch { return null }
})

ipcMain.handle('set-chart-overlay-signals', async (_event, signals, locale) => {
  if (!binanceView) return { ok: false, reason: 'no_view' }
  const normalizedSignals = Array.isArray(signals) ? signals : []
  try {
    logger.info('[OVERLAY_IPC] phase=send', { action: 'signals', count: normalizedSignals.length, locale: locale || null })
    binanceView.webContents.send('overlay-signals', normalizedSignals, locale)
    return { ok: true, count: normalizedSignals.length }
  } catch (error) {
    logger.warn('[OVERLAY_IPC] phase=send-failed', { action: 'signals', reason: error?.message || 'overlay_send_failed' })
    return { ok: false, reason: error?.message || 'overlay_send_failed' }
  }
})

ipcMain.handle('clear-chart-overlay-signals', async () => {
  if (!binanceView) return { ok: false, reason: 'no_view' }
  try {
    logger.info('[OVERLAY_IPC] phase=send', { action: 'clear' })
    binanceView.webContents.send('overlay-clear')
    return { ok: true }
  } catch (error) {
    logger.warn('[OVERLAY_IPC] phase=send-failed', { action: 'clear', reason: error?.message || 'overlay_clear_failed' })
    return { ok: false, reason: error?.message || 'overlay_clear_failed' }
  }
})

ipcMain.handle('set-chart-active-order-lines', async (_event, orders, locale) => {
  const normalizedOrders = Array.isArray(orders) ? orders : []
  _activeOrderLinesState = { orders: normalizedOrders, locale }
  return sendActiveOrderLinesToChart(normalizedOrders, locale, 'renderer-sync')
})

ipcMain.handle('clear-chart-active-order-lines', async () => {
  _activeOrderLinesState = null
  if (!binanceView) return { ok: false, reason: 'no_view' }
  try {
    binanceView.webContents.send('active-order-lines-clear')
    return { ok: true }
  } catch (error) {
    return { ok: false, reason: error?.message || 'send_failed' }
  }
})

ipcMain.handle('clear-all-chart-drawings', async () => {
  if (!binanceView) return { ok: false, reason: 'no_view' }
  try {
    logger.info('[OVERLAY_IPC] phase=send', { action: 'clear-all' })
    binanceView.webContents.send('overlay-clear-all')
    return { ok: true }
  } catch (error) {
    logger.warn('[OVERLAY_IPC] phase=send-failed', { action: 'clear-all', reason: error?.message || 'overlay_clear_all_failed' })
    return { ok: false, reason: error?.message || 'overlay_clear_all_failed' }
  }
})

ipcMain.handle('debug-probe-chart-overlay', async () => {
  if (!binanceView) return { action: 'probe', ok: false, reason: 'no_view' }
  try {
    logger.info('[OVERLAY_IPC] phase=send', { action: 'probe' })
    const waitResult = waitForOverlayStatus('probe')
    binanceView.webContents.send('overlay-probe')
    return await waitResult
  } catch (error) {
    logger.warn('[OVERLAY_IPC] phase=send-failed', { action: 'probe', reason: error?.message || 'probe_failed' })
    return { action: 'probe', ok: false, reason: error?.message || 'probe_failed' }
  }
})

ipcMain.handle('debug-clear-chart-overlay-signals', async () => {
  if (!binanceView) return { action: 'clear-debug', ok: false, reason: 'no_view' }
  try {
    logger.info('[OVERLAY_IPC] phase=send', { action: 'clear-debug' })
    const waitResult = waitForOverlayStatus('clear-debug')
    binanceView.webContents.send('overlay-clear-debug')
    return await waitResult
  } catch (error) {
    logger.warn('[OVERLAY_IPC] phase=send-failed', { action: 'clear-debug', reason: error?.message || 'clear_debug_failed' })
    return { action: 'clear-debug', ok: false, reason: error?.message || 'clear_debug_failed' }
  }
})

// Nuclear option: executeJavaScript directly in the Binance page context.
// Bypasses the preload IPC entirely, useful for debugging.
ipcMain.handle('force-clear-chart-arrows', async () => {
  if (!binanceView) return { ok: false, reason: 'no_view' }
  try {
    const result = await binanceView.webContents.executeJavaScript(`
      (function() {
        try {
          // Try the preload's exposed debug object first
          if (window.__tradeRelayDebug && typeof window.__tradeRelayDebug.clearAll === 'function') {
            window.__tradeRelayDebug.clearAll()
            return 'debug_clearAll_ok'
          }
          // Walk TradingView widget objects looking for removeAllShapes
          let found = false
          const keys = Object.keys(window).filter(k => k.startsWith('TV') || k.startsWith('tv') || k.includes('widget'))
          for (const k of keys) {
            try {
              const w = window[k]
              if (w && typeof w.removeAllShapes === 'function') { w.removeAllShapes(); found = true }
              if (w && typeof w.activeChart === 'function') {
                const c = w.activeChart()
                if (c && typeof c.removeAllShapes === 'function') { c.removeAllShapes(); found = true }
              }
              if (w && typeof w.chart === 'function') {
                const c = w.chart()
                if (c && typeof c.removeAllShapes === 'function') { c.removeAllShapes(); found = true }
              }
            } catch(e) {}
          }
          return found ? 'widget_scan_ok' : 'no_widget_found'
        } catch(e) { return 'error:' + e.message }
      })()
    `)
    return { ok: true, result }
  } catch (error) {
    return { ok: false, reason: error?.message || 'executeJS_failed' }
  }
})

ipcMain.handle('open-binance-devtools', () => { if (binanceView) binanceView.webContents.openDevTools({ mode: 'detach' }) })

ipcMain.handle('open-external', (_event, url) => {
  let parsed
  try { parsed = new URL(url) } catch { return Promise.reject(new Error('Invalid URL')) }
  if (!['https:', 'http:'].includes(parsed.protocol)) return Promise.reject(new Error('Only http/https'))
  const safeUrl = parsed.toString()
  const candidates = process.platform === 'win32'
    ? ['C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe']
    : process.platform === 'darwin'
    ? ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
    : ['google-chrome', 'google-chrome-stable', 'chromium-browser', 'chromium']
  const tryNext = (i) => {
    if (i >= candidates.length) return shell.openExternal(safeUrl)
    if (process.platform === 'linux') {
      exec(`which "${candidates[i]}" 2>/dev/null`, (err, stdout) => {
        if (!err && stdout.trim()) exec(`"${candidates[i]}" "${safeUrl}"`, (e) => { if (e) tryNext(i + 1) })
        else tryNext(i + 1)
      })
    } else execFile(candidates[i], [safeUrl], (err) => { if (err) tryNext(i + 1) })
  }
  tryNext(0)
  return Promise.resolve()
})

ipcMain.handle('minimize-window', () => mainWindow?.minimize())
ipcMain.handle('maximize-window', () => {
  if (mainWindow?.isMaximized()) mainWindow.unmaximize()
  else mainWindow?.maximize()
})
ipcMain.handle('close-window', () => mainWindow?.close())
ipcMain.handle('open-order-kline-window', (event, payload) => {
  if (!payload || typeof payload !== 'object' || !String(payload.symbol || '').trim()) {
    throw new Error('Invalid order kline payload')
  }
  openOrderKlineWindow(payload, BrowserWindow.fromWebContents(event.sender))
  return { ok: true }
})
ipcMain.handle('get-order-kline-payload', () => orderKlinePayload)
ipcMain.handle('close-order-kline-window', (event) => {
  const senderWindow = BrowserWindow.fromWebContents(event.sender)
  if (senderWindow && senderWindow === orderKlineWindow) senderWindow.close()
})
ipcMain.handle('position-review-saved', (_event, positionId) => {
  const normalizedPositionId = Number(positionId)
  if (!Number.isSafeInteger(normalizedPositionId) || normalizedPositionId <= 0) {
    throw new Error('Invalid position ID')
  }
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('position-review-saved', normalizedPositionId)
  }
  return { ok: true }
})

// ── Lifecycle ─────────────────────────────────────────────────────────────────
// On Linux with fractional HiDPI scaling (e.g. 125 / 150 %), Chromium may
// misreport mouse coordinates inside BrowserView, causing the TradingView
// crosshair to appear offset from the actual cursor position.
// Forcing device-scale-factor=1 lets Electron handle scaling correctly.
if (process.platform === 'linux') {
  app.commandLine.appendSwitch('force-device-scale-factor', '1')
}

app.whenReady().then(() => {
  logger.info('Trade Relay starting up', { logFile: logger.getLogFile() })
  logger.info('[market-data] using renderer BrowserView + REST polling for mark price and funding')
  createMainWindow()
  setTimeout(() => { createBinanceView(); updateBinanceViewBounds() }, 1500)
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createMainWindow() })
})

app.on('window-all-closed', () => {
  logger.info('All windows closed, quitting')
  logger.close()
  if (process.platform !== 'darwin') app.quit()
})

process.on('uncaughtException', (err) => {
  logger.error('Uncaught exception', { message: err.message, stack: err.stack })
})

process.on('unhandledRejection', (reason) => {
  logger.error('Unhandled rejection', { reason: String(reason) })
})
