import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent, type ReactNode } from 'react'
import { Download } from 'lucide-react'
import { api, type ApiPositionRecord } from '../api/client'
import { useAuthStore } from '../store/authStore'
import { useToastStore } from '../store/toastStore'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'
import { useTranslation } from '../i18n/translations'
import { formatUtcTimestampToLocalString } from '../utils/datetime'

interface PositionHistoryFilters {
  username: string
  symbol: string
  side: '' | 'LONG' | 'SHORT'
  startTime: string
  endTime: string
}

interface UserOption {
  id: number
  username: string
}

const INITIAL_FILTERS: PositionHistoryFilters = {
  username: '',
  symbol: '',
  side: '',
  startTime: '',
  endTime: '',
}

const PAGE_LIMIT = 200
const EXPORT_LIMIT = 5000
const POSITION_TABLE_COLUMN_COUNT = 22
const INPUT_CLS = 'order-filter-input h-9 w-full rounded border border-[#3e3e42] bg-[#161a21] px-2 py-1.5 text-sm text-[#dde4ef] outline-none focus:border-[#2f7cf6]'

interface GridPoint {
  row: number
  column: number
}

interface GridSelection {
  startRow: number
  endRow: number
  startColumn: number
  endColumn: number
}

interface GridDrag {
  mode: 'cells' | 'rows' | 'columns'
  anchor: GridPoint
}

