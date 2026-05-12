import { useState, useEffect } from 'react'
import { api } from '../api/client'
import { useAuthStore } from '../store/authStore'
import { Locale, useTranslation } from '../i18n/translations'

const UI_LANG = (window as unknown as { electronAPI?: { uiLang?: string } }).electronAPI?.uiLang
const locale: Locale = (UI_LANG === 'en' ? 'en' : 'zh-CN')

interface Order {
  id: number; symbol: string; side: string; order_type: string
  quantity: number; price: number; status: string; username: string
  exchange_order_id?: string; created_at?: string; error_message?: string
}

export function OrderLogScreen() {
  const { t } = useTranslation(locale)
  const [orders, setOrders] = useState<Order[]>([])
  const [loading, setLoading] = useState(true)
  const { user } = useAuthStore()

  const load = async () => {
    setLoading(true)
    try {
      const data = await api.getOrders({ limit: 200 })
      setOrders(data)
    } catch { /* ignore */ }
    setLoading(false)
  }

  useEffect(() => { load() }, [])
  useEffect(() => {
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e]">
      <div className="px-4 py-2 border-b border-[#3e3e42] flex items-center gap-3 shrink-0">
        <span className="text-sm font-semibold text-[#cccccc]">{t('log.title')}</span>
        {user?.role === 'admin' && <span className="text-xs text-[#858585]">{t('log.allUsers')}</span>}
        <button onClick={load} className="ml-auto text-xs text-[#858585] hover:text-[#cccccc]">
          {loading ? t('log.loading') : `↻ ${t('log.refresh')}`}
        </button>
      </div>
      <div className="flex-1 overflow-auto">
        <table className="trade-table w-full">
          <thead><tr>
            <th>{t('log.index')}</th><th>{t('log.time')}</th><th>{t('log.user')}</th><th>{t('log.symbol')}</th>
            <th>{t('log.side')}</th><th>{t('log.type')}</th><th>{t('log.qty')}</th><th>{t('log.price')}</th><th>{t('log.status')}</th><th>{t('log.id')}</th>
          </tr></thead>
          <tbody>
            {orders.length === 0 ? (
              <tr><td colSpan={10} className="text-center text-[#858585] py-6">{loading ? '...' : t('log.empty')}</td></tr>
            ) : orders.map((o, i) => (
              <tr key={o.id}>
                <td className="text-[#858585]">{i + 1}</td>
                <td className="text-[#858585]">{o.created_at ? new Date(o.created_at).toLocaleString() : '-'}</td>
                <td className="text-[#cccccc]">{o.username}</td>
                <td className="font-semibold">{o.symbol}</td>
                <td className={o.side === 'BUY' ? 'text-buy font-semibold' : 'text-sell font-semibold'}>{formatOrderSide(o.side, t)}</td>
                <td className="text-[#858585]">{formatOrderType(o.order_type, t)}</td>
                <td className="font-mono">{o.quantity}</td>
                <td className="font-mono">{o.price ? o.price.toFixed(2) : t('log.market')}</td>
                <td><StatusBadge status={o.status} t={t} /></td>
                <td className="text-[#858585] font-mono truncate max-w-32" title={o.exchange_order_id}>
                  {o.exchange_order_id ? o.exchange_order_id.slice(0, 16) + '...' : o.error_message || '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function StatusBadge({ status, t }: { status: string; t: (key: string) => string }) {
  const cls = status === 'FILLED' ? 'badge-filled'
    : status === 'MOCK' ? 'badge-mock'
    : status === 'FAILED' ? 'badge-failed'
    : 'badge-pending'
  return <span className={`badge ${cls}`}>{formatOrderStatus(status, t)}</span>
}

function formatOrderSide(side: string, t: (key: string) => string) {
  if (side === 'BUY') return t('side.buy')
  if (side === 'SELL') return t('side.sell')
  return side
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

function formatOrderStatus(status: string, t: (key: string) => string) {
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
