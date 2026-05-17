/**
 * RecentTrades — shows recent platform fills (all users, from backend API)
 */
import { useEffect, useState, useCallback, useRef } from 'react'
import { api } from '../api/client'
import { useAuthStore } from '../store/authStore'
import { Locale, useTranslation } from '../i18n/translations'
import { formatUtcTimestampToLocalString, parseUtcTimestamp } from '../utils/datetime'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'

interface Fill {
  username?: string
  symbol: string
  side: string
  order_type?: string | null
  order_category?: string | null
  trade_direction?: string | null
  quantity: number
  price?: number | null
  avg_price: number | null
  realized_pnl?: number | null
  commission?: number | null
  commission_asset?: string | null
  status?: string
  created_at?: string
}

const GRID_TEMPLATE = 'minmax(52px, 0.9fr) minmax(72px, 1fr) 52px 52px 58px 88px 84px 124px 148px 78px 96px'

function getTradeKind(fill: Fill, t: (key: string) => string): { label: string; className: string } {
  const orderCategory = String(fill.order_category || '').toUpperCase()
  const orderType = String(fill.order_type || '').toUpperCase()

  if (orderCategory === 'CONDITIONAL' || orderType.includes('STOP') || orderType.includes('TAKE_PROFIT')) {
    return {
      label: t('type.conditional'),
      className: 'border-[#5c4b19] bg-[#2b2412] text-[#f0b90b]',
    }
  }
  if (orderType === 'LIMIT') {
    return {
      label: t('type.limit'),
      className: 'border-[#244a3a] bg-[#14261f] text-[#0ecb81]',
    }
  }
  if (orderType === 'MARKET') {
    return {
      label: t('type.market'),
      className: 'border-[#3b4454] bg-[#1f2430] text-[#c7d0df]',
    }
  }
  return {
    label: '—',
    className: 'border-[#2f3440] bg-[#1a1d23] text-[#7f8896]',
  }
}

