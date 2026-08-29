import { useState, useEffect, useRef, type ReactNode } from 'react'
import { BarChart3, Calendar, Download, RefreshCw, X } from 'lucide-react'
import { api, type ApiOrderReconcileResult } from '../api/client'
import { useAuthStore } from '../store/authStore'
import { useToastStore } from '../store/toastStore'
import { Locale, useTranslation } from '../i18n/translations'
import { parseUtcTimestamp } from '../utils/datetime'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'
import { computeTradeAnalysis, TradeAnalysis } from '../utils/tradeAnalysis'
import { TradeAnalysisModal } from './TradeAnalysisModal'
import { OrderKlineLoadingModal, OrderKlineModal } from './OrderKlineModal'
import { findPositionWindow, type PositionWindow } from '../utils/orderChart'

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
  exchange_order_id?: string; created_at?: string; updated_at?: string | null; filled_at?: string | null; error_message?: string
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
  const [hasQueried, setHasQueried] = useState(false)
  const [analysis, setAnalysis] = useState<TradeAnalysis | null>(null)
  const [chartPosition, setChartPosition] = useState<PositionWindow | null>(null)
  const [chartLoadingOrderId, setChartLoadingOrderId] = useState<number | null>(null)
  const [chartPendingOrder, setChartPendingOrder] = useState<Order | null>(null)
  const chartRequestRef = useRef(0)
  const [reconciling, setReconciling] = useState(false)
  const [reconcileDialog, setReconcileDialog] = useState<{ result?: ApiOrderReconcileResult; error?: string } | null>(null)
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
    setHasQueried(true)
    load(filters)
  }

  const handleThisWeek = () => {
    const now = new Date()
    // 以周日为一周的起点
    const sunday = new Date(now)
    sunday.setDate(now.getDate() - now.getDay())
    sunday.setHours(0, 0, 0, 0)
    const saturday = new Date(sunday)
    saturday.setDate(sunday.getDate() + 6)
    saturday.setHours(23, 59, 59, 0)
    setFilters((current) => ({
      ...current,
      startTime: toLocalDateTimeInputValue(sunday),
      endTime: toLocalDateTimeInputValue(saturday),
    }))
  }

  const handleClear = () => {
    setFilters(INITIAL_FILTERS)
    setHasQueried(false)
    load(INITIAL_FILTERS)
  }

  const handleAnalyze = async () => {
    if (!hasQueried) {
      showToast('info', t('log.analyze.needQuery'))
      return
    }
    // 配对完整交易需要开仓+平仓全部成交单，忽略状态/开平/订单号等行级过滤，
    // 按用户与时间范围重新拉取，否则仅剩平仓单时无法配出任何交易
    try {
      const data = await api.getOrders({
        limit: EXPORT_LIMIT,
        username: filters.username.trim() || undefined,
        start_time: toBackendDateTime(filters.startTime),
        end_time: toBackendDateTime(filters.endTime),
      })
      const result = computeTradeAnalysis(data)
      if (result.fillCount === 0) {
        showToast('info', t('log.analyze.empty'))
        return
      }
      setAnalysis(result)
    } catch {
      showToast('error', t('log.analyze.failed'))
    }
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

  const handleOrderDoubleClick = async (order: Order) => {
    if (chartLoadingOrderId != null) return
    if (Number(order.filled_qty ?? 0) <= 0 || !(Number(order.avg_price) > 0)) {
      showToast('info', t('log.chart.notFilled'))
      return
    }

    const requestId = ++chartRequestRef.current
    setChartLoadingOrderId(order.id)
    setChartPosition(null)
    setChartPendingOrder(order)
    try {
      // Use the indexed user+symbol endpoint instead of scanning 5000 orders
      // from every market through a username LIKE filter.
      const history = await api.getOrderPositionContext(order.id)
      if (requestId !== chartRequestRef.current) return
      const candidates = history.some((item) => item.id === order.id) ? history : [...history, order]
      const position = findPositionWindow(candidates, order.id)
      if (!position) {
        showToast('info', t('log.chart.noPosition'))
        return
      }
      setChartPendingOrder(null)
      if (window.electronAPI?.openOrderKlineWindow) {
        await window.electronAPI.openOrderKlineWindow(position)
      } else {
        // Browser development fallback when Electron IPC is unavailable.
        setChartPosition(position)
      }
    } catch {
      if (requestId === chartRequestRef.current) showToast('error', t('log.chart.failed'))
    } finally {
      if (requestId === chartRequestRef.current) {
        setChartPendingOrder(null)
        setChartLoadingOrderId(null)
      }
    }
  }

  const handleReconcile = async () => {
    if (reconciling) return
    if (!filters.username.trim() || !filters.startTime || !filters.endTime) {
      showToast('info', t('log.reconcile.required'))
      return
    }

    const startTime = toBackendDateTime(filters.startTime)
    const endTime = toBackendDateTime(filters.endTime)
    if (!startTime || !endTime) {
      showToast('info', t('log.reconcile.required'))
      return
    }

    setReconciling(true)
    try {
      const result = await api.reconcileOrders({
        username: filters.username.trim(),
        start_time: startTime,
        end_time: endTime,
      })
      setReconcileDialog({ result })
      await load(filters)
    } catch (error) {
      const message = typeof error === 'object' && error !== null && 'message' in error
        ? String((error as { message?: unknown }).message || t('log.reconcile.failed'))
        : t('log.reconcile.failed')
      setReconcileDialog({ error: message })
    } finally {
      setReconciling(false)
    }
  }

  const closeOrderChart = () => {
    chartRequestRef.current += 1
    setChartPendingOrder(null)
    setChartLoadingOrderId(null)
    setChartPosition(null)
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
    <div className="relative isolate h-full flex flex-col overflow-hidden bg-[#1e1e1e]">
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
          <button type="button" onClick={handleThisWeek} className="h-9 rounded border border-[#3e3e42] px-3 text-sm text-[#c5ccd8] hover:bg-[#252b36]">
            {t('log.filter.thisWeek')}
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
          <button
            type="button"
            onClick={handleAnalyze}
            className="flex h-9 items-center gap-1.5 rounded border border-[#3e3e42] px-3 text-sm text-[#c5ccd8] hover:bg-[#252b36]"
          >
            <BarChart3 size={14} />
            {t('log.analyze')}
          </button>
          <button
            type="button"
            onClick={() => void handleReconcile()}
            disabled={reconciling}
            className="flex h-9 items-center gap-1.5 rounded border border-[#3e3e42] px-3 text-sm text-[#c5ccd8] hover:bg-[#252b36] disabled:cursor-wait disabled:opacity-60"
          >
            <RefreshCw size={14} className={reconciling ? 'animate-spin' : ''} />
            {reconciling ? t('log.reconcile.running') : t('log.reconcile')}
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
              <tr
                key={o.id}
                onDoubleClick={() => void handleOrderDoubleClick(o)}
                className={`cursor-pointer ${chartLoadingOrderId === o.id ? 'opacity-60' : ''}`}
              >
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
      {analysis && <TradeAnalysisModal analysis={analysis} onClose={() => setAnalysis(null)} />}
      {chartPendingOrder && <OrderKlineLoadingModal symbol={chartPendingOrder.symbol} onClose={closeOrderChart} />}
      {chartPosition && <OrderKlineModal position={chartPosition} onClose={closeOrderChart} />}
      {reconcileDialog && <OrderReconcileResultModal dialog={reconcileDialog} onClose={() => setReconcileDialog(null)} t={t} />}
    </div>
  )
}

