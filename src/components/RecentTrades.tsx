/**
 * RecentTrades — shows recent platform fills (all users, from backend API)
 */
import { useEffect, useState, useCallback, useRef } from 'react'
import { api } from '../api/client'
import { useAuthStore } from '../store/authStore'
import { Locale, useTranslation } from '../i18n/translations'

const UI_LANG = (window as unknown as { electronAPI?: { uiLang?: string } }).electronAPI?.uiLang
const locale: Locale = (UI_LANG === 'en' ? 'en' : 'zh-CN')

interface Fill {
  username?: string
  symbol: string
  side: string
  trade_direction?: string | null
  quantity: number
  price?: number | null
  avg_price: number | null
  status?: string
  created_at?: string
}

function fmtTime(ts?: string): string {
  if (!ts) return '—'
  try {
    const d = new Date(ts)
    const yyyy = d.getFullYear()
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    const hh = String(d.getHours()).padStart(2, '0')
    const min = String(d.getMinutes()).padStart(2, '0')
    const ss = String(d.getSeconds()).padStart(2, '0')
    return `${yyyy}/${mm}/${dd} ${hh}:${min}:${ss}`
  } catch {
    return ts
  }
}

function fmtNum(n: number | null, dp = 2): string {
  if (n == null) return '—'
  return n.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })
}

export function RecentTrades({ isActive = true, refreshTrigger }: { isActive?: boolean; refreshTrigger?: number }) {
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
    const t = setInterval(load, 5000)
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

      {/* Column labels */}
      <div className="grid px-2 py-1 text-[9px] text-[#555] uppercase tracking-wider shrink-0 border-b border-[#2a2a2a]"
        style={{ gridTemplateColumns: '1fr 1fr 52px 36px 0.7fr 1fr 1fr 60px 130px' }}>
        <span>{t('log.user')}</span>
        <span>{t('log.symbol')}</span>
        <span>{t('log.side')}</span>
        <span>{t('log.dir')}</span>
        <span className="text-center">{t('log.qty')}</span>
        <span className="text-center">{t('log.price')}</span>
        <span className="text-center">{t('recentTrades.value')}</span>
        <span className="text-center">{t('log.status')}</span>
        <span className="text-center">{t('log.time')}</span>
      </div>

      {/* Rows */}
      <div className="flex-1 overflow-y-auto">
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
            return (
              <div
                key={i}
                className="grid px-2 py-[2px] text-[10px] hover:bg-[#1e1e1e] font-mono tabular-nums"
                style={{ gridTemplateColumns: '1fr 1fr 52px 36px 0.7fr 1fr 1fr 60px 130px' }}
              >
                <span className="text-[#aaa] truncate pr-1">{f.username ?? '—'}</span>
                <span className="text-[#888] truncate pr-1">{f.symbol}</span>
                <span className={isBuy ? 'text-[#0ecb81]' : 'text-[#f6465d]'}>{f.side === 'BUY' ? t('side.buy') : t('side.sell')}</span>
                <span className={f.trade_direction === 'CLOSE' ? 'text-[#f6465d]' : 'text-[#0ecb81]'}>
                  {f.trade_direction === 'CLOSE' ? t('order.close') : f.trade_direction === 'OPEN' ? t('order.open') : '—'}
                </span>
                <span className="text-center text-[#aaa]">{fmtNum(f.quantity, 3)}</span>
                <span className="text-center text-[#aaa]">{(f.price != null && f.price > 0) ? fmtNum(f.price, 2) : (f.avg_price != null ? fmtNum(f.avg_price, 2) : '—')}</span>
                <span className="text-center text-[#aaa]">{value != null ? fmtNum(value, 2) : '—'}</span>
                <span className={`text-center truncate ${
                  f.status === 'FILLED' ? 'text-[#0ecb81]' :
                  f.status === 'NEW' || f.status === 'PARTIALLY_FILLED' ? 'text-[#f0b90b]' :
                  'text-[#555]'
                }`}>{f.status ?? '—'}</span>
                <span className="text-center text-[#aaa]">{fmtTime(f.created_at)}</span>
              </div>
            )
          })
        })()}
      </div>
    </div>
  )
}
