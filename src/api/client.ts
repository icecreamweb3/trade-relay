// Trade Relay API client — all calls go to FastAPI backend on port 8000
import axios from 'axios'
import { perf } from '../utils/perf'

const DEFAULT_BASE_URL = 'http://127.0.0.1:8000'

function normalizeBaseUrl(url: string): string {
  return url.replace(/\/+$/, '')
}

function resolveBaseUrl(): string {
  const electronBaseUrl = window.electronAPI?.backendBaseUrl?.trim()
  if (electronBaseUrl) return normalizeBaseUrl(electronBaseUrl)

  const viteBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim()
  if (viteBaseUrl) return normalizeBaseUrl(viteBaseUrl)

  return DEFAULT_BASE_URL
}

export function getBackendBaseUrl(): string {
  return resolveBaseUrl()
}

export function getBackendWebSocketUrl(path: string): string {
  const url = new URL(path, `${getBackendBaseUrl()}/`)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

type QueryValue = string | number | boolean | null | undefined | Array<string | number | boolean>

type ApiResult = Record<string, unknown>

interface ApiUser {
  id: number
  username: string
  role: string
  is_active: boolean
  binance_api_key?: string
  binance_api_secret?: string
  created_at?: string
  updated_at?: string
}

interface ApiOrderUser {
  id: number
  username: string
}

interface ApiPosition {
  id: number
  symbol: string
  side: string
  quantity: number
  entry_price: number
  liquidation_price: number
  unrealized_pnl: number
  leverage: number
  margin_type: string
  margin: number
  tp_price?: number | null
  sl_price?: number | null
}

interface ApiOrder {
  id: number
  symbol: string
  side: string
  order_type: string
  trade_direction?: string | null
  quantity: number
  filled_qty?: number
  price: number
  avg_price?: number | null
  realized_pnl?: number | null
  commission?: number | null
  commission_asset?: string | null
  stop_price?: number | null
  reduce_only?: boolean
  post_only?: boolean
  status: string
  username?: string
  exchange_order_id?: string
  created_at?: string
  error_message?: string
}

export interface ApiConditionalOrder {
  algo_id: number
  algo_client_id?: string | null
  symbol: string
  side: string
  position_side: string
  order_type: string
  quantity: number
  trigger_price: number
  status: string
  created_at: string
  trade_direction?: string | null
  exchange_order_id?: string | null
  client_order_id?: string | null
}

interface ApiTrade {
  id: number
  symbol: string
  side: string
  order_type: string
  order_category?: string | null
  trade_direction?: string | null
  quantity: number
  price?: number | null
  avg_price: number | null
  realized_pnl?: number | null
  status?: string
  commission: number
  commission_asset: string
  username?: string
  created_at?: string
}

export interface ApiOrderMarker {
  id: number
  username: string
  symbol: string
  side: string
  trade_direction?: string | null
  order_type: string
  order_category?: string | null
  filled_qty: number
  avg_price: number
  created_at: string
}

interface ApiPositionHistory {
  id: number
  username: string
  symbol: string
  side: string
  entry_price: number
  close_price: number
  quantity: number
  realized_pnl: number
  commission: number
  commission_asset?: string | null
  created_at: string
  updated_at?: string | null
}

interface ApiAccountSummary {
  symbol?: string | null
  base_asset?: string | null
  quote_asset?: string | null
  configured_leverage?: number | null
  long_position_qty?: number | null
  short_position_qty?: number | null
  long_position_value?: number | null
  short_position_value?: number | null
  rest_mark_price?: number | null
  available_balance: number | null
  margin_ratio: number | null
  risk_rate: number | null
  maint_margin: number | null
  total_equity: number | null
  position_value: number | null
  actual_leverage: number | null
  unrealized_pnl: number | null
  wallet_balance: number | null
  has_api_credentials: boolean
  message?: string | null
}

interface ApiProfileStats {
  total_pnl: number
  win_rate: number
  total_trades: number
  total_commission: number
  account_balance: number | null
  total_commission_by_asset: Array<{
    asset: string
    total: number
  }>
}

interface ApiDailyPnl {
  date: string
  pnl: number
  commission: number
  trades: number
  win_rate: number
}

interface ApiDailyLeaderboardEntry {
  rank: number
  username: string
  date: string
  pnl: number
  account_balance: number | null
  trades: number
  win_rate: number
  commission: number
}

interface ApiAllTimeLeaderboardEntry {
  rank: number
  username: string
  pnl: number
  trades: number
  win_rate: number
  commission: number
}

interface ApiProfileOverview {
  stats: ApiProfileStats
  daily_pnl: ApiDailyPnl[]
  daily_leaderboard: ApiDailyLeaderboardEntry[]
  all_time_leaderboard: ApiAllTimeLeaderboardEntry[]
  all_time_days: number | null
}

const inflightGetRequests = new Map<string, Promise<unknown>>()

function buildRequestKey(path: string, params?: Record<string, QueryValue>): string {
  if (!params || Object.keys(params).length === 0) return path
  const url = new URL(path, 'http://local.request')
  const entries = Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null)
    .sort(([left], [right]) => left.localeCompare(right))

  for (const [key, value] of entries) {
    if (Array.isArray(value)) {
      for (const item of value) url.searchParams.append(key, String(item))
    } else {
      url.searchParams.set(key, String(value))
    }
  }

  return `${url.pathname}?${url.searchParams.toString()}`
}

