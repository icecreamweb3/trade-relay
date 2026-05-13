import { useState, useEffect } from 'react'
import { Calendar } from 'lucide-react'
import { api } from '../api/client'
import { useAuthStore } from '../store/authStore'
import { Locale, useTranslation } from '../i18n/translations'

const UI_LANG = (window as unknown as { electronAPI?: { uiLang?: string } }).electronAPI?.uiLang
const locale: Locale = (UI_LANG === 'en' ? 'en' : 'zh-CN')

interface Order {
  id: number; symbol: string; side: string; order_type: string
  quantity: number; price: number; status: string; username?: string
  exchange_order_id?: string; created_at?: string; error_message?: string
}

interface UserOption {
  id: number
  username: string
}

interface OrderFilters {
  username: string
  orderId: string
  startTime: string
  endTime: string
  status: string
}

const INITIAL_FILTERS: OrderFilters = {
  username: '',
  orderId: '',
  startTime: '',
  endTime: '',
  status: '',
}

const STATUS_OPTIONS = ['NEW', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'REJECTED', 'EXPIRED', 'FAILED', 'ERROR', 'MOCK', 'PENDING'] as const

export function OrderLogScreen() {
  const { t } = useTranslation(locale)
  const [orders, setOrders] = useState<Order[]>([])
  const [loading, setLoading] = useState(true)
  const [filters, setFilters] = useState<OrderFilters>(INITIAL_FILTERS)
  const [userOptions, setUserOptions] = useState<UserOption[]>([])
  const { user } = useAuthStore()

  const load = async (nextFilters: OrderFilters = filters) => {
    setLoading(true)
    try {
      const data = await api.getOrders({
        limit: 200,
        username: nextFilters.username.trim() || undefined,
        order_id: nextFilters.orderId.trim() || undefined,
        start_time: toBackendDateTime(nextFilters.startTime),
        end_time: toBackendDateTime(nextFilters.endTime),
        status: nextFilters.status || undefined,
      })
      setOrders(data)
    } catch { /* ignore */ }
    setLoading(false)
  }

  useEffect(() => { load() }, [])
  useEffect(() => {
    if (!user) return

    const loadUsers = async () => {
      try {
        const users = await api.getOrderUsers()
        setUserOptions(users.map((item: UserOption) => ({ id: item.id, username: item.username })))
      } catch {
        setUserOptions([])
      }
    }

    loadUsers()
  }, [user])

  useEffect(() => {
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [filters])

  const handleSearch = (event: React.FormEvent) => {
    event.preventDefault()
    load(filters)
  }

  const handleClear = () => {
    setFilters(INITIAL_FILTERS)
    load(INITIAL_FILTERS)
  }

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e]">
      <div className="px-4 py-2 border-b border-[#3e3e42] flex items-center gap-3 shrink-0">
        <span className="text-sm font-semibold text-[#cccccc]">{t('log.title')}</span>
        <span className="text-xs text-[#858585]">{t('log.allUsers')}</span>
        <button onClick={() => load()} className="ml-auto text-xs text-[#858585] hover:text-[#cccccc]">
          {loading ? t('log.loading') : `↻ ${t('log.refresh')}`}
        </button>
      </div>
      <form onSubmit={handleSearch} className="flex flex-wrap items-end gap-3 border-b border-[#3e3e42] px-4 py-3 shrink-0">
        <FilterField label={t('log.filter.user')} className="w-[220px]">
          <select
            value={filters.username}
            onChange={(event) => setFilters((current) => ({ ...current, username: event.target.value }))}
            className={INPUT_CLS}
          >
            <option value=""></option>
            {userOptions.map((item) => (
              <option key={item.id} value={item.username}>{item.username}</option>
            ))}
          </select>
        </FilterField>
        <FilterField label={t('log.filter.orderId')} className="w-[220px]">
          <input
            type="text"
            value={filters.orderId}
            onChange={(event) => setFilters((current) => ({ ...current, orderId: event.target.value }))}
            className={INPUT_CLS}
          />
        </FilterField>
        <FilterField label={t('log.filter.startTime')} className="w-[220px]">
          <DateTimeFilterInput
            value={filters.startTime}
            onChange={(value) => setFilters((current) => ({ ...current, startTime: value }))}
          />
        </FilterField>
        <FilterField label={t('log.filter.endTime')} className="w-[220px]">
          <DateTimeFilterInput
            value={filters.endTime}
            onChange={(value) => setFilters((current) => ({ ...current, endTime: value }))}
          />
        </FilterField>
        <FilterField label={t('log.filter.status')} className="w-[200px]">
          <div className="flex gap-2">
            <select
              value={filters.status}
              onChange={(event) => setFilters((current) => ({ ...current, status: event.target.value }))}
              className={INPUT_CLS}
            >
              <option value="">{t('log.filter.allStatus')}</option>
              {STATUS_OPTIONS.map((status) => (
                <option key={status} value={status}>{formatOrderStatus(status, t)}</option>
              ))}
            </select>
          </div>
        </FilterField>
        <div className="flex items-end gap-2">
          <button type="submit" className="h-9 rounded bg-[#2f7cf6] px-3 text-sm text-white hover:bg-[#4b90fb]">
            {t('log.filter.search')}
          </button>
          <button type="button" onClick={handleClear} className="h-9 rounded border border-[#3e3e42] px-3 text-sm text-[#c5ccd8] hover:bg-[#252b36]">
            {t('log.filter.clear')}
          </button>
        </div>
      </form>
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
                <td className="text-[#cccccc]">{o.username ?? '—'}</td>
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

const INPUT_CLS = 'order-filter-input h-9 w-full rounded border border-[#3e3e42] bg-[#161a21] px-2 py-1.5 text-sm text-[#dde4ef] outline-none focus:border-[#2f7cf6]'

function FilterField({ label, children, className = '' }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <label className={`block min-w-0 ${className}`}>
      <div className="mb-1 text-xs text-[#8b94a5]">{label}</div>
      {children}
    </label>
  )
}

function DateTimeFilterInput({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <div className="relative">
      <input
        type="datetime-local"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={`${INPUT_CLS} pr-11 datetime-filter-input`}
      />
      <button
        type="button"
        aria-label="Open date time picker"
        onClick={(event) => {
          const input = event.currentTarget.previousElementSibling as HTMLInputElement | null
          if (!input) return
          if (typeof input.showPicker === 'function') {
            input.showPicker()
            return
          }
          input.focus()
          input.click()
        }}
        className="absolute right-1 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded border border-[#7aa2ff] bg-[#dbe7ff] text-[#0f172a] shadow-[0_0_0_1px_rgba(122,162,255,0.15)] hover:bg-white"
      >
        <Calendar size={15} strokeWidth={2.2} />
      </button>
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

function toBackendDateTime(value: string) {
  if (!value) return undefined
  return `${value.replace('T', ' ')}:00`
}
