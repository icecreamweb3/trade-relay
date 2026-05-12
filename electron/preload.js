/**
 * Trade Relay — Main Renderer Preload
 * Exposes IPC channels via contextBridge. Adds auth channels on top of
 * the standard Binance/chart channels from omnitrader-ai.
 */
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('electronAPI', {
  // Sync language for initial store state
  uiLang: ipcRenderer.sendSync('get-ui-lang-sync'),
  getUILang: () => ipcRenderer.invoke('get-ui-lang'),

  // ── Auth ──────────────────────────────────────────────────────────────────
  login: (username, password) => ipcRenderer.invoke('auth-login', { username, password }),
  logout: () => ipcRenderer.invoke('auth-logout'),
  getToken: () => ipcRenderer.invoke('auth-get-token'),
  getAuthStatus: () => ipcRenderer.invoke('auth-get-status'),

  // ── Window controls ───────────────────────────────────────────────────────
  minimizeWindow: () => ipcRenderer.invoke('minimize-window'),
  maximizeWindow: () => ipcRenderer.invoke('maximize-window'),
  closeWindow: () => ipcRenderer.invoke('close-window'),

  // ── Binance BrowserView ───────────────────────────────────────────────────
  resizeBinancePanel: (splitRatio, chartRatio) => ipcRenderer.invoke('resize-binance-panel', splitRatio, chartRatio),
  navigateBinance: (symbol) => ipcRenderer.invoke('navigate-binance', symbol),
  switchChartSymbol: (symbol) => ipcRenderer.invoke('switch-chart-symbol', symbol),
  binanceGoBack: () => ipcRenderer.invoke('binance-go-back'),
  binanceGoForward: () => ipcRenderer.invoke('binance-go-forward'),
  binanceReload: () => ipcRenderer.invoke('binance-reload'),
  setBinanceViewVisible: (visible) => ipcRenderer.invoke('set-binance-view-visible', visible),
  openBinanceDevTools: () => ipcRenderer.invoke('open-binance-devtools'),

  // ── Market data events ────────────────────────────────────────────────────
  onMarketData: (callback) => {
    const handler = (_event, data) => callback(data)
    ipcRenderer.on('market-data', handler)
    return () => ipcRenderer.removeListener('market-data', handler)
  },
  onSymbolChange: (callback) => {
    const handler = (_event, symbol) => callback(symbol)
    ipcRenderer.on('binance-symbol-change', handler)
    return () => ipcRenderer.removeListener('binance-symbol-change', handler)
  },
  onIntervalChange: (callback) => {
    const handler = (_event, interval) => callback(interval)
    ipcRenderer.on('binance-interval-change', handler)
    return () => ipcRenderer.removeListener('binance-interval-change', handler)
  },
  onChartExpandChange: (callback) => {
    const handler = (_event, expanded) => callback(expanded)
    ipcRenderer.on('chart-expand-state-change', handler)
    return () => ipcRenderer.removeListener('chart-expand-state-change', handler)
  },

  // ── Chart controls ────────────────────────────────────────────────────────
  chartToggleFullscreen: () => ipcRenderer.invoke('chart-toggle-fullscreen'),
  getTvKlines: (symbol, interval, limit = 500) =>
    ipcRenderer.invoke('get-tv-klines', symbol, interval, limit),
  openExternal: (url) => ipcRenderer.invoke('open-external', url),

  // ── Renderer → main log forwarding ───────────────────────────────────────
  logToMain: (level, msg, extra) => ipcRenderer.send('log-to-main', level, msg, extra),

})