function makeBackendError(status: number, body: unknown) {
  const detail =
    typeof body === 'object' && body !== null && 'detail' in body
      ? (body as { detail?: string }).detail
      : undefined
  return {
    name: 'BackendRequestError',
    message: detail || `Request failed with status ${status}`,
    response: { status, data: body },
  }
}

async function request<T>(
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  options: { body?: unknown; params?: Record<string, QueryValue> } = {},
): Promise<T> {
  const requestKey = method === 'GET' ? buildRequestKey(path, options.params) : null
  if (requestKey) {
    const inflight = inflightGetRequests.get(requestKey)
    if (inflight) return inflight as Promise<T>
  }

  const perfLabel = `${method} ${path}`
  const perfActive = perf.isActive()
  const perfSpan = perfActive ? perf.spanStart(`api ${perfLabel}`) : null

  const execute = async (): Promise<T> => {
    let result: T
    if (window.electronAPI?.backendRequest) {
      const res = await window.electronAPI.backendRequest(method, path, {
        body: options.body,
        query: options.params,
      })
      if (res.status >= 200 && res.status < 300) {
        result = res.body as T
      } else {
        throw makeBackendError(res.status, res.body)
      }
    } else {
      const token = await getToken()
      const res = await axios.request<T>({
        method,
        url: `${getBackendBaseUrl()}${path}`,
        data: options.body,
        params: options.params,
        headers: getHeaders(token),
      })
      result = res.data
    }
    if (perfActive) perf.spanEnd(perfSpan, 'ok')
    return result
  }

  const pending = execute().catch((err) => {
    if (perfActive) perf.spanEnd(perfSpan, 'error')
    throw err
  })

  if (requestKey) inflightGetRequests.set(requestKey, pending)

  try {
    return await pending
  } finally {
    if (requestKey) inflightGetRequests.delete(requestKey)
  }
}