function fmtTime(ts?: string): string {
  if (!ts) return '—'
  const d = parseUtcTimestamp(ts)
  if (!d) return ts
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const min = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${mm}/${dd} ${hh}:${min}:${ss}`
}

function fmtNum(n: number | null, dp = 2): string {
  if (n == null) return '—'
  return n.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })
}

function fmtSignedNum(n: number | null | undefined, dp = 2): string {
  if (n == null) return '—'
  const formatted = n.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })
  return n > 0 ? `+${formatted}` : formatted
}

export function RecentTrades({ isActive = true, refreshTrigger }: { isActive?: boolean; refreshTrigger?: number }) {
  const locale = useUiPreferencesStore((state) => state.locale)
  const { t } = useTranslation(locale)
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated)
  const [fills, setFills] = useState<Fill[]>([])
  const [userFilter, setUserFilter] = useState('')
  const [isWindowVisible, setIsWindowVisible] = useState(() => {
    if (typeof document === 'undefined') return true
    const visible = document.visibilityState === 'visible'
    const focused = typeof document.hasFocus === 'function' ? document.hasFocus() : true
    return visible && focused
  })
  const inFlightRef = useRef(false)

  // Derive unique user list from loaded fills
  const userOptions = Array.from(new Set(fills.map(f => f.username).filter(Boolean) as string[])).sort()

  const load = useCallback(async () => {
    if (inFlightRef.current) {
      return
    }
    if (!isActive) {
      return
    }
    if (!isAuthenticated) {
      setFills([])
      return
    }
    inFlightRef.current = true
    try {
      const data = await api.getRecentFills()
      setFills(data.slice(0, 30))
    } catch {}
    finally {
      inFlightRef.current = false
    }
  }, [isActive, isAuthenticated])

  useEffect(() => {
    const updateVisibility = () => {
      const visible = document.visibilityState === 'visible'
      const focused = typeof document.hasFocus === 'function' ? document.hasFocus() : true
      setIsWindowVisible(visible && focused)
    }

    updateVisibility()
    document.addEventListener('visibilitychange', updateVisibility)
    window.addEventListener('focus', updateVisibility)
    window.addEventListener('blur', updateVisibility)

    return () => {
      document.removeEventListener('visibilitychange', updateVisibility)
      window.removeEventListener('focus', updateVisibility)
      window.removeEventListener('blur', updateVisibility)
    }
  }, [])

  useEffect(() => {
    load()
    if (!isActive || !isAuthenticated || !isWindowVisible) return
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [isActive, isAuthenticated, isWindowVisible, load])

  useEffect(() => {
    if (!isActive || !isAuthenticated || !isWindowVisible) return
    void load()
  }, [isActive, isAuthenticated, isWindowVisible, load])

  // Reload immediately when a new order is placed
  useEffect(() => {
    if (refreshTrigger == null || refreshTrigger === 0) return
    void load()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshTrigger])

  return (
    <div className="h-full flex flex-col bg-[#161616] select-none">
      {/* Header */}
      <div className="px-3 py-1.5 border-b border-[#3e3e42] shrink-0 flex items-center gap-2">
        <span className="text-[11px] font-semibold text-[#cccccc] shrink-0">{t('recentTrades.title')}</span>
        <select
          value={userFilter}
          onChange={e => setUserFilter(e.target.value)}
          className="ml-auto w-28 bg-[#1E2026] border border-[#2B2F36] focus:border-[#F0B90B] text-[10px] text-[#EAECEF] rounded px-2 py-0.5 outline-none cursor-pointer"
        >
          <option value="">{t('recentTrades.filterUser')}</option>
          {userOptions.map(u => (
            <option key={u} value={u}>{u}</option>
          ))}
        </select>
      </div>

      <div className="flex-1 min-h-0 overflow-auto">
        {/* Column labels */}
        <div className="sticky top-0 z-10 w-max min-w-full border-b border-[#2a2a2a] bg-[#161616]">
          <div
            className="grid gap-x-[2px] px-2 py-1 text-[9px] text-[#555] uppercase tracking-wider whitespace-nowrap shrink-0"
            style={{ gridTemplateColumns: GRID_TEMPLATE }}
          >
          <span>{t('log.user')}</span>
          <span className="-ml-[5px] text-left">{t('log.symbol')}</span>
          <span className="-ml-[7px] text-left">{t('log.side')}</span>
          <span className="pl-[5px] text-left">{t('log.qty')}</span>
          <span className="pl-[5px] text-left">{t('log.dir')}</span>
          <span className="-ml-[7px] text-center">{t('log.price')}</span>
          <span className="-ml-[10px] text-center">{t('recentTrades.value')}</span>
          <span className="text-center">{t('pos.realizedPnl')}</span>
          <span className="text-center">{t('trade.commission')}</span>
          <span className="min-w-0 overflow-hidden text-left">{t('recentTrades.kind')}</span>
          <span className="text-center">{t('log.time')}</span>
          </div>
        </div>

        {/* Rows */}
        {(() => {
          const filtered = userFilter.trim()
            ? fills.filter(f => (f.username ?? '').toLowerCase().includes(userFilter.trim().toLowerCase()))
            : fills
          if (filtered.length === 0) return (
            <div className="px-3 py-4 text-[10px] text-[#444]">{t('recentTrades.empty')}</div>
          )
          return filtered.map((f, i) => {
            const isBuy = f.side === 'BUY'
            const value = f.avg_price != null ? f.quantity * f.avg_price : null
            const commissionText = f.commission != null
              ? `${fmtNum(f.commission, 4)}${f.commission_asset ? ` ${f.commission_asset}` : ''}`
              : '—'
            const realizedPnlClass = (f.realized_pnl ?? 0) > 0
              ? 'text-[#0ecb81]'
              : (f.realized_pnl ?? 0) < 0
                ? 'text-[#f6465d]'
                : 'text-[#aaa]'
            const tradeKind = getTradeKind(f, t)
            return (
              <div
                key={i}
                className="grid w-max min-w-full gap-x-[2px] px-2 py-[2px] text-[10px] hover:bg-[#1e1e1e] font-mono tabular-nums"
                style={{ gridTemplateColumns: GRID_TEMPLATE }}
              >
                <span className="text-[#aaa] truncate pr-1">{f.username ?? '—'}</span>
                <span className="pl-[5px] text-[#888] truncate pr-1">{f.symbol}</span>
                <span className={`${isBuy ? 'text-[#0ecb81]' : 'text-[#f6465d]'} truncate pl-[10px] text-left`}>{f.side === 'BUY' ? t('side.buy') : t('side.sell')}</span>
                <span className="pl-[15px] text-left text-[#aaa]">{fmtNum(f.quantity, 3)}</span>
                <span className={`pl-[15px] text-left ${f.trade_direction === 'CLOSE' ? 'text-[#f6465d]' : 'text-[#0ecb81]'}`}>
                  {f.trade_direction === 'CLOSE' ? t('order.close') : f.trade_direction === 'OPEN' ? t('order.open') : '—'}
                </span>
                <span className="text-right text-[#aaa]">{(f.price != null && f.price > 0) ? fmtNum(f.price, 2) : (f.avg_price != null ? fmtNum(f.avg_price, 2) : '—')}</span>
                <span className="-ml-[10px] text-right text-[#aaa]">{value != null ? fmtNum(value, 2) : '—'}</span>
                <span className={`text-center ${realizedPnlClass}`}>{fmtSignedNum(f.realized_pnl, 2)}</span>
                <span className="text-center text-[#aaa]">{commissionText}</span>
                <span className="min-w-0 overflow-hidden pr-1">
                  <span className={`inline-flex w-full max-w-full min-w-0 items-center justify-center overflow-hidden text-ellipsis whitespace-nowrap rounded border px-1.5 py-[1px] text-[9px] font-semibold uppercase tracking-wide ${tradeKind.className}`}>
                    {tradeKind.label}
                  </span>
                </span>
                <span className="text-right text-[#aaa]" title={formatUtcTimestampToLocalString(f.created_at)}>{fmtTime(f.created_at)}</span>
              </div>
            )
          })
        })()}
      </div>
    </div>
  )
}
