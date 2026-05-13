// Trade Relay API client — all calls go to FastAPI backend on port 8000
import axios from 'axios'

const BASE_URL = 'http://127.0.0.1:8000'

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
}

interface ApiOrder {
  id: number
  symbol: string
  side: string
  order_type: string
  quantity: number
  price: number
  status: string
  username?: string
  exchange_order_id?: string
  created_at?: string
  error_message?: string
}

interface ApiTrade {
  id: number
  symbol: string
  side: string
  order_type: string
  quantity: number
  price?: number | null
  avg_price: number | null
  status?: string
  commission: number
  commission_asset: string
  username?: string
  created_at?: string
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
  created_at: string
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
}

interface ApiDailyPnl {
  date: string
  pnl: number
  trades: number
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
  if (window.electronAPI?.backendRequest) {
    const res = await window.electronAPI.backendRequest(method, path, {
      body: options.body,
      query: options.params,
    })
    if (res.status >= 200 && res.status < 300) return res.body as T
    throw makeBackendError(res.status, res.body)
  }

  const token = await getToken()
  const res = await axios.request<T>({
    method,
    url: `${BASE_URL}${path}`,
    data: options.body,
    params: options.params,
    headers: getHeaders(token),
  })
  return res.data
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

  // ── Positions ─────────────────────────────────────────────────────────────
  async getPositions(): Promise<ApiPosition[]> {
    return request<ApiPosition[]>('GET', '/api/positions')
  },

  async syncPositions(): Promise<ApiPosition[]> {
    return request<ApiPosition[]>('POST', '/api/positions/sync')
  },

  async getOpenOrders(): Promise<ApiOrder[]> {
    return request<ApiOrder[]>('GET', '/api/orders/active')
  },

  async cancelOrder(orderId: number, symbol: string, exchangeOrderId: string): Promise<ApiResult> {
    return request<ApiResult>('POST', `/api/orders/${orderId}/cancel`, {
      body: { symbol, exchange_order_id: exchangeOrderId },
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

  async getAccountSummary(symbol?: string): Promise<ApiAccountSummary> {
    return request<ApiAccountSummary>('GET', '/api/account/summary', {
      params: symbol ? { symbol } : undefined,
    })
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

  // ── Ticker messages ───────────────────────────────────────────────────────
  async getTickerMessages(): Promise<ApiResult> {
    return request<ApiResult>('GET', '/api/ticker-messages')
  },
}
