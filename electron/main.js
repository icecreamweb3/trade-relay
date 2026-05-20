/**
 * Trade Relay — Electron Main Process (Electron + React + BrowserView architecture)
 * Replaced Qt6 + TCP NDJSON bridge with standard Electron IPC.
 */
const { app, BrowserWindow, BrowserView, ipcMain, safeStorage, shell } = require('electron')
const fs = require('fs')
const path = require('path')
const { execFile, exec } = require('child_process')
const http = require('http')
const https = require('https')
const { ProxyAgent } = require('proxy-agent')
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
let binanceView = null
let _autoExpandDone = false
let _splitRatio = 0.60   // default left panel 60% horizontal
let _chartRatio = 0.65   // default chart 65% vertical within left panel

// Map TRADE_RELAY_LANG (zh|en) → Binance locale path segment
const _trLang = (process.env.TRADE_RELAY_LANG || '').toLowerCase()
const _defaultBinanceLang = _trLang === 'en' ? 'en' : _trLang === 'zh' ? 'zh-CN' : 'zh-CN'
const BINANCE_LANG   = process.env.BINANCE_LANG   || _defaultBinanceLang
const UI_LANG        = process.env.UI_LANG        || _defaultBinanceLang
const BINANCE_SYMBOL = process.env.BINANCE_SYMBOL || 'BTCUSDC'
const BACKEND_PORT   = process.env.BACKEND_PORT   || '8000'
const BACKEND_BASE_URL = normalizeBaseUrl(
  process.env.TRADE_RELAY_API_BASE_URL
  || process.env.BACKEND_BASE_URL
  || `http://127.0.0.1:${BACKEND_PORT}`
)
const BINANCE_URL    = `https://www.binance.com/${BINANCE_LANG}/futures/${BINANCE_SYMBOL}`

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

function storeToken(token) {
  if (safeStorage.isEncryptionAvailable()) {
    try { _tokenStore = safeStorage.encryptString(token).toString('base64') } catch { _tokenStore = token }
  } else {
    _tokenStore = token
  }
}

