import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../api/client'
import { useAuthStore } from '../store/authStore'
import { useToastStore } from '../store/toastStore'
import { Locale, useTranslation } from '../i18n/translations'

const UI_LANG = (window as unknown as { electronAPI?: { uiLang?: string } }).electronAPI?.uiLang
const locale: Locale = (UI_LANG === 'en' ? 'en' : 'zh-CN')
const POSITIONS_WS_URL = 'ws://127.0.0.1:8000/api/positions/ws'

type Tab = 'positions' | 'openOrders' | 'history' | 'tradeHistory'
const QUOTE_ASSETS = ['USDT', 'USDC', 'FDUSD', 'BUSD', 'BTC', 'ETH'] as const

interface Position {
  id: number; symbol: string; side: string; quantity: number
  entry_price: number | null; liquidation_price: number | null; unrealized_pnl: number | null
  leverage: number; margin_type: string; margin: number | null
}

interface Order {
  id: number; symbol: string; side: string; order_type: string
  quantity: number; price: number; status: string; username?: string
  created_at?: string; exchange_order_id?: string
}

interface Trade {
  id: number; symbol: string; side: string; order_type: string
  quantity: number; avg_price: number | null; commission: number
  commission_asset: string; created_at?: string
}

interface PositionHistory {
  id: number; username: string; symbol: string; side: string
  entry_price: number; close_price: number; quantity: number
  realized_pnl: number; commission: number; created_at: string
}

