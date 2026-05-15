// Global typings for Electron IPC bridge exposed via contextBridge
interface ElectronAPI {
  uiLang?: string
  backendBaseUrl?: string
  getUILang: () => Promise<string>
  getBackendBaseUrl: () => Promise<string>

  // Auth
  login: (username: string, password: string) => Promise<{ ok: boolean; user?: import('./store/authStore').UserInfo; error?: string }>
  logout: () => Promise<{ ok: boolean }>
  getToken: () => Promise<string | null>
  backendRequest: (
    method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
    path: string,
    options?: {
      body?: unknown
      query?: Record<string, string | number | boolean | null | undefined | Array<string | number | boolean>>
    }
  ) => Promise<{ status: number; body: unknown }>
  getAuthStatus: () => Promise<{ authenticated: boolean; user?: import('./store/authStore').UserInfo }>

  // Window
  minimizeWindow: () => Promise<void>
  maximizeWindow: () => Promise<void>
  closeWindow: () => Promise<void>

  // Binance
  resizeBinancePanel: (splitRatio: number, chartRatio?: number) => Promise<void>
  navigateBinance: (symbol: string) => Promise<void>
  switchChartSymbol: (symbol: string) => Promise<boolean>
  binanceGoBack: () => Promise<void>
  binanceGoForward: () => Promise<void>
  binanceReload: () => Promise<void>
  setBinanceViewVisible: (visible: boolean) => Promise<void>
  openBinanceDevTools: () => Promise<void>

  // Market data events
  onMarketData: (callback: (data: import('./store/marketStore').MarketEvent) => void) => () => void
  onSymbolChange: (callback: (symbol: string) => void) => () => void
  onIntervalChange: (callback: (interval: string) => void) => () => void
  onChartExpandChange: (callback: (expanded: boolean) => void) => () => void

  // Chart
  chartToggleFullscreen?: () => Promise<{ ok: boolean }>
  getTvKlines?: (symbol: string, interval: string, limit?: number) => Promise<unknown[] | null>
  openExternal: (url: string) => Promise<void>

  // Renderer → main log forwarding
  logToMain?: (level: 'debug' | 'info' | 'warn' | 'error', msg: string, extra?: Record<string, unknown>) => void
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI
  }
}

export {}
