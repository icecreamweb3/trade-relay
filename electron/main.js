/**
 * Trade Relay — Electron Main Process (Electron + React + BrowserView architecture)
 * Replaced Qt6 + TCP NDJSON bridge with standard Electron IPC.
 */
const { app, BrowserWindow, BrowserView, ipcMain, safeStorage, shell } = require('electron')
const WebSocket = require('ws')
const path = require('path')
const { execFile, exec } = require('child_process')
const http = require('http')
require('dotenv').config({ path: path.join(__dirname, '../.env') })
const { logger } = require('./logger')

const isDev = process.env.NODE_ENV === 'development'

let mainWindow = null
let binanceView = null
let _autoExpandDone = false
let _splitRatio = 0.67   // default left panel 67% horizontal
let _chartRatio = 0.65   // default chart 65% vertical within left panel

// Map TRADE_RELAY_LANG (zh|en) → Binance locale path segment
const _trLang = (process.env.TRADE_RELAY_LANG || '').toLowerCase()
const _defaultBinanceLang = _trLang === 'en' ? 'en' : _trLang === 'zh' ? 'zh-CN' : 'zh-CN'
const BINANCE_LANG   = process.env.BINANCE_LANG   || _defaultBinanceLang
const UI_LANG        = process.env.UI_LANG        || _defaultBinanceLang
const BINANCE_SYMBOL = process.env.BINANCE_SYMBOL || 'BTCUSDT'
const BACKEND_PORT   = process.env.BACKEND_PORT   || '8000'
const BINANCE_URL    = `https://www.binance.com/${BINANCE_LANG}/futures/${BINANCE_SYMBOL}`

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

// ── HTTP helper for localhost backend ─────────────────────────────────────────
function httpRequest(method, path, body, token) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null
    const headers = { 'Content-Type': 'application/json' }
    if (token) headers['Authorization'] = `Bearer ${token}`
    if (data) headers['Content-Length'] = Buffer.byteLength(data)

    const req = http.request(
      { hostname: '127.0.0.1', port: BACKEND_PORT, path, method, headers },
      (res) => {
        let chunks = ''
        res.on('data', d => (chunks += d))
        res.on('end', () => {
          try { resolve({ status: res.statusCode, body: JSON.parse(chunks) }) }
          catch { resolve({ status: res.statusCode, body: chunks }) }
        })
      }
    )
    req.on('error', reject)
    if (data) req.write(data)
    req.end()
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
    width: 1850,
    height: 1080,
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
  try {
    const res = await httpRequest('POST', '/api/auth/login', { username, password }, null)
    if (res.status === 200 && res.body.access_token) {
      storeToken(res.body.access_token)
      return { ok: true, user: res.body.user }
    }
    return { ok: false, error: res.body?.detail || 'Login failed' }
  } catch (e) {
    return { ok: false, error: `Backend unavailable: ${e.message}` }
  }
})

ipcMain.handle('auth-logout', () => { clearToken(); return { ok: true } })
ipcMain.handle('auth-get-token', () => getToken())

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
  fn.call(logger, `[renderer] ${msg}`, extra)
})

ipcMain.handle('get-ui-lang', () => UI_LANG)
ipcMain.on('get-ui-lang-sync', (event) => { event.returnValue = UI_LANG })

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

// ── markPrice WebSocket in main process (ws package, requires direct network access) ──
// Falls back gracefully if network is not reachable from Node.js context.
let _mpWs = null
let _mpSymbol = null
let _mpRetry = null
let _mpFailed = false   // stop retrying if Node.js can't reach Binance

function startMarkPriceWs(symbol) {
  if (_mpFailed) return   // Node.js network is not reachable, renderer WS is primary
  if (_mpSymbol === symbol && _mpWs) return
  stopMarkPriceWs()
  _mpSymbol = symbol
  const url = `wss://fstream.binance.com/ws/${symbol.toLowerCase()}@markPrice@1s`

  function connect() {
    if (_mpSymbol !== symbol) return
    const ws = new WebSocket(url, { handshakeTimeout: 5000 })
    _mpWs = ws

    ws.on('open', () => {
      logger.info(`[markPrice WS] connected: ${url}`)
    })

    ws.on('message', (raw) => {
      try {
        const msg = JSON.parse(raw.toString())
        if (msg.e !== 'markPriceUpdate') return
        const markPrice = parseFloat(msg.p)
        const fundingRate = parseFloat(msg.r)
        logger.info(`[markPrice WS] ${msg.s}: markPrice=${markPrice} fundingRate=${(fundingRate*100).toFixed(4)}% nextFunding=${new Date(msg.T).toISOString()}`)
        mainWindow?.webContents.send('mark-price-data', {
          type: 'markPrice',
          symbol: msg.s,
          markPrice,
          indexPrice: parseFloat(msg.i),
          fundingRate,
          nextFundingTime: msg.T,
          timestamp: msg.E,
        })
      } catch (err) {
        logger.warn(`[markPrice WS] parse error: ${err.message}`)
      }
    })

    ws.on('unexpected-response', () => {
      logger.warn('[markPrice WS] unexpected response — Node.js network unreachable, disabling main-process WS')
      _mpFailed = true
      stopMarkPriceWs()
    })

    ws.on('error', (err) => {
      logger.warn(`[markPrice WS] error: ${err.message}`)
      // If connection refused / timeout, mark as failed so we stop retrying
      if (err.code === 'ECONNREFUSED' || err.code === 'ETIMEDOUT' || err.code === 'ENOTFOUND') {
        logger.warn('[markPrice WS] Node.js cannot reach Binance — renderer WS will be primary source')
        _mpFailed = true
      }
    })

    ws.on('close', (code) => {
      if (_mpFailed) return
      logger.warn(`[markPrice WS] closed (code=${code}), reconnecting in 5s`)
      _mpRetry = setTimeout(() => { if (_mpSymbol === symbol) connect() }, 5000)
    })
  }

  connect()
}

function stopMarkPriceWs() {
  if (_mpRetry) { clearTimeout(_mpRetry); _mpRetry = null }
  try { _mpWs?.close() } catch {}
  _mpWs = null
  _mpSymbol = null
}

// Allow renderer to switch the markPrice symbol when user changes trading pair
ipcMain.on('switch-mark-price-symbol', (_event, symbol) => {
  startMarkPriceWs(symbol)
})

// ── Lifecycle ─────────────────────────────────────────────────────────────────
app.whenReady().then(() => {
  logger.info('Trade Relay starting up', { logFile: logger.getLogFile() })
  createMainWindow()
  startMarkPriceWs(BINANCE_SYMBOL)
  setTimeout(() => { createBinanceView(); updateBinanceViewBounds() }, 1500)
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createMainWindow() })
})

app.on('window-all-closed', () => {
  logger.info('All windows closed, quitting')
  stopMarkPriceWs()
  logger.close()
  if (process.platform !== 'darwin') app.quit()
})

process.on('uncaughtException', (err) => {
  logger.error('Uncaught exception', { message: err.message, stack: err.stack })
})

process.on('unhandledRejection', (reason) => {
  logger.error('Unhandled rejection', { reason: String(reason) })
})
