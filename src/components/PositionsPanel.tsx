import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import { useAuthStore } from '../store/authStore'
import { useToastStore } from '../store/toastStore'
import { Locale, useTranslation } from '../i18n/translations'

const UI_LANG = (window as unknown as { electronAPI?: { uiLang?: string } }).electronAPI?.uiLang
const locale: Locale = (UI_LANG === 'en' ? 'en' : 'zh-CN')

type Tab = 'positions' | 'openOrders' | 'history' | 'tradeHistory'

interface Position {
  id: number; symbol: string; side: string; quantity: number
  entry_price: number; liquidation_price: number; unrealized_pnl: number
  leverage: number; margin_type: string; margin: number
}

interface Order {
  id: number; symbol: string; side: string; order_type: string
  quantity: number; price: number; status: string; username?: string
  created_at?: string; exchange_order_id?: string
}

interface Trade {
  id: number; symbol: string; side: string; order_type: string
  quantity: number; avg_price: number; commission: number
  commission_asset: string; created_at?: string
}

export function PositionsPanel({ refreshTrigger }: { refreshTrigger?: number }) {
  const { t } = useTranslation(locale)
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated)
  const showToast = useToastStore((state) => state.showToast)
  const [tab, setTab] = useState<Tab>('positions')
  const [positions, setPositions] = useState<Position[]>([])
  const [openOrders, setOpenOrders] = useState<Order[]>([])
  const [history, setHistory] = useState<Order[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
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
      else if (tab === 'tradeHistory') setTrades(await api.getTradeHistory())
    } catch (error: unknown) {
      const msg =
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (error as { message?: string })?.message ||
        t('order.error.failed')
      showToast('error', msg)
    }
    setLoading(false)
  }, [isAuthenticated, showToast, t, tab])

  useEffect(() => { load() }, [load, refreshTrigger])
  useEffect(() => {
    if (!isAuthenticated) return
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [isAuthenticated, load])

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
        <button onClick={load} className="ml-auto px-2 text-[#858585] hover:text-[#cccccc] text-xs pr-3">
          {loading ? t('statusbar.refreshing') : `↻ ${t('pos.refresh')}`}
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {tab === 'positions' && (
          <table className="trade-table w-full">
            <thead><tr>
              <th>{t('pos.symbol')}</th><th>{t('pos.side')}</th><th>{t('pos.size')}</th><th>{t('pos.entry')}</th>
              <th>{t('pos.liq')}</th><th>{t('pos.pnl')}</th><th>{t('pos.leverage')}</th><th>{t('pos.margin')}</th>
            </tr></thead>
            <tbody>
              {positions.length === 0
                ? <tr><td colSpan={8} className="text-center text-[#858585] py-6">{t('pos.empty')}</td></tr>
                : positions.map(p => (
                  <tr key={p.id}>
                    <td className="font-semibold">{p.symbol}</td>
                    <td className={p.side === 'LONG' ? 'text-buy' : 'text-sell'}>{p.side === 'LONG' ? t('pos.long') : t('pos.short')}</td>
                    <td className="font-mono">{p.quantity}</td>
                    <td className="font-mono">{p.entry_price.toFixed(2)}</td>
                    <td className="font-mono text-orange-400">{p.liquidation_price ? p.liquidation_price.toFixed(2) : '-'}</td>
                    <td className={`font-mono font-semibold ${p.unrealized_pnl >= 0 ? 'text-buy' : 'text-sell'}`}>
                      {p.unrealized_pnl >= 0 ? '+' : ''}{p.unrealized_pnl.toFixed(2)}
                    </td>
                    <td>{p.leverage}x</td>
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
            </tr></thead>
            <tbody>
              {(tab === 'openOrders' ? openOrders : history).length === 0
                ? <tr><td colSpan={7} className="text-center text-[#858585] py-6">{t('pos.empty')}</td></tr>
                : (tab === 'openOrders' ? openOrders : history).map(o => (
                  <tr key={o.id}>
                    <td className="text-[#858585]">{formatTimestamp(o.created_at)}</td>
                    <td className="font-semibold">{o.symbol}</td>
                    <td className={o.side === 'BUY' ? 'text-buy' : 'text-sell'}>{o.side === 'BUY' ? t('side.buy') : t('side.sell')}</td>
                    <td className="text-[#858585]">{formatOrderType(o.order_type, t)}</td>
                    <td className="font-mono">{o.quantity}</td>
                    <td className="font-mono">{o.price ? o.price.toFixed(2) : t('log.market')}</td>
                    <td><StatusBadge status={o.status} t={t} /></td>
                  </tr>
                ))
              }
            </tbody>
          </table>
        )}
        {tab === 'tradeHistory' && (
          <table className="trade-table w-full">
            <thead><tr>
              <th>{t('log.time')}</th><th>{t('log.symbol')}</th><th>{t('log.side')}</th><th>{t('pos.size')}</th>
              <th>{t('pos.entry')}</th><th>{t('trade.commission')}</th>
            </tr></thead>
            <tbody>
              {trades.length === 0
                ? <tr><td colSpan={6} className="text-center text-[#858585] py-6">{t('pos.empty')}</td></tr>
                : trades.map(t => (
                  <tr key={t.id}>
                    <td className="text-[#858585]">{formatTimestamp(t.created_at)}</td>
                    <td className="font-semibold">{t.symbol}</td>
                    <td className={t.side === 'BUY' ? 'text-buy' : 'text-sell'}>{t.side === 'BUY' ? useTranslation(locale).t('side.buy') : useTranslation(locale).t('side.sell')}</td>
                    <td className="font-mono">{t.quantity}</td>
                    <td className="font-mono">{t.avg_price ? t.avg_price.toFixed(2) : '-'}</td>
                    <td className="font-mono text-[#858585]">{t.commission} {t.commission_asset}</td>
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