function getHeaders(token?: string | null) {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

async function getToken(): Promise<string | null> {
  return window.electronAPI?.getToken?.() ?? null
}

export const api = {
  // ── Orders ────────────────────────────────────────────────────────────────
  async submitOrder(order: {
    symbol: string; side: string; order_type: string; quantity: number
    price?: number; stop_price?: number; tp_price?: number; sl_price?: number
    post_only?: boolean
    leverage?: number
    margin_type: string; position_direction: string
  }): Promise<ApiResult> {
    return request<ApiResult>('POST', '/api/orders', { body: order })
  },

  async getOrders(params?: {
    limit?: number; user_id?: number; username?: string; order_id?: string
    start_time?: string; end_time?: string; status?: string
  }): Promise<ApiOrder[]> {
    return request<ApiOrder[]>('GET', '/api/orders', { params })
  },

  async getOrderUsers(): Promise<ApiOrderUser[]> {
    return request<ApiOrderUser[]>('GET', '/api/orders/users')
  },

  async getOrderMarkers(params: {
    symbol: string
    limit?: number
  }): Promise<ApiOrderMarker[]> {
    return request<ApiOrderMarker[]>('GET', '/api/orders/markers', { params })
  },

  // ── Positions ─────────────────────────────────────────────────────────────
  async getPositions(): Promise<ApiPosition[]> {
    return request<ApiPosition[]>('GET', '/api/positions')
  },

  async syncPositions(): Promise<ApiPosition[]> {
    return request<ApiPosition[]>('POST', '/api/positions/sync')
  },

  async setPositionTpSl(positionId: number, tpPrice: number | null, slPrice: number | null): Promise<ApiResult> {
    return request<ApiResult>('POST', `/api/positions/${positionId}/tpsl`, {
      body: { tp_price: tpPrice, sl_price: slPrice },
    })
  },

  async getOpenOrders(): Promise<ApiOrder[]> {
    return request<ApiOrder[]>('GET', '/api/orders/active')
  },

  async cancelOrder(orderId: number, symbol: string, exchangeOrderId: string): Promise<ApiResult> {
    return request<ApiResult>('POST', `/api/orders/${orderId}/cancel`, {
      body: { symbol, exchange_order_id: exchangeOrderId },
    })
  },

  async amendOrder(orderId: number, quantity: number, price: number): Promise<ApiResult> {
    return request<ApiResult>('POST', `/api/orders/${orderId}/amend`, {
      body: { quantity, price },
    })
  },

  async getConditionalOrders(): Promise<ApiConditionalOrder[]> {
    return request<ApiConditionalOrder[]>('GET', '/api/orders/conditional')
  },

  async cancelConditionalOrder(algoId: number): Promise<ApiResult> {
    return request<ApiResult>('POST', '/api/orders/conditional/cancel', {
      body: { algo_id: algoId },
    })
  },

  async getOrderHistory(): Promise<ApiOrder[]> {
    return request<ApiOrder[]>('GET', '/api/orders/history')
  },

  async getTradeHistory(): Promise<ApiTrade[]> {
    return request<ApiTrade[]>('GET', '/api/orders/fills')
  },

  async getPositionHistory(): Promise<ApiPositionHistory[]> {
    return request<ApiPositionHistory[]>('GET', '/api/positions/history')
  },

  async getRecentFills(): Promise<ApiTrade[]> {
    return request<ApiTrade[]>('GET', '/api/orders/fills')
  },

  async getAccountSummary(symbol?: string, force = false): Promise<ApiAccountSummary> {
    const params: Record<string, string | boolean> = {}
    if (symbol) params.symbol = symbol
    if (force) params.force = true
    return request<ApiAccountSummary>('GET', '/api/account/summary', {
      params: Object.keys(params).length ? params : undefined,
    })
  },

  async getMarkPrice(symbol: string): Promise<number> {
    const data = await request<{ symbol: string; mark_price: number }>('GET', '/api/account/mark-price', {
      params: { symbol },
    })
    return data.mark_price
  },

  async setAccountLeverage(symbol: string, leverage: number): Promise<ApiResult> {
    return request<ApiResult>('POST', '/api/account/leverage', {
      body: {
        symbol,
        leverage,
      },
    })
  },

  // ── Users (admin) ─────────────────────────────────────────────────────────
  async getUsers(): Promise<ApiUser[]> {
    return request<ApiUser[]>('GET', '/api/users')
  },

  async createUser(data: {
    username: string; password: string; role: string
    binance_api_key?: string; binance_api_secret?: string
  }): Promise<ApiResult> {
    return request<ApiResult>('POST', '/api/users', { body: data })
  },

  async updateUser(userId: number, data: Partial<{
    username: string; role: string; is_active: boolean; password: string
    binance_api_key: string; binance_api_secret: string
  }>): Promise<ApiResult> {
    return request<ApiResult>('PATCH', `/api/users/${userId}`, { body: data })
  },

  async deleteUser(userId: number): Promise<ApiResult> {
    return request<ApiResult>('DELETE', `/api/users/${userId}`)
  },

  // ── Config ────────────────────────────────────────────────────────────────
  async getMyConfig(): Promise<ApiResult> {
    return request<ApiResult>('GET', '/api/config')
  },

  async saveMyConfig(data: {
    binance_api_key?: string; binance_api_secret?: string
    testnet?: boolean; mock_mode?: boolean
  }): Promise<ApiResult> {
    return request<ApiResult>('POST', '/api/config', { body: data })
  },

  async changeMyPassword(data: {
    current_password: string; new_password: string
  }): Promise<ApiResult> {
    return request<ApiResult>('POST', '/api/auth/change-password', { body: data })
  },

  // ── Profile / analytics ───────────────────────────────────────────────────
  async getProfileStats(userId?: number): Promise<ApiProfileStats> {
    const params = userId ? { user_id: userId } : undefined
    return request<ApiProfileStats>('GET', '/api/profile/stats', { params })
  },

  async getDailyPnl(userId?: number): Promise<ApiDailyPnl[]> {
    const params = userId ? { user_id: userId } : undefined
    return request<ApiDailyPnl[]>('GET', '/api/profile/daily-pnl', { params })
  },

  async getProfileOverview(allTimeDays?: number | null): Promise<ApiProfileOverview> {
    const params = allTimeDays ? { all_time_days: allTimeDays } : undefined
    return request<ApiProfileOverview>('GET', '/api/profile/overview', { params })
  },

  // ── Ticker messages ───────────────────────────────────────────────────────
  async getTickerMessages(): Promise<ApiResult> {
    return request<ApiResult>('GET', '/api/ticker-messages')
  },
}
