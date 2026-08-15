import { useState, useEffect, type ReactNode } from 'react'
import { Calendar, Download } from 'lucide-react'
import { api } from '../api/client'
import { useAuthStore } from '../store/authStore'
import { useToastStore } from '../store/toastStore'
import { Locale, useTranslation } from '../i18n/translations'
import { parseUtcTimestamp } from '../utils/datetime'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'

interface Order {
  id: number; symbol: string; side: string; order_type: string
  order_category?: string | null
  trade_direction?: string | null
  quantity: number; price: number; status: string; username?: string
  filled_qty?: number
  avg_price?: number | null
  realized_pnl?: number | null
  commission?: number | null
  commission_asset?: string | null
  stop_price?: number | null
  algo_id?: string | null
  exchange_order_id?: string; created_at?: string; updated_at?: string | null; error_message?: string
  source?: 'trade_relay' | 'external'
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
  tradeDirection: '' | 'OPEN' | 'CLOSE'
}

type Translate = (key: string, vars?: Record<string, string | number>) => string

const INITIAL_FILTERS: OrderFilters = {
  username: '',
  orderId: '',
  startTime: '',
  endTime: '',
  status: '',
  tradeDirection: '',
}

const STATUS_OPTIONS = ['NEW', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'REJECTED', 'EXPIRED', 'FAILED', 'ERROR', 'MOCK', 'PENDING'] as const