export function PositionsPanel({
  refreshTrigger,
  isActive = true,
  sizeUnit = 'QUOTE',
}: {
  refreshTrigger?: number
  isActive?: boolean
  sizeUnit?: 'QUOTE' | 'BASE'
}) {
  const { t } = useTranslation(locale)
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated)
  const showToast = useToastStore((state) => state.showToast)
  const [tab, setTab] = useState<Tab>('positions')
  const [positions, setPositions] = useState<Position[]>([])
  const [openOrders, setOpenOrders] = useState<Order[]>([])
  const [history, setHistory] = useState<Order[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [positionHistory, setPositionHistory] = useState<PositionHistory[]>([])
  const [loading, setLoading] = useState(false)
  const [cancellingId, setCancellingId] = useState<number | null>(null)
  const loadRef = useRef<() => Promise<void>>(async () => {})

  const loadPositions = useCallback(async () => {
    if (!isActive || !isAuthenticated) return
    try {
      setPositions(await api.getPositions())
    } catch {
      // silently ignore background position refresh errors
    }
  }, [isActive, isAuthenticated])

  const loadPositionsRef = useRef<() => Promise<void>>(async () => {})
  useEffect(() => { loadPositionsRef.current = loadPositions }, [loadPositions])

  const load = useCallback(async () => {
    if (!isActive) {
      setLoading(false)
      return
    }
    if (!isAuthenticated) {
      setPositions([])
      setOpenOrders([])
      setHistory([])
      setTrades([])
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      if (tab === 'positions') setPositions(await api.getPositions())
      else if (tab === 'openOrders') setOpenOrders(await api.getOpenOrders())
      else if (tab === 'history') setHistory(await api.getOrderHistory())
      else if (tab === 'tradeHistory') setPositionHistory(await api.getPositionHistory())
    } catch (error: unknown) {
      const msg =
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (error as { message?: string })?.message ||
        t('order.error.failed')
      showToast('error', msg)
    }
    setLoading(false)
  }, [isActive, isAuthenticated, showToast, t, tab])

  useEffect(() => {
    loadRef.current = load
  }, [load])

  // Trigger load on mount and when refreshTrigger or tab changes.
  // Intentionally NOT including `load` in deps — we call it via loadRef to avoid
  // re-triggering on every internal state change (which in dev HMR would cause
  // dozens of concurrent requests from stale component instances).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void loadRef.current() }, [refreshTrigger, tab])

  // Periodic poll for Open Orders tab so missed WebSocket notifications don't leave stale status
  const tabRef = useRef<Tab>('positions')
  useEffect(() => { tabRef.current = tab }, [tab])
  useEffect(() => {
    if (!isActive || !isAuthenticated) return
    const POLL_MS = 30_000
    const timer = setInterval(() => {
      if (tabRef.current === 'openOrders') void loadRef.current()
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [isActive, isAuthenticated])

  useEffect(() => {
    if (!isActive || !isAuthenticated) return

    let alive = true
    let socket: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let reloadTimer: ReturnType<typeof setTimeout> | null = null
    let lastReloadAt = 0
    const RELOAD_COOLDOWN_MS = 2000 // 两次重载之间最短间隔

    const scheduleReload = () => {
      if (!alive) return
      if (reloadTimer) clearTimeout(reloadTimer)
      const now = Date.now()
      const delay = Math.max(250, lastReloadAt + RELOAD_COOLDOWN_MS - now)
      reloadTimer = setTimeout(() => {
        lastReloadAt = Date.now()
        void loadRef.current()
      }, delay)
    }

    const connect = async () => {
      const token = await window.electronAPI?.getToken?.()
      if (!alive || !token) return

      socket = new WebSocket(`${POSITIONS_WS_URL}?token=${encodeURIComponent(token)}`)

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data as string) as { type?: string; event?: string }
          if (data.type === 'account_update') {
            // Positions are already written to DB before this message is sent.
            // loadPositionsRef always fetches positions regardless of which tab is active.
            void loadPositionsRef.current()
            // Also reload whatever tab is currently shown (e.g. open orders).
            scheduleReload()
          } else if (data.type === 'order_update') {
            scheduleReload()
            // For poll-based or REST-sync updates, positions are already updated in DB.
            if (data.event === 'POLL' || data.event === 'SYNC' || data.event === 'REST_SYNC') {
              void loadPositionsRef.current()
            }
          }
        } catch {
          // ignore malformed messages
        }
      }

      socket.onerror = () => {
        socket?.close()
      }

      socket.onclose = () => {
        if (!alive) return
        reconnectTimer = setTimeout(() => {
          void connect()
        }, 3000)
      }
    }

    void connect()

    return () => {
      alive = false
      if (reloadTimer) clearTimeout(reloadTimer)
      if (reconnectTimer) clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [isActive, isAuthenticated])

  const handleCancelOrder = async (o: Order) => {
    if (!o.exchange_order_id) return
    setCancellingId(o.id)
    try {
      await api.cancelOrder(o.id, o.symbol, o.exchange_order_id)
      showToast('Order cancelled', 'success')
      setOpenOrders(prev => prev.map(x => x.id === o.id ? { ...x, status: 'CANCELED' } : x))
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      showToast(`Cancel failed: ${msg}`, 'error')
    } finally {
      setCancellingId(null)
    }
  }

  const handleRefresh = useCallback(async () => {
    if (!isActive || !isAuthenticated || loading) return
    setLoading(true)
    try {
      const synced = await api.syncPositions()
      setPositions(synced)
      if (tab !== 'positions') await loadRef.current()
    } catch (error: unknown) {
      const msg =
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (error as { message?: string })?.message ||
        t('order.error.failed')
      showToast('error', msg)
    } finally {
      setLoading(false)
    }
  }, [isActive, isAuthenticated, loading, tab, showToast, t])

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e] border-t border-[#3e3e42]">
      {/* Tab bar */}
      <div className="flex bg-[#252526] border-b border-[#3e3e42] shrink-0">
        {(['positions', 'openOrders', 'history', 'tradeHistory'] as Tab[]).map(tabKey => (
          <button key={tabKey} onClick={() => setTab(tabKey)}
            className={`px-3 py-1.5 text-xs font-medium border-b-2 transition-colors ${
              tab === tabKey ? 'border-[#007acc] text-[#cccccc]' : 'border-transparent text-[#858585] hover:text-[#cccccc]'
            }`}
          >
            {t(`pos.${tabKey === 'positions' ? 'title' : tabKey === 'openOrders' ? 'openOrders' : tabKey === 'history' ? 'history' : 'tradeHistory'}`)}
          </button>
        ))}
        <button onClick={handleRefresh} disabled={loading} className="ml-auto px-2 text-[#858585] hover:text-[#cccccc] text-xs pr-3 disabled:opacity-50">
          {loading ? t('statusbar.refreshing') : `↻ ${t('pos.refresh')}`}
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {tab === 'positions' && (
          <table className="trade-table w-full">
            <thead><tr>
              <th>{t('pos.symbol')}</th><th>{t('pos.side')}</th><th>{t('pos.size')}</th><th>{t('pos.entry')}</th>
              <th>{t('pos.liq')}</th><th>{t('pos.pnl')}</th><th>{t('pos.margin')}</th>
            </tr></thead>
            <tbody>
              {positions.length === 0
                ? <tr><td colSpan={7} className="text-center text-[#858585] py-6">{t('pos.empty')}</td></tr>
                : positions.map(p => (
                  <tr key={p.id}>
                    <td className="font-semibold">{p.symbol}</td>
                    <td className={p.side === 'LONG' ? 'text-buy' : 'text-sell'}>{p.side === 'LONG' ? t('pos.long') : t('pos.short')}</td>
                    <td className="font-mono">{formatPositionSize(p, sizeUnit)}</td>
                    <td className="font-mono">{p.entry_price != null ? p.entry_price.toFixed(2) : '-'}</td>
                    <td className="font-mono text-orange-400">{p.liquidation_price != null ? p.liquidation_price.toFixed(2) : '-'}</td>
                    <td className={`font-mono font-semibold ${(p.unrealized_pnl ?? 0) >= 0 ? 'text-buy' : 'text-sell'}`}>
                      {p.unrealized_pnl != null ? `${p.unrealized_pnl >= 0 ? '+' : ''}${p.unrealized_pnl.toFixed(2)}` : '-'}
                    </td>
                    <td className="text-[#858585]">{formatMarginType(p.margin_type, t)}</td>
                  </tr>
                ))
              }
            </tbody>
          </table>
        )}
        {(tab === 'openOrders' || tab === 'history') && (
          <table className="trade-table w-full">
            <thead><tr>
              <th>{t('log.time')}</th><th>{t('log.symbol')}</th><th>{t('log.side')}</th><th>{t('log.type')}</th>
              <th>{t('log.qty')}</th><th>{t('log.price')}</th><th>{t('log.status')}</th>
              {tab === 'openOrders' && <th>Order Id</th>}
              {tab === 'openOrders' && <th></th>}
            </tr></thead>
            <tbody>
              {(tab === 'openOrders' ? openOrders : history).length === 0
                ? <tr><td colSpan={tab === 'openOrders' ? 9 : 7} className="text-center text-[#858585] py-6">{t('pos.empty')}</td></tr>
                : (tab === 'openOrders' ? openOrders : history).map(o => (
                  <tr key={o.id}>
                    <td className="text-[#858585]">{formatTimestamp(o.created_at)}</td>
                    <td className="font-semibold">{o.symbol}</td>
                    <td className={o.side === 'BUY' ? 'text-buy' : 'text-sell'}>{o.side === 'BUY' ? t('side.buy') : t('side.sell')}</td>
                    <td className="text-[#858585]">{formatOrderType(o.order_type, t)}</td>
                    <td className="font-mono">{o.quantity}</td>
                    <td className="font-mono">{o.price ? o.price.toFixed(2) : t('log.market')}</td>
                    <td><StatusBadge status={o.status} t={t} /></td>
                    {tab === 'openOrders' && <td className="font-mono text-[10px] text-[#858585]">{o.exchange_order_id ?? '—'}</td>}
                    {tab === 'openOrders' && (
                      <td>
                        {o.status === 'NEW' || o.status === 'PARTIALLY_FILLED' ? (
                          <button
                            disabled={cancellingId === o.id}
                            onClick={() => void handleCancelOrder(o)}
                            className="px-2 py-0.5 text-[10px] rounded border border-[#f44] text-[#f44] hover:bg-[#f44] hover:text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            {cancellingId === o.id ? '…' : 'Cancel'}
                          </button>
                        ) : null}
                      </td>
                    )}
                  </tr>
                ))
              }
            </tbody>
          </table>
        )}
        {tab === 'tradeHistory' && (
          <table className="trade-table w-full">
            <thead><tr>
              <th>{t('log.time')}</th><th>{t('log.symbol')}</th><th>{t('log.side')}</th>
              <th>{t('pos.size')}</th><th>{t('pos.entry')}</th><th>{t('pos.closePrice')}</th>
              <th>{t('pos.realizedPnl')}</th><th>{t('trade.commission')}</th>
            </tr></thead>
            <tbody>
              {positionHistory.length === 0
                ? <tr><td colSpan={8} className="text-center text-[#858585] py-6">{t('pos.empty')}</td></tr>
                : positionHistory.map(ph => (
                  <tr key={ph.id}>
                    <td className="text-[#858585]">{formatTimestamp(ph.created_at)}</td>
                    <td className="font-semibold">{ph.symbol}</td>
                    <td className={ph.side === 'LONG' ? 'text-buy' : 'text-sell'}>{ph.side}</td>
                    <td className="font-mono">{ph.quantity}</td>
                    <td className="font-mono">{ph.entry_price.toFixed(2)}</td>
                    <td className="font-mono">{ph.close_price.toFixed(2)}</td>
                    <td className={`font-mono ${ph.realized_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {ph.realized_pnl >= 0 ? '+' : ''}{ph.realized_pnl.toFixed(4)}
                    </td>
                    <td className="font-mono text-[#858585]">{ph.commission.toFixed(4)}</td>
                  </tr>
                ))
              }
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function formatTimestamp(value?: string) {
  if (!value) return '-'
  return new Date(value).toLocaleString()
}

function StatusBadge({ status, t }: { status: string; t: (key: string) => string }) {
  const cls = status === 'FILLED' ? 'badge-filled'
    : status === 'MOCK' ? 'badge-mock'
    : status === 'FAILED' ? 'badge-failed'
    : 'badge-pending'
  return <span className={`badge ${cls}`}>{formatStatus(status, t)}</span>
}

function formatOrderType(orderType: string, t: (key: string) => string) {
  switch (orderType) {
    case 'LIMIT': return t('type.limit')
    case 'MARKET': return t('type.market')
    case 'STOP': return t('type.stop')
    case 'STOP_MARKET': return t('type.stopMarket')
    case 'TAKE_PROFIT': return t('type.takeProfit')
    case 'TAKE_PROFIT_MARKET': return t('type.takeProfitMarket')
    default: return orderType
  }
}

function formatStatus(status: string, t: (key: string) => string) {
  switch (status) {
    case 'FILLED': return t('status.filled')
    case 'MOCK': return t('status.mock')
    case 'FAILED': return t('status.failed')
    case 'NEW': return t('status.new')
    case 'PARTIALLY_FILLED': return t('status.partiallyFilled')
    case 'CANCELED': return t('status.canceled')
    case 'REJECTED': return t('status.rejected')
    case 'EXPIRED': return t('status.expired')
    case 'ERROR': return t('status.error')
    default: return t('status.pending')
  }
}

function formatMarginType(marginType: string, t: (key: string) => string) {
  if (marginType === 'CROSS') return t('pos.marginType.cross')
  if (marginType === 'ISOLATED') return t('pos.marginType.isolated')
  return marginType
}

function splitTradingSymbol(symbol: string) {
  const upperSymbol = symbol.toUpperCase()
  for (const quoteAsset of QUOTE_ASSETS) {
    if (upperSymbol.endsWith(quoteAsset) && upperSymbol.length > quoteAsset.length) {
      return { baseAsset: upperSymbol.slice(0, -quoteAsset.length), quoteAsset }
    }
  }
  return { baseAsset: upperSymbol, quoteAsset: 'USDT' }
}

function formatPositionSize(position: Position, sizeUnit: 'QUOTE' | 'BASE') {
  if (sizeUnit === 'BASE') return position.quantity.toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 })

  const { quoteAsset } = splitTradingSymbol(position.symbol)
  const price = Number.isFinite(position.entry_price) ? position.entry_price : 0
  const quoteValue = position.quantity * price
  if (!quoteValue) return `— ${quoteAsset}`
  return `${quoteValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${quoteAsset}`
}
