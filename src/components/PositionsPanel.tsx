import { useState, useEffect, useCallback, useRef } from 'react'
import { createPortal } from 'react-dom'
import { api, ApiConditionalOrder } from '../api/client'
import { useAuthStore } from '../store/authStore'
import { useToastStore } from '../store/toastStore'
import { Locale, useTranslation } from '../i18n/translations'
import { perfSignalDone } from '../utils/perf'

const UI_LANG = (window as unknown as { electronAPI?: { uiLang?: string } }).electronAPI?.uiLang
const locale: Locale = (UI_LANG === 'en' ? 'en' : 'zh-CN')
const POSITIONS_WS_URL = 'ws://127.0.0.1:8000/api/positions/ws'

type Tab = 'positions' | 'openOrders' | 'history' | 'tradeHistory'
const QUOTE_ASSETS = ['USDT', 'USDC', 'FDUSD', 'BUSD', 'BTC', 'ETH'] as const

interface Position {
  id: number; symbol: string; side: string; quantity: number
  entry_price: number | null; liquidation_price: number | null; unrealized_pnl: number | null
  leverage: number; margin_type: string; margin: number | null
  tp_price?: number | null; sl_price?: number | null
}

interface Order {
  id: number; symbol: string; side: string; order_type: string
  quantity: number; price: number; stop_price?: number | null
  reduce_only?: boolean; post_only?: boolean
  status: string; username?: string; created_at?: string; exchange_order_id?: string
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
  const currentUser = useAuthStore((state) => state.user)
  const showToast = useToastStore((state) => state.showToast)
  const [tab, setTab] = useState<Tab>('positions')
  const [positions, setPositions] = useState<Position[]>([])
  const [openOrders, setOpenOrders] = useState<Order[]>([])
  const [history, setHistory] = useState<Order[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [positionHistory, setPositionHistory] = useState<PositionHistory[]>([])
  const [loading, setLoading] = useState(false)
  const [cancellingId, setCancellingId] = useState<number | null>(null)
  const [conditionalOrders, setConditionalOrders] = useState<ApiConditionalOrder[]>([])
  const [cancellingAlgoId, setCancellingAlgoId] = useState<number | null>(null)
  const [openOrdersSubTab, setOpenOrdersSubTab] = useState<'basic' | 'conditional'>('basic')
  const [tpslPosition, setTpslPosition] = useState<Position | null>(null)
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
  useEffect(() => { void loadRef.current() }, [refreshTrigger, tab])

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

  const handleCancelConditional = async (o: ApiConditionalOrder) => {
    setCancellingAlgoId(o.algo_id)
    try {
      await api.cancelConditionalOrder(o.algo_id)
      showToast('Conditional order cancelled', 'success')
      setConditionalOrders(prev => prev.filter(x => x.algo_id !== o.algo_id))
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || (err as { message?: string })?.message || 'Cancel failed'
      showToast(`Cancel failed: ${msg}`, 'error')
    } finally {
      setCancellingAlgoId(null)
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
              <th>{t('pos.symbol')}</th><th>{t('pos.side')}</th><th>{t('pos.size')}</th><th>{t('pos.entry')}</th>
              <th>{t('pos.liq')}</th><th>{t('pos.pnl')}</th><th>{t('pos.margin')}</th><th>TP/SL</th>
            </tr></thead>
            <tbody>
              {positions.length === 0
                ? <tr><td colSpan={8} className="text-center text-[#858585] py-6">{t('pos.empty')}</td></tr>
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
                          title="Set TP/SL"
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
                    ? `Basic(${openOrders.length})`
                    : `Conditional(${conditionalOrders.length})`}
                </button>
              ))}
            </div>
          )}
          {(tab === 'history' || openOrdersSubTab === 'basic') && (
            <table className="trade-table w-full">
              <thead><tr>
                <th>{t('log.time')}</th><th>{t('log.symbol')}</th><th>{t('log.side')}</th><th>{t('log.type')}</th>
                <th>{t('log.qty')}</th><th>{t('log.price')}</th><th>{t('log.status')}</th>
                {tab === 'openOrders' && <th>Reduce Only</th>}
                {tab === 'openOrders' && <th>Post Only</th>}
                {tab === 'openOrders' && <th>Trigger Conditions</th>}
                {tab === 'openOrders' && <th>Order Id</th>}
                {tab === 'openOrders' && <th></th>}
              </tr></thead>
              <tbody>
                {(tab === 'openOrders' ? openOrders : history).length === 0
                  ? <tr><td colSpan={tab === 'openOrders' ? 12 : 7} className="text-center text-[#858585] py-6">{t('pos.empty')}</td></tr>
                  : (tab === 'openOrders' ? openOrders : history).map(o => (
                    <tr key={o.id}>
                      <td className="text-[#858585]">{formatTimestamp(o.created_at)}</td>
                      <td className="font-semibold">{o.symbol}</td>
                      <td className={o.side === 'BUY' ? 'text-buy' : 'text-sell'}>{o.side === 'BUY' ? t('side.buy') : t('side.sell')}</td>
                      <td className="text-[#858585]">{formatOrderType(o.order_type, t)}</td>
                      <td className="font-mono">{o.quantity}</td>
                      <td className="font-mono">{o.price ? o.price.toFixed(2) : t('log.market')}</td>
                      <td><StatusBadge status={o.status} t={t} /></td>
                      {tab === 'openOrders' && <td className="text-center">{o.reduce_only ? 'Yes' : 'No'}</td>}
                      {tab === 'openOrders' && <td className="text-center">{o.post_only ? 'Yes' : 'No'}</td>}
                      {tab === 'openOrders' && (
                        <td className="font-mono text-[11px]">
                          {o.stop_price ? `Last Price <= ${o.stop_price.toLocaleString('en-US', { minimumFractionDigits: 1 })}` : '—'}
                        </td>
                      )}
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
          {tab === 'openOrders' && openOrdersSubTab === 'conditional' && (
            <table className="trade-table w-full">
              <thead><tr>
                <th>{t('log.time')}</th>
                <th>{t('log.symbol')}</th>
                <th>Type</th>
                <th>{t('log.side')}</th>
                <th>Amount</th>
                <th>{t('log.price')}</th>
                <th>Trigger Conditions</th>
                <th></th>
              </tr></thead>
              <tbody>
                {conditionalOrders.length === 0
                  ? <tr><td colSpan={8} className="text-center text-[#858585] py-6">{t('pos.empty')}</td></tr>
                  : conditionalOrders.map(o => {
                    const isTp = o.order_type === 'TAKE_PROFIT_MARKET'
                    const isShortClose = o.side === 'BUY'
                    return (
                      <tr key={o.algo_id}>
                        <td className="text-[#858585]">{formatTimestamp(o.created_at)}</td>
                        <td className="font-semibold">{o.symbol}<br/><span className="text-[10px] text-[#858585]">Perp</span></td>
                        <td>{isTp ? 'Take Profit Market' : 'Stop Market'}</td>
                        <td className={isShortClose ? 'text-buy' : 'text-sell'}>
                          {isShortClose ? 'Close Short' : 'Close Long'}
                        </td>
                        <td className="font-mono">Close Position</td>
                        <td className="font-mono">Market</td>
                        <td className="font-mono text-[11px]">
                          Last Price {isTp ? (isShortClose ? '>=' : '<=') : (isShortClose ? '<=' : '>=')} {o.trigger_price.toLocaleString('en-US', { minimumFractionDigits: 1 })}
                        </td>
                        <td>
                          <button
                            disabled={cancellingAlgoId === o.algo_id}
                            onClick={() => void handleCancelConditional(o)}
                            className="px-2 py-0.5 text-[10px] rounded border border-[#f44] text-[#f44] hover:bg-[#f44] hover:text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            {cancellingAlgoId === o.algo_id ? '…' : 'Cancel'}
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
    </>
  )
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
  showToast: (type: string, msg: string) => void
}) {
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
          <span className="text-[13px] font-semibold text-[#EAECEF]">TP/SL for entire position</span>
          <button onClick={onClose} className="text-[#848E9C] hover:text-[#EAECEF] text-lg leading-none">×</button>
        </div>

        {/* Warning banner */}
        <div className="mx-4 mt-3 px-3 py-2 rounded border border-[#2B2F36] bg-[#2B2F36]/50 flex items-start gap-2">
          <span className="text-[#848E9C] text-[11px] mt-0.5 shrink-0">ⓘ</span>
          <span className="text-[10px] text-[#848E9C] leading-relaxed">
            In a rapidly changing market, setting a stop-loss trigger price close to the liquidation price may result in the order failing to execute.
          </span>
        </div>

        {/* Info row */}
        <div className="px-4 pt-3 pb-2 space-y-1.5">
          <div className="flex justify-between text-[11px]">
            <span className="text-[#848E9C]">Symbol</span>
            <span className={`font-semibold ${position.side === 'LONG' ? 'text-[#0ECB81]' : 'text-[#F6465D]'}`}>
              {position.symbol} Perpetual / {position.side === 'LONG' ? 'Long' : 'Short'} {leverage}×
            </span>
          </div>
          <div className="flex justify-between text-[11px]">
            <span className="text-[#848E9C]">Entry Price</span>
            <span className="text-[#EAECEF] font-mono">{entryPrice != null ? entryPrice.toFixed(1) : '—'} {quoteAsset}</span>
          </div>
        </div>

        <div className="border-t border-[#2B2F36] mx-4 mb-1" />

        {/* Column labels */}
        <div className="grid grid-cols-2 gap-2 px-4 pt-2 pb-1">
          <span className="text-[10px] text-[#848E9C]">Trigger Price ({quoteAsset})</span>
          <span className="text-[10px] text-[#848E9C]">Est. PnL ({quoteAsset})</span>
        </div>

        {/* Take Profit */}
        <div className="px-4 pb-1">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[12px] font-semibold text-[#EAECEF]">Take Profit</span>
            {tpInput && (
              <button onClick={() => setTpInput('')} className="text-[10px] text-[#F0B90B] hover:underline">Cancel</button>
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
              ? <>When <span className="text-[#F0B90B] font-medium">Last Price</span> reaches{' '}
                  <span className="text-[#EAECEF]">{tpVal.toFixed(2)}</span>, it will trigger{' '}
                  Take Profit Market order to close this position. Estimated PNL will be{' '}
                  <span className={tpPnl >= 0 ? 'text-[#0ECB81]' : 'text-[#F6465D]'}>
                    {tpPnl >= 0 ? '+' : ''}{tpPnl.toFixed(2)} {quoteAsset}
                  </span>.</>
              : <span className="text-[#555]">Enter a trigger price to calculate estimated PnL.</span>
            }
          </div>
        </div>

        {/* Stop Loss */}
        <div className="px-4 pt-2 pb-3">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[12px] font-semibold text-[#EAECEF]">Stop Loss</span>
            {slInput && (
              <button onClick={() => setSlInput('')} className="text-[10px] text-[#F0B90B] hover:underline">Cancel</button>
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
              ? <>When <span className="text-[#F0B90B] font-medium">Last Price</span> reaches{' '}
                  <span className="text-[#EAECEF]">{slVal.toFixed(2)}</span>, it will trigger{' '}
                  Stop Market order to close this position. Estimated PNL will be{' '}
                  <span className={slPnl >= 0 ? 'text-[#0ECB81]' : 'text-[#F6465D]'}>
                    {slPnl >= 0 ? '+' : ''}{slPnl.toFixed(2)} {quoteAsset}
                  </span>.</>
              : <span className="text-[#555]">Enter a trigger price to calculate estimated PnL.</span>
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
            {submitting ? '...' : 'Confirm'}
          </button>
        </div>
      </div>
    </div>,
    document.body
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