function OrderReconcileResultModal({
  dialog,
  onClose,
  t,
}: {
  dialog: { result?: ApiOrderReconcileResult; error?: string }
  onClose: () => void
  t: Translate
}) {
  const result = dialog.result
  return (
    <div className="fixed inset-0 z-[300] flex items-center justify-center bg-black/65 px-4" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section role="dialog" aria-modal="true" className="w-full max-w-[620px] overflow-hidden rounded-lg border border-[#414956] bg-[#171b21] shadow-2xl">
        <header className="flex items-center justify-between border-b border-[#303641] px-4 py-3">
          <h2 className="text-sm font-semibold text-[#e8ebf0]">{t('log.reconcile.resultTitle')}</h2>
          <button type="button" onClick={onClose} className="rounded p-1 text-[#929baa] hover:bg-[#29303a] hover:text-white"><X size={17} /></button>
        </header>
        <div className="p-4">
          {dialog.error ? (
            <div className="rounded border border-[#f6465d]/35 bg-[#f6465d]/10 px-3 py-3 text-sm text-[#ff8292]">{dialog.error}</div>
          ) : result ? (
            <>
              <div className="mb-3 text-xs leading-5 text-[#929baa]">
                <div>{result.username} · {result.start_time} — {result.end_time}</div>
                <div>{t('log.reconcile.symbols')}: {result.symbols.join(', ') || '—'}</div>
              </div>
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
                <ReconcileStat label={t('log.reconcile.scanned')} value={result.scanned_orders} />
                <ReconcileStat label={t('log.reconcile.inserted')} value={result.inserted} tone="success" />
                <ReconcileStat label={t('log.reconcile.updated')} value={result.updated} tone="info" />
                <ReconcileStat label={t('log.reconcile.unchanged')} value={result.unchanged} />
                <ReconcileStat label={t('log.reconcile.trades')} value={result.scanned_trades} />
                <ReconcileStat label={t('log.reconcile.errors')} value={result.failed} tone={result.failed ? 'error' : undefined} />
              </div>
              {result.warnings.length > 0 && (
                <div className="mt-3 max-h-32 overflow-auto rounded border border-[#f0b90b]/25 bg-[#f0b90b]/5 px-3 py-2 text-xs leading-5 text-[#d9bd66]">
                  {result.warnings.map((warning, index) => <div key={index}>{warning}</div>)}
                </div>
              )}
            </>
          ) : null}
        </div>
        <footer className="flex justify-end border-t border-[#303641] px-4 py-3">
          <button type="button" onClick={onClose} className="rounded bg-[#2f7cf6] px-4 py-1.5 text-sm text-white hover:bg-[#4b90fb]">{t('common.close')}</button>
        </footer>
      </section>
    </div>
  )
}

