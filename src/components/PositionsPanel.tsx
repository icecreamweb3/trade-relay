import { useState, useEffect, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'
import { api, ApiConditionalOrder, getBackendWebSocketUrl } from '../api/client'
import { useMarketStore } from '../store/marketStore'
import { useAuthStore } from '../store/authStore'
import { useToastStore, type ToastKind } from '../store/toastStore'
import { Locale, useTranslation } from '../i18n/translations'
import { formatUtcTimestampToLocalString } from '../utils/datetime'
import { perfSignalDone } from '../utils/perf'
import { getPreferredLocale, useUiPreferencesStore } from '../store/uiPreferencesStore'

type Tab = 'positions' | 'openOrders' | 'history' | 'tradeHistory'
const QUOTE_ASSETS = ['USDT', 'USDC', 'FDUSD', 'BUSD', 'BTC', 'ETH'] as const

interface Position {
  id: number; symbol: string; side: string; quantity: number
  position_mode: string
  entry_price: number | null; liquidation_price: number | null; unrealized_pnl: number | null
  leverage: number; margin_type: string; margin: number | null
  tp_price?: number | null; sl_price?: number | null
}

interface Order {
  id: number; symbol: string; side: string; order_type: string
  quantity: number; filled_qty?: number; price: number; stop_price?: number | null
  avg_price?: number | null
  reduce_only?: boolean; post_only?: boolean
  trade_direction?: string | null
  commission?: number | null; commission_asset?: string | null
  status: string; username?: string; created_at?: string; updated_at?: string | null; exchange_order_id?: string
}

interface Trade {
  id: number; symbol: string; side: string; order_type: string
  quantity: number; avg_price: number | null; commission: number
  commission_asset: string; created_at?: string
}

interface PositionHistory {
  id: number; username: string; symbol: string; side: string
  position_mode: string
  entry_price: number; close_price: number; quantity: number
  realized_pnl: number; commission: number; commission_asset?: string | null; created_at: string; updated_at?: string | null
}

interface PositionsWsMessage {
  type?: string
  event?: string
  positions?: Position[]
  open_orders?: Order[]
  conditional_orders?: ApiConditionalOrder[]
}

interface MarketCloseConfirm {
  position: Position
  quantity: number
  side: 'BUY' | 'SELL'
}

interface AmendOrderDraft {
  order: Order
  position: Position | null
}

export function PositionsPanel({
  refreshTrigger,
  isActive = true,
  sizeUnit = 'QUOTE',
  onOrdersChanged,
}: {
  refreshTrigger?: number
  isActive?: boolean
  sizeUnit?: 'QUOTE' | 'BASE'
  onOrdersChanged?: () => void
}) {
  const locale = useUiPreferencesStore((state) => state.locale)
  const { t } = useTranslation(locale)
  const { symbol: activeSymbol, currentPrice, markPrice } = useMarketStore()
  const { baseAsset: activeBaseAsset, quoteAsset: activeQuoteAsset } = splitTradingSymbol(activeSymbol)
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated)
  const currentUser = useAuthStore((state) => state.user)
  const showToast = useToastStore((state) => state.showToast)
  const [tab, setTab] = useState<Tab>('positions')
  const [positions, setPositions] = useState<Position[]>([])
  const [openOrders, setOpenOrders] = useState<Order[]>([])
  const [history, setHistory] = useState<Order[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [positionHistory, setPositionHistory] = useState<PositionHistory[]>([])
  const [loading, setLoading] = useState(false)
  const [closingPositionId, setClosingPositionId] = useState<number | null>(null)
  const [cancellingId, setCancellingId] = useState<number | null>(null)
  const [amendingId, setAmendingId] = useState<number | null>(null)
  const [conditionalOrders, setConditionalOrders] = useState<ApiConditionalOrder[]>([])
  const [cancellingAlgoId, setCancellingAlgoId] = useState<number | null>(null)
  const [bulkCancelling, setBulkCancelling] = useState<'basic' | 'conditional' | null>(null)
  const [bulkCancelConfirm, setBulkCancelConfirm] = useState<'basic' | 'conditional' | null>(null)
  const [openOrdersSubTab, setOpenOrdersSubTab] = useState<'basic' | 'conditional'>('basic')
  const [tpslPosition, setTpslPosition] = useState<Position | null>(null)
  const [marketCloseConfirm, setMarketCloseConfirm] = useState<MarketCloseConfirm | null>(null)
  const [amendDraft, setAmendDraft] = useState<AmendOrderDraft | null>(null)
  const loadRef = useRef<() => Promise<void>>(async () => {})
  const _positionsFirstLoadDone = useRef(false)

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
      setConditionalOrders([])
      setHistory([])
      setTrades([])
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      if (tab === 'positions') setPositions(await api.getPositions())
      else if (tab === 'openOrders') {
        const [basic, conditional] = await Promise.all([api.getOpenOrders(), api.getConditionalOrders()])
        setOpenOrders(basic)
        setConditionalOrders(conditional)
      }
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
    if (!_positionsFirstLoadDone.current) {
      _positionsFirstLoadDone.current = true
      perfSignalDone('positions panel: first load done')
    }
  }, [isActive, isAuthenticated, showToast, t, tab])

  useEffect(() => {
    loadRef.current = load
  }, [load])

  // Trigger load on mount and when refreshTrigger or tab changes.
  // Intentionally NOT including `load` in deps — we call it via loadRef to avoid
  // re-triggering on every internal state change (which in dev HMR would cause
  // dozens of concurrent requests from stale component instances).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void loadRef.current() }, [refreshTrigger, tab, isActive, isAuthenticated])

  // When refreshTrigger fires and we're NOT on the positions tab, still refresh positions
  // (load() only fetches the active tab's data)
  const tabRef = useRef<Tab>('positions')
  useEffect(() => { tabRef.current = tab }, [tab])
  useEffect(() => {
    if (!refreshTrigger) return
    if (tabRef.current !== 'positions') void loadPositionsRef.current()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshTrigger])

  // Periodic poll for Open Orders tab so missed WebSocket notifications don't leave stale status
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

      const wsUrl = new URL(getBackendWebSocketUrl('/api/positions/ws'))
      wsUrl.searchParams.set('token', token)
      socket = new WebSocket(wsUrl.toString())

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data as string) as PositionsWsMessage
          const hasSnapshot = Array.isArray(data.positions) || Array.isArray(data.open_orders) || Array.isArray(data.conditional_orders)

          if (Array.isArray(data.positions)) {
            setPositions(data.positions)
          }
          if (Array.isArray(data.open_orders)) {
            setOpenOrders(data.open_orders)
          }
          if (Array.isArray(data.conditional_orders)) {
            setConditionalOrders(data.conditional_orders)
          }

          if (data.type === 'account_update') {
            if (!hasSnapshot) {
              void loadPositionsRef.current()
              scheduleReload()
            }
          } else if (data.type === 'order_update') {
            if (!hasSnapshot) {
              scheduleReload()
              if (data.event === 'POLL' || data.event === 'SYNC' || data.event === 'REST_SYNC') {
                void loadPositionsRef.current()
              }
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
      showToast('success', t('pos.cancelSingleSuccess'))
      setOpenOrders(prev => prev.map(x => x.id === o.id ? { ...x, status: 'CANCELED' } : x))
      onOrdersChanged?.()
    } catch (err: unknown) {
      showToast('error', t('pos.cancelSingleFailed', {
        reason: getRequestErrorMessage(err, t('pos.cancelFailed')),
      }))
    } finally {
      setCancellingId(null)
    }
  }

  const handleCancelConditional = async (o: ApiConditionalOrder) => {
    setCancellingAlgoId(o.algo_id)
    try {
      await api.cancelConditionalOrder(o.algo_id)
      showToast('success', t('pos.cancelConditionalSuccess'))
      setConditionalOrders(prev => prev.filter(x => x.algo_id !== o.algo_id))
    } catch (err: unknown) {
      showToast('error', t('pos.cancelSingleFailed', {
        reason: getRequestErrorMessage(err, t('pos.cancelFailed')),
      }))
    } finally {
      setCancellingAlgoId(null)
    }
  }

  const handleAmendOrder = useCallback(async (order: Order, quantity: number, price: number) => {
    setAmendingId(order.id)
    try {
      await api.amendOrder(order.id, quantity, price)
      showToast('success', t('pos.amendSuccess'))
      setAmendDraft(null)
      await loadRef.current()
      onOrdersChanged?.()
    } catch (err: unknown) {
      showToast('error', t('pos.amendFailed', {
        reason: getRequestErrorMessage(err, t('order.error.failed')),
      }))
    } finally {
      setAmendingId(null)
    }
  }, [onOrdersChanged, showToast, t])

  const handleCancelAllOpenOrders = useCallback(async () => {
    if (tab !== 'openOrders') return

    if (openOrdersSubTab === 'basic') {
      const cancellableOrders = openOrders.filter((order) =>
        (order.status === 'NEW' || order.status === 'PARTIALLY_FILLED') && order.exchange_order_id,
      )
      if (cancellableOrders.length === 0) return

      setBulkCancelling('basic')
      const cancelledIds = new Set<number>()
      let failedCount = 0
      try {
        for (const order of cancellableOrders) {
          try {
            await api.cancelOrder(order.id, order.symbol, String(order.exchange_order_id))
            cancelledIds.add(order.id)
          } catch {
            failedCount += 1
          }
        }
        if (cancelledIds.size > 0) {
          setOpenOrders((prev) => prev.filter((order) => !cancelledIds.has(order.id)))
          onOrdersChanged?.()
        }
        if (failedCount === 0) {
          showToast('success', t('pos.cancelAllSuccess', { count: cancelledIds.size }))
        } else {
          showToast('error', t('pos.cancelAllPartial', { count: cancelledIds.size, failed: failedCount }))
        }
      } finally {
        setBulkCancelling(null)
      }
      return
    }

    const cancellableConditionalOrders = conditionalOrders.filter((order) => order.status === 'NEW')
    if (cancellableConditionalOrders.length === 0) return

    setBulkCancelling('conditional')
    const cancelledAlgoIds = new Set<number>()
    let failedCount = 0
    try {
      for (const order of cancellableConditionalOrders) {
        try {
          await api.cancelConditionalOrder(order.algo_id)
          cancelledAlgoIds.add(order.algo_id)
        } catch {
          failedCount += 1
        }
      }
      if (cancelledAlgoIds.size > 0) {
        setConditionalOrders((prev) => prev.filter((order) => !cancelledAlgoIds.has(order.algo_id)))
      }
      if (failedCount === 0) {
        showToast('success', t('pos.cancelAllSuccess', { count: cancelledAlgoIds.size }))
      } else {
        showToast('error', t('pos.cancelAllPartial', { count: cancelledAlgoIds.size, failed: failedCount }))
      }
    } finally {
      setBulkCancelling(null)
    }
  }, [conditionalOrders, onOrdersChanged, openOrders, openOrdersSubTab, showToast, t, tab])

  const basicCancellableCount = openOrders.filter((order) =>
    (order.status === 'NEW' || order.status === 'PARTIALLY_FILLED') && order.exchange_order_id,
  ).length
  const conditionalCancellableCount = conditionalOrders.filter((order) => order.status === 'NEW').length
  const sizeUnitAsset = sizeUnit === 'QUOTE' ? activeQuoteAsset : activeBaseAsset
  const sizeHeaderLabel = `${t('pos.size')} (${sizeUnitAsset})`
  const qtyHeaderLabel = `${t('log.qty')} (${sizeUnitAsset})`
  const amountHeaderLabel = `${t('pos.amount')} (${sizeUnitAsset})`

  const handleMarketClose = useCallback((position: Position) => {
    if (!currentUser?.username || closingPositionId !== null) return
    const quantity = Math.abs(position.quantity)
    if (!quantity || quantity <= 0) {
      showToast('error', t('order.error.invalidQuantity'))
      return
    }

    setMarketCloseConfirm({
      position,
      quantity,
      side: position.side === 'LONG' ? 'SELL' : 'BUY',
    })
  }, [closingPositionId, currentUser?.username, showToast, t])

  const confirmMarketClose = useCallback(async () => {
    if (!marketCloseConfirm || !currentUser?.username) return

    const { position, quantity, side } = marketCloseConfirm
    setMarketCloseConfirm(null)
    setClosingPositionId(position.id)
    try {
      await api.submitOrder({
        symbol: position.symbol,
        side,
        order_type: 'MARKET',
        quantity,
        leverage: readStoredLeverage(currentUser.username, position.symbol) ?? position.leverage ?? 10,
        margin_type: position.margin_type,
        position_direction: 'CLOSE',
        position_mode: position.position_mode,
      })
      showToast('success', t('pos.marketClose.success'))
      setPositions(prev => prev.filter((item) => item.id !== position.id))
    } catch (error: unknown) {
      showToast('error', getRequestErrorMessage(error, t('order.error.failed')))
    } finally {
      setClosingPositionId(null)
    }
  }, [currentUser?.username, marketCloseConfirm, showToast, t])

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
    <>
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
              <th>{t('pos.symbol')}</th><th>{t('pos.side')}</th><th>{sizeHeaderLabel}</th><th>{t('pos.entry')}</th>
              <th>{t('pos.positionMode')}</th><th>{t('pos.liq')}</th><th>{t('pos.pnl')}</th><th>{t('pos.margin')}</th><th>{t('pos.tpSl')}</th><th></th>
            </tr></thead>
            <tbody>
              {positions.length === 0
                ? <tr><td colSpan={10} className="text-center text-[#858585] py-6">{t('pos.empty')}</td></tr>
                : positions.map(p => (
                  <tr key={p.id}>
                    <td className="font-semibold">{p.symbol}</td>
                    <td className={p.side === 'LONG' ? 'text-buy' : 'text-sell'}>{p.side === 'LONG' ? t('pos.long') : t('pos.short')}</td>
                    <td className="font-mono">{formatPositionSize(p, sizeUnit, activeSymbol, markPrice ?? currentPrice)}</td>
                    <td className="font-mono">{p.entry_price != null ? p.entry_price.toFixed(2) : '-'}</td>
                    <td className="text-[#858585]">{formatPositionMode(p.position_mode, t)}</td>
                    <td className="font-mono text-orange-400">{p.liquidation_price != null ? p.liquidation_price.toFixed(2) : '-'}</td>
                    <td className={`font-mono font-semibold ${(getLiveUnrealizedPnl(p, activeSymbol, markPrice ?? currentPrice) ?? 0) >= 0 ? 'text-buy' : 'text-sell'}`}>
                      {formatUnrealizedPnl(p, activeSymbol, markPrice ?? currentPrice)}
                    </td>
                    <td className="text-[#858585]">{formatMarginType(p.margin_type, t)}</td>
                    <td>
                      <div className="flex items-center gap-1.5 whitespace-nowrap">
                        <span
                          className="font-mono text-[10px] text-[#aaa] cursor-pointer hover:text-[#F0B90B] transition-colors"
                          onClick={() => setTpslPosition(p)}
                        >
                          {formatTpSl(p.tp_price, p.sl_price)}
                        </span>
                        <button
                          onClick={() => setTpslPosition(p)}
                          className="shrink-0 w-[18px] h-[18px] flex items-center justify-center rounded border border-[#3C4149] text-[#848E9C] hover:border-[#F0B90B] hover:text-[#F0B90B] transition-colors"
                          title={t('pos.setTpSl')}
                        >
                          <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
                            <circle cx="5" cy="5" r="3.5"/>
                            <line x1="5" y1="1" x2="5" y2="2"/>
                            <line x1="5" y1="8" x2="5" y2="9"/>
                            <line x1="1" y1="5" x2="2" y2="5"/>
                            <line x1="8" y1="5" x2="9" y2="5"/>
                          </svg>
                        </button>
                      </div>
                    </td>
                    <td className="text-right">
                      <button
                        disabled={closingPositionId === p.id}
                        onClick={() => void handleMarketClose(p)}
                        className="px-2.5 py-1 text-[11px] rounded border border-[#f6465d] text-[#f6465d] hover:bg-[#f6465d] hover:text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                        title={t('pos.marketClose')}
                      >
                        {closingPositionId === p.id ? '…' : t('pos.marketClose')}
                      </button>
                    </td>
                  </tr>
                ))
              }
            </tbody>
          </table>
        )}
        {(tab === 'openOrders' || tab === 'history') && (
          <>
          {tab === 'openOrders' && (
            <div className="flex gap-0 border-b border-[#3e3e42] bg-[#252526] px-2 pt-1">
              {(['basic', 'conditional'] as const).map(sub => (
                <button
                  key={sub}
                  onClick={() => setOpenOrdersSubTab(sub)}
                  className={`px-3 py-1 text-[11px] font-medium border-b-2 transition-colors ${
                    openOrdersSubTab === sub ? 'border-[#F0B90B] text-[#F0B90B]' : 'border-transparent text-[#858585] hover:text-[#cccccc]'
                  }`}
                >
                  {sub === 'basic'
                    ? `${t('pos.basicOrders')}(${openOrders.length})`
                    : `${t('pos.conditionalOrders')}(${conditionalOrders.length})`}
                </button>
              ))}
              <button
                onClick={() => setBulkCancelConfirm(openOrdersSubTab)}
                disabled={
                  bulkCancelling !== null ||
                  (openOrdersSubTab === 'basic' ? basicCancellableCount === 0 : conditionalCancellableCount === 0)
                }
                className="ml-auto mb-1 px-2.5 py-1 text-[10px] rounded border border-[#f44] text-[#f44] hover:bg-[#f44] hover:text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {bulkCancelling === openOrdersSubTab ? t('pos.cancelAllLoading') : t('pos.cancelAll')}
              </button>
            </div>
          )}
          {(tab === 'history' || openOrdersSubTab === 'basic') && (
            <table className="trade-table w-full">
              <thead><tr>
                <th>{t('log.time')}</th><th>{t('log.symbol')}</th><th>{t('log.side')}</th><th>{t('log.type')}</th>
                {tab === 'history' && <th>{t('log.dir')}</th>}
                <th>{qtyHeaderLabel}</th><th>{t('log.price')}</th>
                {tab === 'history' && <th>{t('trade.commission')}</th>}
                {tab === 'history' && <th>{t('trade.commissionAsset')}</th>}
                <th>{t('log.status')}</th>
                {tab === 'openOrders' && <th>{t('pos.reduceOnly')}</th>}
                {tab === 'openOrders' && <th>{t('pos.postOnly')}</th>}
                {tab === 'openOrders' && <th>{t('pos.triggerConditions')}</th>}
                {tab === 'openOrders' && <th>{t('log.id')}</th>}
                {tab === 'openOrders' && <th></th>}
              </tr></thead>
              <tbody>
                {(tab === 'openOrders' ? openOrders : history).length === 0
                  ? <tr><td colSpan={tab === 'openOrders' ? 12 : 10} className="text-center text-[#858585] py-6">{t('pos.empty')}</td></tr>
                  : (tab === 'openOrders' ? openOrders : history).map(o => (
                    <tr key={o.id}>
                      <td className="text-[#858585]">{formatTimestamp(tab === 'history' ? (o.updated_at || o.created_at) : o.created_at)}</td>
                      <td className="font-semibold">{o.symbol}</td>
                      <td className={o.side === 'BUY' ? 'text-buy' : 'text-sell'}>{o.side === 'BUY' ? t('side.buy') : t('side.sell')}</td>
                      <td className="text-[#858585]">{formatOrderType(o.order_type, t)}</td>
                      {tab === 'history' && <td className="text-[#858585]">{formatTradeDirection(o.trade_direction, t)}</td>}
                      <td className="font-mono">{formatOrderSize(o, sizeUnit, activeSymbol, markPrice ?? currentPrice)}</td>
                      <td className="font-mono">{o.price ? o.price.toFixed(2) : t('log.market')}</td>
                      {tab === 'history' && <td className="font-mono text-[#858585]">{o.commission != null ? o.commission.toFixed(4) : '—'}</td>}
                      {tab === 'history' && <td className="font-mono text-[#858585]">{o.commission_asset ?? '—'}</td>}
                      <td><StatusBadge status={o.status} t={t} /></td>
                      {tab === 'openOrders' && <td className="text-center">{o.reduce_only ? t('common.yes') : t('common.no')}</td>}
                      {tab === 'openOrders' && <td className="text-center">{o.post_only ? t('common.yes') : t('common.no')}</td>}
                      {tab === 'openOrders' && (
                        <td className="font-mono text-[11px]">
                          {o.stop_price
                            ? t('pos.triggerConditionWithPrice', {
                                operator: '<=',
                                price: o.stop_price.toLocaleString('en-US', { minimumFractionDigits: 1 }),
                              })
                            : '—'}
                        </td>
                      )}
                      {tab === 'openOrders' && <td className="font-mono text-[10px] text-[#858585]">{o.exchange_order_id ?? '—'}</td>}
                      {tab === 'openOrders' && (
                        <td>
                          {o.status === 'NEW' || o.status === 'PARTIALLY_FILLED' ? (
                            <div className="flex items-center justify-end gap-1">
                              {canAmendOrder(o) ? (
                                <button
                                  disabled={amendingId === o.id || cancellingId === o.id}
                                  onClick={() => setAmendDraft({ order: o, position: findPositionForOrder(positions, o) })}
                                  className="px-2 py-0.5 text-[10px] rounded border border-[#F0B90B] text-[#F0B90B] hover:bg-[#F0B90B] hover:text-black transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                                >
                                  {amendingId === o.id ? '…' : t('common.modify')}
                                </button>
                              ) : null}
                              <button
                                disabled={cancellingId === o.id || amendingId === o.id}
                                onClick={() => void handleCancelOrder(o)}
                                className="px-2 py-0.5 text-[10px] rounded border border-[#f44] text-[#f44] hover:bg-[#f44] hover:text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                              >
                                {cancellingId === o.id ? '…' : t('common.cancel')}
                              </button>
                            </div>
                          ) : null}
                        </td>
                      )}
                    </tr>
                  ))
                }
              </tbody>
            </table>
          )}
          {tab === 'openOrders' && openOrdersSubTab === 'conditional' && (
            <table className="trade-table w-full">
              <thead><tr>
                <th>{t('log.time')}</th>
                <th>{t('log.symbol')}</th>
                <th>{t('log.type')}</th>
                <th>{t('log.side')}</th>
                <th>{amountHeaderLabel}</th>
                <th>{t('log.price')}</th>
                <th>{t('pos.triggerConditions')}</th>
                <th></th>
              </tr></thead>
              <tbody>
                {conditionalOrders.length === 0
                  ? <tr><td colSpan={8} className="text-center text-[#858585] py-6">{t('pos.empty')}</td></tr>
                  : conditionalOrders.map(o => {
                    const isTp = o.order_type === 'TAKE_PROFIT_MARKET'
                    const actionLabel = formatConditionalAction(o, t)
                    const triggerOperator = getConditionalTriggerOperator(o)
                    const isCloseOrder = (o.trade_direction ?? '').toUpperCase() === 'CLOSE'
                    return (
                      <tr key={o.algo_id}>
                        <td className="text-[#858585]">{formatTimestamp(o.created_at)}</td>
                        <td className="font-semibold">{o.symbol}<br/><span className="text-[10px] text-[#858585]">{t('order.perpetual')}</span></td>
                        <td>{isTp ? t('type.takeProfitMarket') : t('type.stopMarket')}</td>
                        <td className={o.side === 'BUY' ? 'text-buy' : 'text-sell'}>
                          {actionLabel}
                        </td>
                        <td className="font-mono">
                          {formatConditionalOrderSize(o, sizeUnit, activeSymbol, markPrice ?? currentPrice)}
                        </td>
                        <td className="font-mono">{t('log.market')}</td>
                        <td className="font-mono text-[11px]">
                          {t('pos.triggerConditionWithPrice', {
                            operator: triggerOperator,
                            price: o.trigger_price.toLocaleString('en-US', { minimumFractionDigits: 1 }),
                          })}
                        </td>
                        <td>
                          <button
                            disabled={cancellingAlgoId === o.algo_id}
                            onClick={() => void handleCancelConditional(o)}
                            className="px-2 py-0.5 text-[10px] rounded border border-[#f44] text-[#f44] hover:bg-[#f44] hover:text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            {cancellingAlgoId === o.algo_id ? '…' : t('common.cancel')}
                          </button>
                        </td>
                      </tr>
                    )
                  })
                }
              </tbody>
            </table>
          )}
          </>
        )}
        {tab === 'tradeHistory' && (
          <table className="trade-table w-full">
            <thead><tr>
              <th>{t('log.time')}</th><th>{t('log.symbol')}</th><th>{t('log.side')}</th>
              <th>{t('pos.positionMode')}</th><th>{sizeHeaderLabel}</th><th>{t('pos.entry')}</th><th>{t('pos.closePrice')}</th>
              <th>{t('pos.realizedPnl')}</th><th>{t('trade.commission')}</th><th>{t('trade.commissionAsset')}</th>
            </tr></thead>
            <tbody>
              {positionHistory.length === 0
                ? <tr><td colSpan={10} className="text-center text-[#858585] py-6">{t('pos.empty')}</td></tr>
                : positionHistory.map(ph => (
                  <tr key={ph.id}>
                    <td className="text-[#858585]">{formatTimestamp(ph.updated_at || ph.created_at)}</td>
                    <td className="font-semibold">{ph.symbol}</td>
                    <td className={ph.side === 'LONG' ? 'text-buy' : 'text-sell'}>{ph.side}</td>
                    <td className="text-[#858585]">{formatPositionMode(ph.position_mode, t)}</td>
                    <td className="font-mono">{formatPositionHistorySize(ph, sizeUnit)}</td>
                    <td className="font-mono">{ph.entry_price.toFixed(2)}</td>
                    <td className="font-mono">{ph.close_price.toFixed(2)}</td>
                    <td className={`font-mono ${ph.realized_pnl >= 0 ? 'text-profit' : 'text-loss'}`}>
                      {ph.realized_pnl >= 0 ? '+' : ''}{ph.realized_pnl.toFixed(4)}
                    </td>
                    <td className="font-mono text-[#858585]">{ph.commission.toFixed(4)}</td>
                    <td className="font-mono text-[#858585]">{ph.commission_asset || '-'}</td>
                  </tr>
                ))
              }
            </tbody>
          </table>
        )}
      </div>
    </div>

    {tpslPosition && (
      <TpSlModal
        position={tpslPosition}
        username={currentUser?.username ?? ''}
        onClose={() => setTpslPosition(null)}
        onSaved={(posId, tp, sl) => {
          setPositions(prev => prev.map(p => p.id === posId ? { ...p, tp_price: tp, sl_price: sl } : p))
          setTpslPosition(null)
        }}
        showToast={showToast}
      />
    )}

    {marketCloseConfirm && (
      <MarketCloseConfirmModal
        confirm={marketCloseConfirm}
        submitting={closingPositionId === marketCloseConfirm.position.id}
        onCancel={() => setMarketCloseConfirm(null)}
        onConfirm={() => void confirmMarketClose()}
        t={t}
      />
    )}

    {bulkCancelConfirm && (
      <BulkCancelConfirmModal
        count={bulkCancelConfirm === 'basic' ? basicCancellableCount : conditionalCancellableCount}
        submitting={bulkCancelling === bulkCancelConfirm}
        onCancel={() => setBulkCancelConfirm(null)}
        onConfirm={() => {
          setBulkCancelConfirm(null)
          void handleCancelAllOpenOrders()
        }}
        t={t}
      />
    )}

    {amendDraft && (
      <AmendOrderModal
        order={amendDraft.order}
        position={amendDraft.position}
        submitting={amendingId === amendDraft.order.id}
        onCancel={() => setAmendDraft(null)}
        onConfirm={(quantity, price) => void handleAmendOrder(amendDraft.order, quantity, price)}
        t={t}
      />
    )}
    </>
  )
}