export function OrderLogScreen() {
  const locale = useUiPreferencesStore((state) => state.locale)
  const { t } = useTranslation(locale)
  const showToast = useToastStore((state) => state.showToast)
  const [orders, setOrders] = useState<Order[]>([])
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState(false)
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
        trade_direction: nextFilters.tradeDirection || undefined,
      })
      setOrders(data)
    } catch { /* ignore */ }
    setLoading(false)
  }

  useEffect(() => {
    if (!user) {
      setOrders([])
      setLoading(false)
      return
    }
    void load()
  }, [user])
  useEffect(() => {
    if (!user) return

    const loadUsers = async () => {
      try {
        const users = await api.getOrderUsers()
        setUserOptions(users.map((item: UserOption) => ({ id: item.id, username: item.username })))
      } catch {}
    }

    loadUsers()
  }, [user])

  useEffect(() => {
    if (!user) return
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [filters, user])

  const handleSearch = (event: React.FormEvent) => {
    event.preventDefault()
    load(filters)
  }

  const handleClear = () => {
    setFilters(INITIAL_FILTERS)
    load(INITIAL_FILTERS)
  }

  const handleExport = async () => {
    if (exporting) return
    setExporting(true)
    try {
      const data = await api.getOrders({
        limit: EXPORT_LIMIT,
        username: filters.username.trim() || undefined,
        order_id: filters.orderId.trim() || undefined,
        start_time: toBackendDateTime(filters.startTime),
        end_time: toBackendDateTime(filters.endTime),
        status: filters.status || undefined,
        trade_direction: filters.tradeDirection || undefined,
      })
      if (data.length === 0) {
        showToast('info', t('log.export.empty'))
        return
      }
      const XLSX = await import('xlsx')
      const worksheet = XLSX.utils.json_to_sheet(data.map((order, index) => buildExportRow(order, index, t)))
      // 加 BOM 头，保证 Excel 打开时中文不乱码
      const csv = '\uFEFF' + XLSX.utils.sheet_to_csv(worksheet)
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `order_log_${formatFileTimestamp(new Date())}.csv`
      anchor.click()
      URL.revokeObjectURL(url)
      showToast('success', t('log.export.success', { count: data.length }))
    } catch {
      showToast('error', t('log.export.failed'))
    } finally {
      setExporting(false)
    }
  }

  const formatNotional = (order: Order) => {
    const refPrice = order.avg_price ?? (order.price && order.price > 0 ? order.price : null)
    if (refPrice == null) return '—'
    return (order.quantity * refPrice).toFixed(2)
  }

  const handleCopy = async (value: string | null | undefined, label: string) => {
    const text = value?.trim()
    if (!text) return

    try {
      await navigator.clipboard.writeText(text)
      showToast('success', `${label} ${t('common.copied')}`)
    } catch {
      showToast('error', t('common.copyFailed'))
    }
  }

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e]">
      <div className="px-4 py-2 border-b border-[#3e3e42] flex items-center gap-3 shrink-0">
        <span className="text-sm font-semibold text-[#cccccc]">{t('log.title')}</span>
        <span className="text-xs text-[#858585]">{t('log.allUsers')}</span>
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
        <FilterField label={t('log.dir')} className="w-[160px]">
          <select
            value={filters.tradeDirection}
            onChange={(event) => setFilters((current) => ({
              ...current,
              tradeDirection: event.target.value as '' | 'OPEN' | 'CLOSE',
            }))}
            className={INPUT_CLS}
          >
            <option value="">{t('log.filter.allDirections')}</option>
            <option value="OPEN">{t('order.open')}</option>
            <option value="CLOSE">{t('order.close')}</option>
          </select>
        </FilterField>
        <div className="flex items-end gap-2">
          <button type="submit" className="h-9 rounded bg-[#2f7cf6] px-3 text-sm text-white hover:bg-[#4b90fb]">
            {t('log.filter.search')}
          </button>
          <button type="button" onClick={handleClear} className="h-9 rounded border border-[#3e3e42] px-3 text-sm text-[#c5ccd8] hover:bg-[#252b36]">
            {t('log.filter.clear')}
          </button>
          <button
            type="button"
            onClick={() => void handleExport()}
            disabled={exporting}
            className="flex h-9 items-center gap-1.5 rounded border border-[#3e3e42] px-3 text-sm text-[#c5ccd8] hover:bg-[#252b36] disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Download size={14} />
            {exporting ? t('log.export.exporting') : t('log.filter.export')}
          </button>
        </div>
      </form>
      <div className="flex-1 overflow-auto">
        <table className="trade-table w-full">
          <thead><tr>
            <th>{t('log.index')}</th><th className="w-[76px]">{t('log.user')}</th><th>{t('log.symbol')}</th>
            <th>{t('log.createdAt')}</th><th>{t('log.updatedAt')}</th><th>{t('log.side')}</th><th>{t('log.type')}</th><th>{t('log.qty')}</th><th>{t('log.dir')}</th><th>{t('log.price')}</th><th className="min-w-[180px]">{t('pos.triggerConditions')}</th><th>{t('log.filledPrice')}</th><th>{t('log.notional')}</th><th>{t('log.realizedPnl')}</th><th>{t('trade.commission')}</th><th>{t('trade.commissionAsset')}</th><th>{t('log.status')}</th><th className="min-w-[160px]">{t('log.algoId')}</th><th className="min-w-[160px]">{t('log.id')}</th><th className="min-w-[320px]">{t('log.errorMessage')}</th>
          </tr></thead>
          <tbody>
            {orders.length === 0 ? (
              <tr><td colSpan={20} className="text-center text-[#858585] py-6">{t('log.empty')}</td></tr>
            ) : orders.map((o, i) => (
              <tr key={o.id}>
                <td className="text-[#858585]">{i + 1}</td>
                <td className="w-[76px] text-[#cccccc] truncate">{o.username ?? '—'}</td>
                <td className="font-semibold">
                  {o.symbol}
                  {o.source === 'external' && (
                    <span className="ml-1 rounded px-1 py-0.5 text-[9px] font-medium bg-[#2B3139] text-[#F0B90B] border border-[#F0B90B33]">{t('order.source.external')}</span>
                  )}
                </td>
                <td className="text-[#858585] whitespace-nowrap">{formatLogTimestamp(o.created_at)}</td>
                <td className="text-[#858585] whitespace-nowrap">{formatLogTimestamp(o.updated_at || undefined)}</td>
                <td className={o.side === 'BUY' ? 'text-buy font-semibold' : 'text-sell font-semibold'}>{formatOrderSide(o.side, t)}</td>
                <td className="text-[#858585]">{formatOrderType(o.order_type, t)}</td>
                <td className="font-mono">{o.quantity}</td>
                <td className={`font-medium ${o.trade_direction === 'CLOSE' ? 'text-[#f6465d]' : o.trade_direction === 'OPEN' ? 'text-[#0ecb81]' : 'text-[#858585]'}`}>
                  {o.trade_direction === 'CLOSE' ? t('order.close') : o.trade_direction === 'OPEN' ? t('order.open') : '—'}
                </td>
                <td className="font-mono">{o.price ? o.price.toFixed(2) : t('log.market')}</td>
                <td className="font-mono text-[11px] whitespace-nowrap">
                  {renderTriggerCondition(o, t)}
                </td>
                <td className="font-mono">{o.avg_price != null ? o.avg_price.toFixed(2) : '—'}</td>
                <td className="font-mono">{formatNotional(o)}</td>
                <td className={`font-mono ${Number(o.realized_pnl ?? 0) > 0 ? 'text-buy' : Number(o.realized_pnl ?? 0) < 0 ? 'text-sell' : 'text-[#858585]'}`}>
                  {formatSignedNumber(o.realized_pnl, 4)}
                </td>
                <td className="font-mono text-[#858585]">{formatNumber(o.commission, 4)}</td>
                <td className="font-mono text-[#858585]">{o.commission_asset ?? '—'}</td>
                <td><StatusBadge status={o.status} t={t} /></td>
                <td className="min-w-[160px] font-mono whitespace-nowrap">
                  <CopyValueButton
                    value={o.algo_id}
                    title={t('log.algoId')}
                    emptyPlaceholder="--"
                    onCopy={handleCopy}
                    hint={t('common.clickToCopy')}
                  />
                </td>
                <td className="min-w-[160px] font-mono whitespace-nowrap">
                  <CopyValueButton
                    value={o.exchange_order_id}
                    title={t('log.id')}
                    emptyPlaceholder="--"
                    onCopy={handleCopy}
                    hint={t('common.clickToCopy')}
                  />
                </td>
                <td className="min-w-[320px] text-[#858585]">
                  <div className="max-w-[420px] truncate" title={o.error_message ?? '--'}>
                    {formatErrorMessage(o.error_message)}
                  </div>
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

function CopyValueButton({
  value,
  title,
  emptyPlaceholder,
  onCopy,
  hint,
}: {
  value?: string | null
  title: string
  emptyPlaceholder: string
  onCopy: (value: string | null | undefined, title: string) => void
  hint: string
}) {
  const text = value?.trim()

  if (!text) {
    return <span className="text-[#858585]">{emptyPlaceholder}</span>
  }

  return (
    <button
      type="button"
      onClick={() => void onCopy(text, title)}
      title={`${hint}: ${text}`}
      className="text-[#7fb2ff] hover:text-[#a9cbff] transition-colors underline-offset-2 hover:underline"
    >
      {text}
    </button>
  )
}

function StatusBadge({ status, t }: { status: string; t: Translate }) {
  const cls = status === 'FILLED' ? 'badge-filled'
    : status === 'MOCK' ? 'badge-mock'
    : status === 'FAILED' ? 'badge-failed'
    : 'badge-pending'
  return <span className={`badge ${cls}`}>{formatOrderStatus(status, t)}</span>
}

function formatOrderSide(side: string, t: Translate) {
  if (side === 'BUY') return t('side.buy')
  if (side === 'SELL') return t('side.sell')
  return side
}

function formatOrderType(orderType: string, t: Translate) {
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

function renderTriggerCondition(order: Order, t: Translate): ReactNode {
  if (order.stop_price == null || order.stop_price <= 0) return '—'

  const upperCategory = String(order.order_category || '').toUpperCase()
  const upperType = String(order.order_type || '').toUpperCase()
  const isConditional = upperCategory === 'CONDITIONAL'
    || upperType.includes('STOP')
    || upperType.includes('TAKE_PROFIT')

  if (!isConditional) return '—'

  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="text-[#9aa3b2]">{t('pos.lastPrice')}</span>
      <span className="font-semibold text-[#d8dee9]">{getTriggerOperator(order)}</span>
      <span className="font-semibold text-[#f0b90b]">
        {order.stop_price.toLocaleString('en-US', { minimumFractionDigits: 1 })}
      </span>
    </span>
  )
}

function getTriggerOperator(order: Order) {
  const side = String(order.side || '').toUpperCase()
  const orderType = String(order.order_type || '').toUpperCase()

  if (orderType === 'TAKE_PROFIT' || orderType === 'TAKE_PROFIT_MARKET') {
    return side === 'BUY' ? '<=' : '>='
  }
  if (orderType === 'STOP' || orderType === 'STOP_MARKET') {
    return side === 'BUY' ? '>=' : '<='
  }
  return '—'
}

function formatOrderStatus(status: string, t: Translate) {
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

function formatErrorMessage(value?: string) {
  if (!value) return '--'
  const trimmed = value.trim()
  if (!trimmed) return '--'
  const maxLength = 96
  return trimmed.length > maxLength ? `${trimmed.slice(0, maxLength - 1)}…` : trimmed
}

function formatNumber(value?: number | null, decimals = 4) {
  return value != null ? value.toFixed(decimals) : '—'
}

function formatSignedNumber(value?: number | null, decimals = 4) {
  if (value == null) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(decimals)}`
}

function toBackendDateTime(value: string) {
  if (!value) return undefined
  return `${value.replace('T', ' ')}:00`
}

function formatLogTimestamp(value?: string) {
  if (!value) return '-'
  const date = parseUtcTimestamp(value)
  if (!date) return value

  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  const seconds = String(date.getSeconds()).padStart(2, '0')
  return `${month}/${day}/${year}, ${hours}:${minutes}:${seconds}`
}

const EXPORT_LIMIT = 5000

function buildExportRow(order: Order, index: number, t: Translate) {
  const refPrice = order.avg_price ?? (order.price && order.price > 0 ? order.price : null)
  return {
    [t('log.index')]: index + 1,
    [t('log.user')]: order.username ?? '',
    [t('log.symbol')]: order.source === 'external'
      ? `${order.symbol} (${t('order.source.external')})`
      : order.symbol,
    [t('log.createdAt')]: formatLogTimestamp(order.created_at),
    [t('log.updatedAt')]: formatLogTimestamp(order.updated_at || undefined),
    [t('log.side')]: formatOrderSide(order.side, t),
    [t('log.type')]: formatOrderType(order.order_type, t),
    [t('log.qty')]: order.quantity,
    [t('log.dir')]: order.trade_direction === 'CLOSE' ? t('order.close') : order.trade_direction === 'OPEN' ? t('order.open') : '',
    [t('log.price')]: order.price && order.price > 0 ? order.price : t('log.market'),
    [t('pos.triggerConditions')]: formatTriggerConditionText(order, t),
    [t('log.filledPrice')]: order.avg_price ?? null,
    [t('log.notional')]: refPrice != null ? Number((order.quantity * refPrice).toFixed(2)) : null,
    [t('log.realizedPnl')]: order.realized_pnl ?? null,
    [t('trade.commission')]: order.commission ?? null,
    [t('trade.commissionAsset')]: order.commission_asset ?? '',
    [t('log.status')]: formatOrderStatus(order.status, t),
    [t('log.algoId')]: order.algo_id ?? '',
    [t('log.id')]: order.exchange_order_id ?? '',
    [t('log.errorMessage')]: order.error_message?.trim() || '',
  }
}

function formatTriggerConditionText(order: Order, t: Translate): string {
  if (order.stop_price == null || order.stop_price <= 0) return ''

  const upperCategory = String(order.order_category || '').toUpperCase()
  const upperType = String(order.order_type || '').toUpperCase()
  const isConditional = upperCategory === 'CONDITIONAL'
    || upperType.includes('STOP')
    || upperType.includes('TAKE_PROFIT')

  if (!isConditional) return ''

  return `${t('pos.lastPrice')} ${getTriggerOperator(order)} ${order.stop_price.toLocaleString('en-US', { minimumFractionDigits: 1 })}`
}

function formatFileTimestamp(date: Date) {
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}_${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
}