function ReconcileStat({ label, value, tone }: { label: string; value: number; tone?: 'success' | 'info' | 'error' }) {
  const valueClass = tone === 'success' ? 'text-[#0ecb81]'
    : tone === 'info' ? 'text-[#4b90fb]'
      : tone === 'error' ? 'text-[#f6465d]' : 'text-[#e4e8ee]'
  return (
    <div className="rounded border border-[#303641] bg-[#101318] px-2 py-2 text-center">
      <div className={`font-mono text-lg font-semibold ${valueClass}`}>{value}</div>
      <div className="mt-0.5 truncate text-[10px] text-[#7f8998]" title={label}>{label}</div>
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
  const pickerRef = useRef<HTMLInputElement>(null)
  const [text, setText] = useState(() => formatFilterDateTimeDisplay(value))

  // 外部修改（本周/清空/选择器）时同步显示；用户输入过程中不打断
  useEffect(() => {
    setText((current) => {
      if ((parseFilterDateTimeText(current) ?? '') === value) return current
      return formatFilterDateTimeDisplay(value)
    })
  }, [value])

  const commitText = (nextText: string) => {
    setText(nextText)
    const trimmed = nextText.trim()
    if (!trimmed) {
      if (value) onChange('')
      return
    }
    const parsed = parseFilterDateTimeText(trimmed)
    if (parsed && parsed !== value) onChange(parsed)
  }

  return (
    <div className="relative">
      <input
        type="text"
        value={text}
        placeholder="YYYY/MM/DD HH:mm:ss"
        onChange={(event) => commitText(event.target.value)}
        onBlur={() => setText(formatFilterDateTimeDisplay(value))}
        className={`${INPUT_CLS} pr-11`}
      />
      <input
        ref={pickerRef}
        type="datetime-local"
        step={1}
        value={value}
        onChange={(event) => onChange(normalizePickerValue(event.target.value))}
        tabIndex={-1}
        aria-hidden="true"
        className="pointer-events-none absolute right-2 top-1/2 h-px w-px -translate-y-1/2 opacity-0"
      />
      <button
        type="button"
        aria-label="Open date time picker"
        onClick={() => {
          const picker = pickerRef.current
          if (!picker) return
          try {
            if (typeof picker.showPicker === 'function') {
              picker.showPicker()
              return
            }
          } catch { /* fall through to focus/click */ }
          picker.focus()
          picker.click()
        }}
        className="absolute right-1 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded border border-[#7aa2ff] bg-[#dbe7ff] text-[#0f172a] shadow-[0_0_0_1px_rgba(122,162,255,0.15)] hover:bg-white"
      >
        <Calendar size={15} strokeWidth={2.2} />
      </button>
    </div>
  )
}

// 显示格式：2026/08/10 00:00:00（24 小时制，本地时间）
function formatFilterDateTimeDisplay(value: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

// 解析用户输入（支持 2026/08/10 00:00:00、2026-08-10 00:00 等），返回内部值 YYYY-MM-DDTHH:mm:ss
function parseFilterDateTimeText(text: string): string | null {
  const match = text.trim().match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T]+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?$/)
  if (!match) return null
  const [, year, month, day, hour = '0', minute = '0', second = '0'] = match
  if (Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59) return null
  const pad = (v: string) => v.padStart(2, '0')
  const canonical = `${year}-${pad(month)}-${pad(day)}T${pad(hour)}:${pad(minute)}:${pad(second)}`
  const date = new Date(canonical)
  if (Number.isNaN(date.getTime())) return null
  // 排除 2026/02/30 这类被 Date 自动进位的非法日期
  if (date.getFullYear() !== Number(year) || date.getMonth() + 1 !== Number(month) || date.getDate() !== Number(day)) return null
  return canonical
}

function normalizePickerValue(value: string): string {
  if (!value) return ''
  // 选择器可能只给到分钟，补足秒
  return value.length === 16 ? `${value}:00` : value
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
  // datetime-local 的值是浏览器本地时间（UTC+8），数据库 created_at 存 UTC 且后端直接按字符串比较，
  // 发送前先转成 UTC，保证筛选范围与界面显示（同样按本地时区渲染）一致
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return undefined
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ` +
    `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`
}

function toLocalDateTimeInputValue(date: Date) {
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
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