function canAmendOrder(order: Order): boolean {
  const status = String(order.status || '').toUpperCase()
  const orderType = String(order.order_type || '').toUpperCase()
  return (status === 'NEW' || status === 'PARTIALLY_FILLED') && orderType === 'LIMIT' && Boolean(order.exchange_order_id)
}

function findPositionForOrder(positions: Position[], order: Order): Position | null {
  const matched = positions.find((position) => {
    if (position.symbol.toUpperCase() !== order.symbol.toUpperCase()) return false
    if (position.quantity <= 0) return false
    if (order.side === 'SELL') return position.side === 'LONG'
    if (order.side === 'BUY') return position.side === 'SHORT'
    return false
  })
  return matched ?? null
}

function getRequestErrorMessage(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail)) {
    const message = detail.map((item) => {
      if (typeof item === 'string') return item
      if (item && typeof item === 'object') {
        const obj = item as { msg?: unknown; message?: unknown }
        if (typeof obj.msg === 'string') return obj.msg
        if (typeof obj.message === 'string') return obj.message
        try {
          return JSON.stringify(item)
        } catch {
          return ''
        }
      }
      return String(item ?? '')
    }).filter(Boolean).join('; ')
    if (message) return message
  }
  if (detail && typeof detail === 'object') {
    const obj = detail as { msg?: unknown; message?: unknown }
    if (typeof obj.msg === 'string') return obj.msg
    if (typeof obj.message === 'string') return obj.message
    try {
      return JSON.stringify(detail)
    } catch {
      return fallback
    }
  }
  const message = (error as { message?: unknown })?.message
  return typeof message === 'string' && message ? message : fallback
}