export function PositionHistoryScreen() {
  const locale = useUiPreferencesStore((state) => state.locale)
  const { t } = useTranslation(locale)
  const user = useAuthStore((state) => state.user)
  const showToast = useToastStore((state) => state.showToast)
  const [rows, setRows] = useState<ApiPositionRecord[]>([])
  const [filters, setFilters] = useState<PositionHistoryFilters>(INITIAL_FILTERS)
  const [userOptions, setUserOptions] = useState<UserOption[]>([])
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState(false)
  const tableRef = useRef<HTMLTableElement>(null)
  const selectionRef = useRef<GridSelection | null>(null)
  const cellAnchorRef = useRef<GridPoint | null>(null)
  const dragRef = useRef<GridDrag | null>(null)

  const load = async (nextFilters: PositionHistoryFilters = filters) => {
    if (!user) {
      setRows([])
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      setRows(await api.getPositionRecords(buildQuery(nextFilters, PAGE_LIMIT)))
    } catch (error: unknown) {
      showToast('error', getRequestErrorMessage(error, t('order.error.failed')))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load(INITIAL_FILTERS)
    if (user?.role !== 'admin') {
      setUserOptions([])
      return
    }
    void api.getOrderUsers()
      .then((items) => setUserOptions(items.map((item) => ({ id: item.id, username: item.username }))))
      .catch(() => setUserOptions([]))
  // Initial load is keyed to the authenticated identity; filters are submitted explicitly.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id, user?.role])

  const handleSearch = (event: FormEvent) => {
    event.preventDefault()
    void load(filters)
  }

  const handleThisWeek = () => {
    const now = new Date()
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
    void load(INITIAL_FILTERS)
  }

  const handleExport = async () => {
    if (exporting) return
    setExporting(true)
    try {
      const exportRows: ApiPositionRecord[] = []
      let offset = 0
      while (true) {
        const page = await api.getPositionRecords({ ...buildQuery(filters, EXPORT_LIMIT), offset })
        exportRows.push(...page)
        if (page.length < EXPORT_LIMIT) break
        offset += page.length
      }
      if (exportRows.length === 0) {
        showToast('info', t('pos.historyExport.empty'))
        return
      }
      const XLSX = await import('xlsx')
      const worksheet = XLSX.utils.json_to_sheet(exportRows.map((row, index) => buildExportRow(row, index, t)))
      const csv = '\uFEFF' + XLSX.utils.sheet_to_csv(worksheet)
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `position_history_${formatFileTimestamp(new Date())}.csv`
      anchor.click()
      URL.revokeObjectURL(url)
      showToast('success', t('pos.historyExport.success', { count: exportRows.length }))
    } catch {
      showToast('error', t('pos.historyExport.failed'))
    } finally {
      setExporting(false)
    }
  }

  const paintSelection = useCallback((selection: GridSelection | null) => {
    const table = tableRef.current
    if (!table) return
    table.querySelectorAll('th,td').forEach((cell) => cell.classList.remove('trade-grid-selected'))
    selectionRef.current = selection
    if (!selection) return

    const firstRow = Math.min(selection.startRow, selection.endRow)
    const lastRow = Math.max(selection.startRow, selection.endRow)
    const firstColumn = Math.min(selection.startColumn, selection.endColumn)
    const lastColumn = Math.max(selection.startColumn, selection.endColumn)
    for (let row = firstRow; row <= lastRow; row += 1) {
      const cells = row === -1 ? table.tHead?.rows[0]?.cells : table.tBodies[0]?.rows[row]?.cells
      if (!cells) continue
      for (let column = firstColumn; column <= lastColumn; column += 1) {
        cells[column]?.classList.add('trade-grid-selected')
      }
    }
  }, [])

  const getGridPoint = (target: EventTarget | null): GridPoint | null => {
    const table = tableRef.current
    const cell = target instanceof Element ? target.closest<HTMLTableCellElement>('td,th') : null
    if (!table || !cell || !table.contains(cell)) return null
    const rowElement = cell.parentElement as HTMLTableRowElement | null
    if (!rowElement) return null
    return {
      row: rowElement.parentElement?.tagName === 'THEAD' ? -1 : rowElement.sectionRowIndex,
      column: cell.cellIndex,
    }
  }

  const updateDraggedSelection = (point: GridPoint) => {
    const drag = dragRef.current
    if (!drag) return
    if (drag.mode === 'rows' && point.row >= 0) {
      paintSelection({
        startRow: drag.anchor.row,
        endRow: point.row,
        startColumn: 0,
        endColumn: POSITION_TABLE_COLUMN_COUNT - 1,
      })
      return
    }
    if (drag.mode === 'columns') {
      paintSelection({
        startRow: -1,
        endRow: Math.max(-1, rows.length - 1),
        startColumn: drag.anchor.column,
        endColumn: point.column,
      })
      return
    }
    if (drag.mode === 'cells' && point.row >= 0) {
      paintSelection({
        startRow: drag.anchor.row,
        endRow: point.row,
        startColumn: drag.anchor.column,
        endColumn: point.column,
      })
    }
  }

  const handleGridMouseDown = (event: ReactMouseEvent<HTMLTableElement>) => {
    if (event.button !== 0) return
    const point = getGridPoint(event.target)
    if (!point) return
    tableRef.current?.focus({ preventScroll: true })

    if (point.row === -1) {
      dragRef.current = { mode: 'columns', anchor: point }
      paintSelection({
        startRow: -1,
        endRow: Math.max(-1, rows.length - 1),
        startColumn: point.column,
        endColumn: point.column,
      })
    } else if (point.column === 0) {
      dragRef.current = { mode: 'rows', anchor: point }
      paintSelection({
        startRow: point.row,
        endRow: point.row,
        startColumn: 0,
        endColumn: POSITION_TABLE_COLUMN_COUNT - 1,
      })
    } else {
      const anchor = event.shiftKey && cellAnchorRef.current ? cellAnchorRef.current : point
      if (!event.shiftKey) cellAnchorRef.current = point
      dragRef.current = { mode: 'cells', anchor }
      paintSelection({
        startRow: anchor.row,
        endRow: point.row,
        startColumn: anchor.column,
        endColumn: point.column,
      })
    }
    event.preventDefault()
  }

  const handleGridMouseOver = (event: ReactMouseEvent<HTMLTableElement>) => {
    if (!dragRef.current) return
    const point = getGridPoint(event.target)
    if (point) updateDraggedSelection(point)
  }

  const handleGridCopy = async (event: ReactKeyboardEvent<HTMLTableElement>) => {
    if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'c') return
    const table = tableRef.current
    const selection = selectionRef.current
    if (!table || !selection) return
    event.preventDefault()

    const firstRow = Math.min(selection.startRow, selection.endRow)
    const lastRow = Math.max(selection.startRow, selection.endRow)
    const firstColumn = Math.min(selection.startColumn, selection.endColumn)
    const lastColumn = Math.max(selection.startColumn, selection.endColumn)
    const lines: string[] = []
    for (let row = firstRow; row <= lastRow; row += 1) {
      const cells = row === -1 ? table.tHead?.rows[0]?.cells : table.tBodies[0]?.rows[row]?.cells
      if (!cells) continue
      const values: string[] = []
      for (let column = firstColumn; column <= lastColumn; column += 1) {
        values.push(cells[column]?.innerText.trim() ?? '')
      }
      lines.push(values.join('\t'))
    }
    if (lines.length === 0) return
    try {
      await navigator.clipboard.writeText(lines.join('\n'))
      showToast('success', t('pos.historyCell.copied'))
    } catch {
      showToast('error', t('common.copyFailed'))
    }
  }

  useEffect(() => {
    const stopDragging = () => { dragRef.current = null }
    window.addEventListener('mouseup', stopDragging)
    return () => window.removeEventListener('mouseup', stopDragging)
  }, [])

  useEffect(() => {
    cellAnchorRef.current = null
    paintSelection(null)
  }, [rows, paintSelection])

  return (
    <div className="relative isolate flex h-full flex-col overflow-hidden bg-[#1e1e1e]">
      <div className="flex shrink-0 items-center gap-3 border-b border-[#3e3e42] px-4 py-2">
        <span className="text-sm font-semibold text-[#cccccc]">{t('pos.historyScreen.title')}</span>
        <span className="text-xs text-[#858585]">
          {user?.role === 'admin' ? t('log.allUsers') : user?.username ?? ''}
        </span>
      </div>

      <form onSubmit={handleSearch} className="flex shrink-0 flex-wrap items-end gap-3 border-b border-[#3e3e42] px-4 py-3">
        {user?.role === 'admin' && (
          <FilterField label={t('log.filter.user')} className="w-[180px]">
            <select
              value={filters.username}
              onChange={(event) => setFilters((current) => ({ ...current, username: event.target.value }))}
              className={INPUT_CLS}
            >
              <option value=""></option>
              {userOptions.map((item) => <option key={item.id} value={item.username}>{item.username}</option>)}
            </select>
          </FilterField>
        )}
        <FilterField label={t('log.symbol')} className="w-[180px]">
          <input
            value={filters.symbol}
            onChange={(event) => setFilters((current) => ({ ...current, symbol: event.target.value.toUpperCase() }))}
            placeholder="BTCUSDC"
            className={INPUT_CLS}
          />
        </FilterField>
        <FilterField label={t('log.side')} className="w-[150px]">
          <select
            value={filters.side}
            onChange={(event) => setFilters((current) => ({
              ...current,
              side: event.target.value as PositionHistoryFilters['side'],
            }))}
            className={INPUT_CLS}
          >
            <option value="">{t('pos.historyFilter.allSides')}</option>
            <option value="LONG">{t('pos.long')}</option>
            <option value="SHORT">{t('pos.short')}</option>
          </select>
        </FilterField>
        <FilterField label={t('log.filter.startTime')} className="w-[220px]">
          <input
            type="datetime-local"
            step={1}
            value={filters.startTime}
            onChange={(event) => setFilters((current) => ({ ...current, startTime: event.target.value }))}
            className={INPUT_CLS}
          />
        </FilterField>
        <FilterField label={t('log.filter.endTime')} className="w-[220px]">
          <input
            type="datetime-local"
            step={1}
            value={filters.endTime}
            onChange={(event) => setFilters((current) => ({ ...current, endTime: event.target.value }))}
            className={INPUT_CLS}
          />
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
            {exporting ? t('pos.historyExport.exporting') : t('log.filter.export')}
          </button>
        </div>
      </form>

      <div className="flex-1 overflow-auto">
        <table
          ref={tableRef}
          tabIndex={0}
          aria-label={t('pos.historyScreen.title')}
          className="trade-table position-selection-grid w-full min-w-[2100px] outline-none [&_th]:cursor-cell [&_td]:cursor-cell [&_tbody_td:first-child]:cursor-pointer"
          onMouseDown={handleGridMouseDown}
          onMouseOver={handleGridMouseOver}
          onKeyDown={(event) => void handleGridCopy(event)}
        >
          <thead><tr>
            <th>{t('log.index')}</th><th>{t('pos.openTime')}</th><th>{t('pos.closeTime')}</th><th>{t('log.symbol')}</th>
            <th>{t('log.side')}</th><th>{t('pos.positionMode')}</th><th>{t('log.qty')}</th>
            <th>{t('pos.entry')}</th><th>{t('pos.closePrice')}</th><th>{t('pos.realizedPnl')}</th>
            <th>{t('trade.commission')}</th><th>{t('pos.historyExport.netPnl')}</th>
            <th>{t('pos.historyExport.plannedStop')}</th><th>{t('pos.historyExport.initialRisk')}</th>
            <th>{t('pos.mfe')}</th><th>{t('pos.mae')}</th><th>{t('pos.historyExport.netPnlR')}</th>
            <th>{t('pos.profitCaptureRate')}</th><th>{t('pos.historyExport.giveback')}</th><th>{t('pos.historyExport.status')}</th>
            <th>{t('pos.openOrdersId')}</th><th>{t('pos.closeOrdersId')}</th>
          </tr></thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={22} className="py-6 text-center text-[#858585]">{loading ? t('log.loading') : t('pos.empty')}</td></tr>
            ) : rows.map((row, index) => (
              <tr key={row.id}>
                <td className="text-[#858585]">{index + 1}</td>
                <td className="whitespace-nowrap text-[#858585]">{formatTimestamp(row.open_time)}</td>
                <td className="whitespace-nowrap text-[#858585]">{formatTimestamp(row.close_time)}</td>
                <td className="font-semibold">{row.symbol}</td>
                <td className={row.side === 'LONG' ? 'text-buy' : 'text-sell'}>{row.side}</td>
                <td className="text-[#858585]">{formatPositionMode(row.position_mode, t)}</td>
                <td className="font-mono">{row.quantity}</td>
                <td className="font-mono">{formatNumber(row.entry_price, 2)}</td>
                <td className="font-mono">{formatNumber(row.close_price, 2)}</td>
                <td className={`font-mono ${pnlTone(row.realized_pnl)}`}>{formatSigned(row.realized_pnl)}</td>
                <td className="font-mono text-[#858585]">{row.commission.toFixed(4)} {row.commission_asset ?? ''}</td>
                <td className={`font-mono ${pnlTone(row.net_pnl)}`}>{formatSigned(row.net_pnl)}</td>
                <td className="font-mono">{formatNumber(row.planned_stop_price, 2)}</td>
                <td className="font-mono">{formatNumber(row.initial_risk_usdc)}</td>
                <td className="font-mono text-buy">{formatExcursion(row.mfe_usdc, row.mfe_r, '+')}</td>
                <td className="font-mono text-sell">{formatExcursion(row.mae_usdc, row.mae_r, '-')}</td>
                <td className={`font-mono ${pnlTone(row.net_pnl_r)}`}>{formatR(row.net_pnl_r)}</td>
                <td className="font-mono">{formatPercent(row.profit_capture_rate)}</td>
                <td className="font-mono">{formatNumber(row.profit_giveback_usdc)}</td>
                <td className="text-[#858585]">{formatExcursionStatus(row.excursion_status, t)}</td>
                <td className="font-mono">{formatOrderIds(row.open_orders_id)}</td>
                <td className="font-mono">{formatOrderIds(row.close_orders_id)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function FilterField({ label, children, className = '' }: { label: string; children: ReactNode; className?: string }) {
  return (
    <label className={`block min-w-0 ${className}`}>
      <div className="mb-1 text-xs text-[#8b94a5]">{label}</div>
      {children}
    </label>
  )
}

function buildQuery(filters: PositionHistoryFilters, limit: number) {
  return {
    limit,
    username: filters.username.trim() || undefined,
    symbol: filters.symbol.trim() || undefined,
    side: filters.side || undefined,
    start_time: toBackendDateTime(filters.startTime),
    end_time: toBackendDateTime(filters.endTime),
  }
}

function buildExportRow(
  row: ApiPositionRecord,
  index: number,
  t: (key: string, vars?: Record<string, string | number>) => string,
) {
  return {
    [t('log.index')]: index + 1,
    [t('pos.openTime')]: formatTimestamp(row.open_time),
    [t('pos.closeTime')]: formatTimestamp(row.close_time),
    [t('log.symbol')]: row.symbol,
    [t('log.side')]: row.side === 'LONG' ? t('pos.long') : t('pos.short'),
    [t('pos.positionMode')]: formatPositionMode(row.position_mode, t),
    [t('log.qty')]: row.quantity,
    [t('pos.entry')]: row.entry_price,
    [t('pos.closePrice')]: row.close_price,
    [t('pos.realizedPnl')]: row.realized_pnl,
    [t('trade.commission')]: row.commission,
    [t('trade.commissionAsset')]: row.commission_asset ?? '',
    [t('pos.historyExport.netPnl')]: row.net_pnl ?? null,
    [t('pos.historyExport.plannedStop')]: row.planned_stop_price ?? null,
    [t('pos.historyExport.initialRisk')]: row.initial_risk_usdc ?? null,
    [t('pos.mfe')]: row.mfe_usdc ?? null,
    [t('pos.mae')]: row.mae_usdc ?? null,
    [t('pos.historyExport.mfeR')]: row.mfe_r ?? null,
    [t('pos.historyExport.maeR')]: row.mae_r ?? null,
    [t('pos.historyExport.netPnlR')]: row.net_pnl_r ?? null,
    [t('pos.profitCaptureRate')]: row.profit_capture_rate != null ? `${(row.profit_capture_rate * 100).toFixed(2)}%` : '',
    [t('pos.historyExport.giveback')]: row.profit_giveback_usdc ?? null,
    [t('pos.historyExport.status')]: row.excursion_status ?? '',
    [t('pos.openOrdersId')]: formatOrderIds(row.open_orders_id),
    [t('pos.closeOrdersId')]: formatOrderIds(row.close_orders_id),
  }
}

function formatTimestamp(value?: string | null) {
  return formatUtcTimestampToLocalString(value)
}

function formatPositionMode(value: string, t: (key: string) => string) {
  if (value === 'SINGLE') return t('pos.positionMode.single')
  if (value === 'DUAL') return t('pos.positionMode.dual')
  return value || '-'
}

function formatNumber(value?: number | null, decimals = 4) {
  return value == null ? '—' : value.toFixed(decimals)
}

function formatOrderIds(values?: string[]) {
  return `[${(values ?? []).join(',')}]`
}

function formatSigned(value?: number | null) {
  if (value == null) return '—'
  return `${value > 0 ? '+' : ''}${value.toFixed(4)}`
}

function formatR(value?: number | null) {
  if (value == null) return 'R —'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)} R`
}

function formatExcursion(value?: number | null, rValue?: number | null, prefix = '') {
  if (value == null) return '—'
  return `${prefix}${value.toFixed(4)} / ${rValue == null ? 'R —' : `${prefix}${rValue.toFixed(2)} R`}`
}

function formatPercent(value?: number | null) {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`
}

function pnlTone(value?: number | null) {
  if (value == null || value === 0) return 'text-[#858585]'
  return value > 0 ? 'text-buy' : 'text-sell'
}

function formatExcursionStatus(value: ApiPositionRecord['excursion_status'], t: (key: string) => string) {
  if (value === 'PENDING') return t('pos.excursionCalculating')
  if (value === 'CALCULATED') return t('pos.excursionCalculated')
  if (value === 'FAILED') return t('pos.excursionFailed')
  return '—'
}

function toBackendDateTime(value: string) {
  if (!value) return undefined
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return undefined
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ` +
    `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`
}

function toLocalDateTimeInputValue(date: Date) {
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function formatFileTimestamp(date: Date) {
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}_` +
    `${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
}

function getRequestErrorMessage(error: unknown, fallback: string) {
  return (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    || (error as { message?: string })?.message
    || fallback
}
