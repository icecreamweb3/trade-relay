// Trade Relay API client — all calls go to FastAPI backend on port 8000
import axios from 'axios'

const BASE_URL = 'http://127.0.0.1:8000'

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
  }) {
    const token = await getToken()
    const res = await axios.post(`${BASE_URL}/api/orders`, order, { headers: getHeaders(token) })
    return res.data
  },

  async getOrders(params?: {
    limit?: number; user_id?: number; username?: string; order_id?: string
    start_time?: string; end_time?: string; status?: string
  }) {
    const token = await getToken()
    const res = await axios.get(`${BASE_URL}/api/orders`, { params, headers: getHeaders(token) })
    return res.data
  },

  async getOrderUsers() {
    const token = await getToken()
    const res = await axios.get(`${BASE_URL}/api/orders/users`, { headers: getHeaders(token) })
    return res.data
  },

  // ── Positions ─────────────────────────────────────────────────────────────
  async getPositions() {
    const token = await getToken()
    const res = await axios.get(`${BASE_URL}/api/positions`, { headers: getHeaders(token) })
    return res.data
  },

  async getOpenOrders() {
    const token = await getToken()
    const res = await axios.get(`${BASE_URL}/api/orders/active`, { headers: getHeaders(token) })
    return res.data
  },

  async getOrderHistory() {
    const token = await getToken()
    const res = await axios.get(`${BASE_URL}/api/orders/history`, { headers: getHeaders(token) })
    return res.data
  },

  async getTradeHistory() {
    const token = await getToken()
    const res = await axios.get(`${BASE_URL}/api/orders/fills`, { headers: getHeaders(token) })
    return res.data
  },

  async getRecentFills() {
    const token = await getToken()
    const res = await axios.get(`${BASE_URL}/api/orders/fills`, { headers: getHeaders(token) })
    return res.data
  },

  async getAccountSummary(symbol?: string) {
    const token = await getToken()
    const res = await axios.get(`${BASE_URL}/api/account/summary`, {
      params: symbol ? { symbol } : undefined,
      headers: getHeaders(token),
    })
    return res.data
  },

  async setAccountLeverage(symbol: string, leverage: number) {
    const token = await getToken()
    const res = await axios.post(`${BASE_URL}/api/account/leverage`, {
      symbol,
      leverage,
    }, {
      headers: getHeaders(token),
    })
    return res.data
  },

  // ── Users (admin) ─────────────────────────────────────────────────────────
  async getUsers() {
    const token = await getToken()
    const res = await axios.get(`${BASE_URL}/api/users`, { headers: getHeaders(token) })
    return res.data
  },

  async createUser(data: {
    username: string; password: string; role: string
    binance_api_key?: string; binance_api_secret?: string
  }) {
    const token = await getToken()
    const res = await axios.post(`${BASE_URL}/api/users`, data, { headers: getHeaders(token) })
    return res.data
  },

  async updateUser(userId: number, data: Partial<{
    username: string; role: string; is_active: boolean; password: string
    binance_api_key: string; binance_api_secret: string
  }>) {
    const token = await getToken()
    const res = await axios.patch(`${BASE_URL}/api/users/${userId}`, data, { headers: getHeaders(token) })
    return res.data
  },

  async deleteUser(userId: number) {
    const token = await getToken()
    const res = await axios.delete(`${BASE_URL}/api/users/${userId}`, { headers: getHeaders(token) })
    return res.data
  },

  // ── Config ────────────────────────────────────────────────────────────────
  async getMyConfig() {
    const token = await getToken()
    const res = await axios.get(`${BASE_URL}/api/config`, { headers: getHeaders(token) })
    return res.data
  },

  async saveMyConfig(data: {
    binance_api_key?: string; binance_api_secret?: string
    testnet?: boolean; mock_mode?: boolean
  }) {
    const token = await getToken()
    const res = await axios.post(`${BASE_URL}/api/config`, data, { headers: getHeaders(token) })
    return res.data
  },

  async changeMyPassword(data: {
    current_password: string; new_password: string
  }) {
    const token = await getToken()
    const res = await axios.post(`${BASE_URL}/api/auth/change-password`, data, { headers: getHeaders(token) })
    return res.data
  },

  // ── Profile / analytics ───────────────────────────────────────────────────
  async getProfileStats(userId?: number) {
    const token = await getToken()
    const params = userId ? { user_id: userId } : {}
    const res = await axios.get(`${BASE_URL}/api/profile/stats`, { params, headers: getHeaders(token) })
    return res.data
  },

  async getDailyPnl(userId?: number) {
    const token = await getToken()
    const params = userId ? { user_id: userId } : {}
    const res = await axios.get(`${BASE_URL}/api/profile/daily-pnl`, { params, headers: getHeaders(token) })
    return res.data
  },

  // ── Ticker messages ───────────────────────────────────────────────────────
  async getTickerMessages() {
    const token = await getToken()
    const res = await axios.get(`${BASE_URL}/api/ticker-messages`, { headers: getHeaders(token) })
    return res.data
  },
}