function formatTradeDirection(value: string | null | undefined, t: (key: string) => string): string {
  const normalized = String(value || '').toUpperCase()
  if (normalized === 'OPEN') return t('order.open')
  if (normalized === 'CLOSE') return t('order.close')
  return '—'
}

function formatTpSl(tp?: number | null, sl?: number | null): string {
  const tpStr = tp != null && tp > 0 ? tp.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 2 }) : '--'
  const slStr = sl != null && sl > 0 ? sl.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 2 }) : '--'
  return `${tpStr} / ${slStr}`
}

function readStoredLeverage(username: string, symbol: string): number | null {
  try {
    const raw = window.localStorage.getItem(`trade-relay:leverage:${username}:${symbol.toUpperCase()}`)
    if (!raw) return null
    const value = Number.parseInt(raw, 10)
    return Number.isFinite(value) && value >= 1 && value <= 125 ? value : null
  } catch {
    return null
  }
}

function MarketCloseConfirmModal({
  confirm,
  submitting,
  onCancel,
  onConfirm,
  t,
}: {
  confirm: MarketCloseConfirm
  submitting: boolean
  onCancel: () => void
  onConfirm: () => void
  t: (key: string) => string
}) {
  const { position, quantity, side } = confirm

  useEffect(() => {
    const api = (window as unknown as { electronAPI?: { setBinanceViewVisible?: (v: boolean) => void } }).electronAPI
    api?.setBinanceViewVisible?.(false)
    return () => { api?.setBinanceViewVisible?.(true) }
  }, [])

  return createPortal(
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-[#1E2026] border border-[#474D57] rounded-lg shadow-xl w-80 p-5 space-y-4">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-bold text-[#EAECEF]">{t('order.market.confirm.title')}</span>
          <span className={`ml-auto text-[11px] font-bold px-2 py-0.5 rounded ${
            side === 'BUY' ? 'bg-[#0ecb81]/20 text-[#0ecb81]' : 'bg-[#f6465d]/20 text-[#f6465d]'
          }`}>
            {side === 'BUY' ? t('side.buy') : t('side.sell')}
          </span>
        </div>
        <div className="space-y-2 text-[11px] text-[#B7BDC6]">
          <div className="flex justify-between">
            <span>{t('pos.symbol')}</span>
            <span className="font-semibold text-[#EAECEF]">{position.symbol}</span>
          </div>
          <div className="flex justify-between">
            <span>{t('pos.side')}</span>
            <span className={position.side === 'LONG' ? 'text-buy font-semibold' : 'text-sell font-semibold'}>
              {position.side === 'LONG' ? t('pos.long') : t('pos.short')}
            </span>
          </div>
          <div className="flex justify-between">
            <span>{t('pos.size')}</span>
            <span className="font-mono text-[#EAECEF]">{quantity}</span>
          </div>
        </div>
        <p className="text-[11px] leading-relaxed text-[#848E9C]">{t('order.market.confirm.warning')}</p>
        <div className="grid grid-cols-2 gap-2 pt-1">
          <button
            onClick={onCancel}
            disabled={submitting}
            className="py-2 text-[12px] font-medium rounded border border-[#474D57] text-[#848E9C] hover:text-[#EAECEF] hover:border-[#848E9C] transition-colors disabled:opacity-40"
          >
            {t('order.market.confirm.cancel')}
          </button>
          <button
            onClick={onConfirm}
            disabled={submitting}
            className="py-2 text-[12px] font-bold rounded bg-[#F0B90B] hover:bg-[#d4a30a] text-black transition-colors disabled:opacity-40"
          >
            {submitting ? t('order.submitting') : t('order.market.confirm.submit')}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

function BulkCancelConfirmModal({
  count,
  submitting,
  onCancel,
  onConfirm,
  t,
}: {
  count: number
  submitting: boolean
  onCancel: () => void
  onConfirm: () => void
  t: (key: string, vars?: Record<string, string | number>) => string
}) {
  useEffect(() => {
    const api = (window as unknown as { electronAPI?: { setBinanceViewVisible?: (v: boolean) => void } }).electronAPI
    api?.setBinanceViewVisible?.(false)
    return () => { api?.setBinanceViewVisible?.(true) }
  }, [])

  return createPortal(
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-[#1E2026] border border-[#474D57] rounded-lg shadow-xl w-80 p-5 space-y-4">
        <div className="text-[13px] font-bold text-[#EAECEF]">{t('pos.cancelAllConfirmTitle')}</div>
        <p className="text-[11px] leading-relaxed text-[#B7BDC6]">{t('pos.cancelAllConfirmMessage', { count })}</p>
        <div className="grid grid-cols-2 gap-2 pt-1">
          <button
            onClick={onCancel}
            disabled={submitting}
            className="py-2 text-[12px] font-medium rounded border border-[#474D57] text-[#848E9C] hover:text-[#EAECEF] hover:border-[#848E9C] transition-colors disabled:opacity-40"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={onConfirm}
            disabled={submitting}
            className="py-2 text-[12px] font-medium rounded border border-[#f44] text-[#f44] hover:bg-[#f44] hover:text-white transition-colors disabled:opacity-40"
          >
            {submitting ? t('pos.cancelAllLoading') : t('pos.cancelAllConfirmAction')}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

function AmendOrderModal({
  order,
  position,
  submitting,
  onCancel,
  onConfirm,
  t,
}: {
  order: Order
  position: Position | null
  submitting: boolean
  onCancel: () => void
  onConfirm: (quantity: number, price: number) => void
  t: (key: string, vars?: Record<string, string | number>) => string
}) {
  const [quantityInput, setQuantityInput] = useState(String(order.quantity))
  const [priceInput, setPriceInput] = useState(order.price ? String(order.price) : '')
  const filledQuantity = order.filled_qty ?? 0

  useEffect(() => {
    const api = (window as unknown as { electronAPI?: { setBinanceViewVisible?: (v: boolean) => void } }).electronAPI
    api?.setBinanceViewVisible?.(false)
    return () => { api?.setBinanceViewVisible?.(true) }
  }, [])

  const quantity = Number.parseFloat(quantityInput)
  const price = Number.parseFloat(priceInput)
  const remainingQuantity = Number.isFinite(quantity) ? quantity - filledQuantity : Number.NaN
  const isValid = Number.isFinite(quantity) && quantity > filledQuantity && Number.isFinite(price) && price > 0
  const entryPrice = position?.entry_price ?? null
  const estimateQuantity = Number.isFinite(remainingQuantity) && remainingQuantity > 0 ? remainingQuantity : null
  const estimatedPnl = position && entryPrice && estimateQuantity && Number.isFinite(price)
    ? (position.side === 'LONG'
      ? (price - entryPrice) * estimateQuantity
      : (entryPrice - price) * estimateQuantity)
    : null
  const amendOutcomeType = estimatedPnl == null
    ? null
    : estimatedPnl >= 0
      ? t('type.takeProfit')
      : t('order.stopLoss')
  const { quoteAsset } = splitTradingSymbol(order.symbol)

  return createPortal(
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-[#1E2026] border border-[#474D57] rounded-lg shadow-xl w-[360px] p-5 space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-[13px] font-bold text-[#EAECEF]">{t('pos.amendTitle')}</div>
            <div className="mt-1 text-[11px] text-[#848E9C]">{order.symbol} · {order.side === 'BUY' ? t('side.buy') : t('side.sell')} · {t('type.limit')}</div>
          </div>
          <span className="rounded bg-[#2B3139] px-2 py-0.5 text-[10px] font-mono text-[#B7BDC6]">#{order.exchange_order_id ?? '—'}</span>
        </div>

        <div className="grid gap-3">
          <label className="grid gap-1.5 text-[11px] text-[#B7BDC6]">
            <span>{t('pos.amendTargetQuantity')}</span>
            <input
              value={quantityInput}
              onChange={(event) => setQuantityInput(event.target.value)}
              inputMode="decimal"
              className="rounded border border-[#3C4149] bg-[#181A20] px-3 py-2 text-[12px] text-[#EAECEF] outline-none transition-colors focus:border-[#F0B90B]"
            />
          </label>
          <label className="grid gap-1.5 text-[11px] text-[#B7BDC6]">
            <span>{t('log.price')}</span>
            <input
              value={priceInput}
              onChange={(event) => setPriceInput(event.target.value)}
              inputMode="decimal"
              className="rounded border border-[#3C4149] bg-[#181A20] px-3 py-2 text-[12px] text-[#EAECEF] outline-none transition-colors focus:border-[#F0B90B]"
            />
          </label>
        </div>

        <div className="grid gap-1 rounded border border-[#2B3139] bg-[#181A20] px-3 py-2 text-[11px] text-[#B7BDC6]">
          <div className="flex items-center justify-between gap-3">
            <span>{t('pos.amendFilled')}</span>
            <span className="font-mono text-[#EAECEF]">{filledQuantity}</span>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span>{t('pos.amendRemaining')}</span>
            <span className={`font-mono ${Number.isFinite(remainingQuantity) && remainingQuantity > 0 ? 'text-[#F0B90B]' : 'text-[#f6465d]'}`}>
              {Number.isFinite(remainingQuantity) ? remainingQuantity : '—'}
            </span>
          </div>
          {position && entryPrice ? (
            <>
              <div className="flex items-center justify-between gap-3">
                <span>{t('pos.entry')}</span>
                <span className="font-mono text-[#EAECEF]">{entryPrice.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 8 })}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span>{t('pos.tpSl')}</span>
                <span className={`font-medium ${estimatedPnl == null ? 'text-[#848E9C]' : estimatedPnl >= 0 ? 'text-[#0ECB81]' : 'text-[#F6465D]'}`}>
                  {amendOutcomeType ?? '—'}
                </span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span>{t('pos.estimatedPnl')}</span>
                <span className={`font-mono ${estimatedPnl == null ? 'text-[#848E9C]' : estimatedPnl >= 0 ? 'text-[#0ECB81]' : 'text-[#F6465D]'}`}>
                  {estimatedPnl == null
                    ? '—'
                    : `${estimatedPnl >= 0 ? '+' : ''}${estimatedPnl.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 8 })} ${quoteAsset}`}
                </span>
              </div>
            </>
          ) : null}
        </div>

        <p className="text-[11px] leading-relaxed text-[#848E9C]">{t('pos.amendHint')}</p>
        {!isValid && Number.isFinite(quantity) ? (
          <p className="text-[11px] leading-relaxed text-[#f6465d]">{t('pos.amendTargetQuantityError')}</p>
        ) : null}

        <div className="grid grid-cols-2 gap-2 pt-1">
          <button
            onClick={onCancel}
            disabled={submitting}
            className="py-2 text-[12px] font-medium rounded border border-[#474D57] text-[#848E9C] hover:text-[#EAECEF] hover:border-[#848E9C] transition-colors disabled:opacity-40"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={() => onConfirm(quantity, price)}
            disabled={submitting || !isValid}
            className="py-2 text-[12px] font-bold rounded bg-[#F0B90B] hover:bg-[#d4a30a] text-black transition-colors disabled:opacity-40"
          >
            {submitting ? t('order.submitting') : t('common.confirm')}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

function TpSlModal({
  position,
  username,
  onClose,
  onSaved,
  showToast,
}: {
  position: Position
  username: string
  onClose: () => void
  onSaved: (posId: number, tp: number | null, sl: number | null) => void
  showToast: (type: ToastKind, msg: string) => void
}) {
  const locale = getPreferredLocale()
  const { t } = useTranslation(locale)
  const { quoteAsset } = splitTradingSymbol(position.symbol)
  const leverage = readStoredLeverage(username, position.symbol) ?? position.leverage
  const [tpInput, setTpInput] = useState(position.tp_price ? String(position.tp_price) : '')
  const [slInput, setSlInput] = useState(position.sl_price ? String(position.sl_price) : '')
  const [submitting, setSubmitting] = useState(false)

  // Hide BrowserView (native layer) while modal is open, same as LoginModal pattern
  useEffect(() => {
    const api = (window as unknown as { electronAPI?: { setBinanceViewVisible?: (v: boolean) => void } }).electronAPI
    api?.setBinanceViewVisible?.(false)
    return () => { api?.setBinanceViewVisible?.(true) }
  }, [])

  const entryPrice = position.entry_price

  function calcPnl(triggerPrice: number): number | null {
    if (!entryPrice || !triggerPrice) return null
    const qty = position.quantity
    if (position.side === 'LONG') return (triggerPrice - entryPrice) * qty
    return (entryPrice - triggerPrice) * qty
  }

  const tpVal = parseFloat(tpInput)
  const slVal = parseFloat(slInput)
  const tpPnl = Number.isFinite(tpVal) && tpVal > 0 ? calcPnl(tpVal) : null
  const slPnl = Number.isFinite(slVal) && slVal > 0 ? calcPnl(slVal) : null

  const handleConfirm = async () => {
    const tp = Number.isFinite(tpVal) && tpVal > 0 ? tpVal : null
    const sl = Number.isFinite(slVal) && slVal > 0 ? slVal : null
    setSubmitting(true)
    try {
      await api.setPositionTpSl(position.id, tp, sl)
      onSaved(position.id, tp, sl)
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || (err as { message?: string })?.message || 'Failed'
      showToast('error', msg)
    } finally {
      setSubmitting(false)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div
        className="bg-[#1E2026] border border-[#2B2F36] rounded-lg w-[420px] shadow-2xl"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-[#2B2F36]">
          <span className="text-[13px] font-semibold text-[#EAECEF]">{t('pos.tpSlEntirePosition')}</span>
          <button onClick={onClose} className="text-[#848E9C] hover:text-[#EAECEF] text-lg leading-none">×</button>
        </div>

        {/* Warning banner */}
        <div className="mx-4 mt-3 px-3 py-2 rounded border border-[#2B2F36] bg-[#2B2F36]/50 flex items-start gap-2">
          <span className="text-[#848E9C] text-[11px] mt-0.5 shrink-0">ⓘ</span>
          <span className="text-[10px] text-[#848E9C] leading-relaxed">
            {t('pos.tpSlWarning')}
          </span>
        </div>

        {/* Info row */}
        <div className="px-4 pt-3 pb-2 space-y-1.5">
          <div className="flex justify-between text-[11px]">
            <span className="text-[#848E9C]">{t('pos.symbol')}</span>
            <span className={`font-semibold ${position.side === 'LONG' ? 'text-[#0ECB81]' : 'text-[#F6465D]'}`}>
              {position.symbol} {t('order.perpetual')} / {position.side === 'LONG' ? t('pos.long') : t('pos.short')} {leverage}×
            </span>
          </div>
          <div className="flex justify-between text-[11px]">
            <span className="text-[#848E9C]">{t('pos.entry')}</span>
            <span className="text-[#EAECEF] font-mono">{entryPrice != null ? entryPrice.toFixed(1) : '—'} {quoteAsset}</span>
          </div>
        </div>

        <div className="border-t border-[#2B2F36] mx-4 mb-1" />

        {/* Column labels */}
        <div className="grid grid-cols-2 gap-2 px-4 pt-2 pb-1">
          <span className="text-[10px] text-[#848E9C]">{t('order.triggerPrice')} ({quoteAsset})</span>
          <span className="text-[10px] text-[#848E9C]">{t('pos.estimatedPnl')} ({quoteAsset})</span>
        </div>

        {/* Take Profit */}
        <div className="px-4 pb-1">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[12px] font-semibold text-[#EAECEF]">{t('order.takeProfit')}</span>
            {tpInput && (
              <button onClick={() => setTpInput('')} className="text-[10px] text-[#F0B90B] hover:underline">{t('common.cancel')}</button>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <input
              type="number"
              value={tpInput}
              onChange={e => setTpInput(e.target.value)}
              placeholder="—"
              className="bg-[#2B2F36] border border-[#3C4149] focus:border-[#F0B90B] text-[12px] text-[#EAECEF] rounded px-3 py-2 outline-none font-mono"
            />
            <div className={`bg-[#2B2F36] border border-[#3C4149] rounded px-3 py-2 font-mono text-[12px] flex items-center ${
              tpPnl == null ? 'text-[#555]' : tpPnl >= 0 ? 'text-[#0ECB81]' : 'text-[#F6465D]'
            }`}>
              {tpPnl == null
                ? '—'
                : `${tpPnl >= 0 ? '+' : ''}${tpPnl.toFixed(2)}`}
            </div>
          </div>
          <div className="mt-1.5 text-[10px] text-[#848E9C] leading-relaxed min-h-[30px]">
            {tpPnl != null
              ? <>{t('pos.when')} <span className="text-[#F0B90B] font-medium">{t('order.triggerPriceType.last')}</span> {t('pos.reaches')}{' '}
                  <span className="text-[#EAECEF]">{tpVal.toFixed(2)}</span>, {t('pos.tpTriggerHint')}{' '}
                  <span className={tpPnl >= 0 ? 'text-[#0ECB81]' : 'text-[#F6465D]'}>
                    {tpPnl >= 0 ? '+' : ''}{tpPnl.toFixed(2)} {quoteAsset}
                  </span>.</>
              : <span className="text-[#555]">{t('pos.enterTriggerPriceHint')}</span>
            }
          </div>
        </div>

        {/* Stop Loss */}
        <div className="px-4 pt-2 pb-3">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[12px] font-semibold text-[#EAECEF]">{t('order.stopLoss')}</span>
            {slInput && (
              <button onClick={() => setSlInput('')} className="text-[10px] text-[#F0B90B] hover:underline">{t('common.cancel')}</button>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <input
              type="number"
              value={slInput}
              onChange={e => setSlInput(e.target.value)}
              placeholder="—"
              className="bg-[#2B2F36] border border-[#3C4149] focus:border-[#F0B90B] text-[12px] text-[#EAECEF] rounded px-3 py-2 outline-none font-mono"
            />
            <div className={`bg-[#2B2F36] border border-[#3C4149] rounded px-3 py-2 font-mono text-[12px] flex items-center ${
              slPnl == null ? 'text-[#555]' : slPnl >= 0 ? 'text-[#0ECB81]' : 'text-[#F6465D]'
            }`}>
              {slPnl == null
                ? '—'
                : `${slPnl >= 0 ? '+' : ''}${slPnl.toFixed(2)}`}
            </div>
          </div>
          <div className="mt-1.5 text-[10px] text-[#848E9C] leading-relaxed min-h-[30px]">
            {slPnl != null
              ? <>{t('pos.when')} <span className="text-[#F0B90B] font-medium">{t('order.triggerPriceType.last')}</span> {t('pos.reaches')}{' '}
                  <span className="text-[#EAECEF]">{slVal.toFixed(2)}</span>, {t('pos.slTriggerHint')}{' '}
                  <span className={slPnl >= 0 ? 'text-[#0ECB81]' : 'text-[#F6465D]'}>
                    {slPnl >= 0 ? '+' : ''}{slPnl.toFixed(2)} {quoteAsset}
                  </span>.</>
              : <span className="text-[#555]">{t('pos.enterTriggerPriceHint')}</span>
            }
          </div>
        </div>

        {/* Confirm */}
        <div className="px-4 pb-4 border-t border-[#2B2F36] pt-3">
          <button
            onClick={() => void handleConfirm()}
            disabled={submitting || (!tpInput && !slInput)}
            className="w-full py-3 rounded text-[13px] font-bold bg-[#F0B90B] text-black hover:bg-[#d4a30a] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {submitting ? '...' : t('common.confirm')}
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}

function formatTimestamp(value?: string) {
  return formatUtcTimestampToLocalString(value)
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

function formatConditionalAction(order: ApiConditionalOrder, t: (key: string) => string) {
  const side = order.side.toUpperCase()
  const tradeDirection = (order.trade_direction ?? '').toUpperCase()

  if (tradeDirection === 'OPEN') {
    return side === 'BUY' ? t('order.openLong') : t('order.openShort')
  }
  if (tradeDirection === 'CLOSE') {
    return side === 'BUY' ? t('order.closeShort') : t('order.closeLong')
  }

  if (side === 'BUY' && order.position_side === 'LONG') return t('order.openLong')
  if (side === 'SELL' && order.position_side === 'SHORT') return t('order.openShort')
  if (side === 'BUY' && order.position_side === 'SHORT') return t('order.closeShort')
  if (side === 'SELL' && order.position_side === 'LONG') return t('order.closeLong')
  return side
}

function getConditionalTriggerOperator(order: ApiConditionalOrder) {
  const side = order.side.toUpperCase()
  if (order.order_type === 'TAKE_PROFIT_MARKET') {
    return side === 'BUY' ? '<=' : '>='
  }
  if (order.order_type === 'STOP_MARKET') {
    return side === 'BUY' ? '>=' : '<='
  }
  return '—'
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

function formatPositionMode(positionMode: string, t: (key: string) => string) {
  if (positionMode === 'SINGLE') return t('pos.positionMode.single')
  if (positionMode === 'DUAL') return t('pos.positionMode.dual')
  return positionMode || '-'
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

function getLiveReferencePrice(position: Position, activeSymbol: string, livePrice: number | null) {
  if (position.symbol.toUpperCase() !== activeSymbol.toUpperCase()) return null
  return livePrice
}

function getLiveUnrealizedPnl(position: Position, activeSymbol: string, livePrice: number | null) {
  const referencePrice = getLiveReferencePrice(position, activeSymbol, livePrice)
  if (referencePrice == null || position.entry_price == null) return position.unrealized_pnl
  if (position.side === 'LONG') return position.quantity * (referencePrice - position.entry_price)
  if (position.side === 'SHORT') return position.quantity * (position.entry_price - referencePrice)
  return position.unrealized_pnl
}

function formatUnrealizedPnl(position: Position, activeSymbol: string, livePrice: number | null) {
  const pnl = getLiveUnrealizedPnl(position, activeSymbol, livePrice)
  if (pnl == null) return '-'
  return `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}`
}

function formatPositionSize(position: Position, sizeUnit: 'QUOTE' | 'BASE', activeSymbol: string, livePrice: number | null) {
  const { baseAsset, quoteAsset } = splitTradingSymbol(position.symbol)

  if (sizeUnit === 'BASE') {
    return `${position.quantity.toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 })} ${baseAsset}`
  }

  const liveReferencePrice = getLiveReferencePrice(position, activeSymbol, livePrice)
  const price: number = typeof liveReferencePrice === 'number' && Number.isFinite(liveReferencePrice)
    ? liveReferencePrice
    : (typeof position.entry_price === 'number' && Number.isFinite(position.entry_price) ? position.entry_price : 0)
  const quoteValue = position.quantity * price
  if (!quoteValue) return `— ${quoteAsset}`
  return `${quoteValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${quoteAsset}`
}

function formatOrderSize(order: Order, sizeUnit: 'QUOTE' | 'BASE', activeSymbol: string, livePrice: number | null) {
  const { baseAsset, quoteAsset } = splitTradingSymbol(order.symbol)

  if (sizeUnit === 'BASE') {
    return `${order.quantity.toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 })} ${baseAsset}`
  }

  const liveReferencePrice = order.symbol.toUpperCase() === activeSymbol.toUpperCase() ? livePrice : null
  const referencePrice = typeof order.avg_price === 'number' && Number.isFinite(order.avg_price) && order.avg_price > 0
    ? order.avg_price
    : typeof order.price === 'number' && Number.isFinite(order.price) && order.price > 0
      ? order.price
      : typeof order.stop_price === 'number' && Number.isFinite(order.stop_price) && order.stop_price > 0
        ? order.stop_price
        : (typeof liveReferencePrice === 'number' && Number.isFinite(liveReferencePrice) ? liveReferencePrice : 0)
  const quoteValue = order.quantity * referencePrice
  if (!quoteValue) return `— ${quoteAsset}`
  return `${quoteValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${quoteAsset}`
}

function formatPositionHistorySize(positionHistory: PositionHistory, sizeUnit: 'QUOTE' | 'BASE') {
  const { baseAsset, quoteAsset } = splitTradingSymbol(positionHistory.symbol)

  if (sizeUnit === 'BASE') {
    return `${positionHistory.quantity.toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 })} ${baseAsset}`
  }

  const referencePrice = typeof positionHistory.entry_price === 'number' && Number.isFinite(positionHistory.entry_price) && positionHistory.entry_price > 0
    ? positionHistory.entry_price
    : (typeof positionHistory.close_price === 'number' && Number.isFinite(positionHistory.close_price) ? positionHistory.close_price : 0)
  const quoteValue = positionHistory.quantity * referencePrice
  if (!quoteValue) return `— ${quoteAsset}`
  return `${quoteValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${quoteAsset}`
}

function formatConditionalOrderSize(order: ApiConditionalOrder, sizeUnit: 'QUOTE' | 'BASE', activeSymbol: string, livePrice: number | null) {
  const { baseAsset, quoteAsset } = splitTradingSymbol(order.symbol)

  if (sizeUnit === 'BASE') {
    return `${order.quantity.toLocaleString('en-US', { minimumFractionDigits: 3, maximumFractionDigits: 3 })} ${baseAsset}`
  }

  const liveReferencePrice = order.symbol.toUpperCase() === activeSymbol.toUpperCase() ? livePrice : null
  const referencePrice = typeof order.trigger_price === 'number' && Number.isFinite(order.trigger_price) && order.trigger_price > 0
    ? order.trigger_price
    : (typeof liveReferencePrice === 'number' && Number.isFinite(liveReferencePrice) ? liveReferencePrice : 0)
  const quoteValue = order.quantity * referencePrice

  if (!quoteValue) return `— ${quoteAsset}`
  return `${quoteValue.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${quoteAsset}`
}