function getToken() {
  if (!_tokenStore) return null
  if (safeStorage.isEncryptionAvailable()) {
    try { return safeStorage.decryptString(Buffer.from(_tokenStore, 'base64')) } catch { return _tokenStore }
  }
  return _tokenStore
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
      resolve(payload)
    }

    const handler = (event, payload) => {
      if (event.sender !== targetWebContents) return
      if (!payload || payload.action !== action) return
      finish(payload)
    }

    const timer = setTimeout(() => {
      finish({ action, ok: false, reason: 'timeout' })
    }, timeoutMs)

    ipcMain.on('overlay-status', handler)
  })
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
    mainWindow.loadURL('http://localhost:5173')
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
  }

  mainWindow.on('closed', () => { mainWindow = null })
  mainWindow.webContents.on('did-finish-load', () => updateBinanceViewBounds())
  mainWindow.on('resize', () => updateBinanceViewBounds())

  mainWindow.webContents.on('before-input-event', (_event, input) => {
    if (input.type === 'keyDown' && input.key === 'F12') {
      if (mainWindow.webContents.isDevToolsOpened()) mainWindow.webContents.closeDevTools()
      else mainWindow.webContents.openDevTools({ mode: 'detach' })
    }
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

  mainWindow.addBrowserView(binanceView)
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

function updateBinanceViewBounds() {
  if (!binanceView || !mainWindow) return
  const bounds = mainWindow.getBounds()
  const TITLEBAR_H = 36
  const STATUSBAR_H = 24
  const availH = bounds.height - TITLEBAR_H - STATUSBAR_H
  const panelWidth = Math.floor(bounds.width * _splitRatio)
  const chartHeight = Math.floor(availH * _chartRatio)
  binanceView.setBounds({
    x: 0,
    y: TITLEBAR_H,
    width: panelWidth,
    height: chartHeight,
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

ipcMain.handle('auth-logout', () => { clearToken(); return { ok: true } })
ipcMain.handle('auth-get-token', () => getToken())

ipcMain.handle('backend-request', async (_event, { method = 'GET', path, body = null, query = null }) => {
  const token = getToken()
  const requestPath = buildBackendPath(path, query)
  return httpRequest(method, requestPath, body, token)
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

ipcMain.handle('get-ui-lang', () => UI_LANG)
ipcMain.on('get-ui-lang-sync', (event) => { event.returnValue = UI_LANG })
ipcMain.handle('get-backend-base-url', () => BACKEND_BASE_URL)
ipcMain.on('get-backend-base-url-sync', (event) => { event.returnValue = BACKEND_BASE_URL })

ipcMain.handle('resize-binance-panel', (_event, splitRatio, chartRatio) => {
  if (splitRatio === 0) {
    // Hide BrowserView by moving it off-screen
    if (binanceView) binanceView.setBounds({ x: -9999, y: -9999, width: 1, height: 1 })
    return
  }
  _splitRatio = Math.max(0.1, Math.min(0.95, splitRatio))
  if (chartRatio != null) _chartRatio = Math.max(0.1, Math.min(0.95, chartRatio))
  updateBinanceViewBounds()
})

ipcMain.handle('navigate-binance', (_event, symbol) => {
  if (!binanceView) return
  const pair = symbol.toUpperCase().endsWith('USDT') ? symbol.toUpperCase() : symbol.toUpperCase() + 'USDT'
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
  if (!binanceView || !mainWindow) return
  if (visible) {
    updateBinanceViewBounds()
  } else {
    // Move off-screen to hide without destroying the view
    binanceView.setBounds({ x: -9999, y: -9999, width: 1, height: 1 })
  }
})

ipcMain.on('market-data', (event, ...args) => {
  if (!binanceView || !mainWindow || event.sender !== binanceView.webContents) return
  mainWindow.webContents.send('market-data', ...args)
})

ipcMain.on('chart-interval-change', (event, interval) => {
  if (!binanceView || !mainWindow || event.sender !== binanceView.webContents) return
  mainWindow.webContents.send('binance-interval-change', interval)
})

ipcMain.on('chart-expand-state-change', (event, expanded) => {
  if (!binanceView || !mainWindow || event.sender !== binanceView.webContents) return
  mainWindow.webContents.send('chart-expand-state-change', expanded)
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
    binanceView.webContents.send('overlay-signals', normalizedSignals, locale)
    return { ok: true, count: normalizedSignals.length }
  } catch (error) {
    return { ok: false, reason: error?.message || 'overlay_send_failed' }
  }
})

ipcMain.handle('clear-chart-overlay-signals', async () => {
  if (!binanceView) return { ok: false, reason: 'no_view' }
  try {
    binanceView.webContents.send('overlay-clear')
    return { ok: true }
  } catch (error) {
    return { ok: false, reason: error?.message || 'overlay_clear_failed' }
  }
})

ipcMain.handle('debug-probe-chart-overlay', async () => {
  if (!binanceView) return { action: 'probe', ok: false, reason: 'no_view' }
  try {
    const waitResult = waitForOverlayStatus('probe')
    binanceView.webContents.send('overlay-probe')
    return await waitResult
  } catch (error) {
    return { action: 'probe', ok: false, reason: error?.message || 'probe_failed' }
  }
})

ipcMain.handle('debug-clear-chart-overlay-signals', async () => {
  if (!binanceView) return { action: 'clear-debug', ok: false, reason: 'no_view' }
  try {
    const waitResult = waitForOverlayStatus('clear-debug')
    binanceView.webContents.send('overlay-clear-debug')
    return await waitResult
  } catch (error) {
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

// ── Lifecycle ─────────────────────────────────────────────────────────────────
// On Linux with fractional HiDPI scaling (e.g. 125 / 150 %), Chromium may
// misreport mouse coordinates inside BrowserView, causing the TradingView
// crosshair to appear offset from the actual cursor position.
// Forcing device-scale-factor=1 lets Electron handle scaling correctly.
if (process.platform === 'linux') {
  app.commandLine.appendSwitch('force-device-scale-factor', '1')
}

app.whenReady().then(() => {
  logger.info('Trade Relay starting up', { logFile: logger.getLogFile(), bootstrapFile: logger.getBootstrapFile() })
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
