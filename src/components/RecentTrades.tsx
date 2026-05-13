/**
 * RecentTrades — shows recent platform fills (all users, from backend API)
 */
import { useEffect, useState, useCallback } from 'react'
import { api } from '../api/client'
import { useAuthStore } from '../store/authStore'
import { Locale, useTranslation } from '../i18n/translations'

const UI_LANG = (window as unknown as { electronAPI?: { uiLang?: string } }).electronAPI?.uiLang
const locale: Locale = (UI_LANG === 'en' ? 'en' : 'zh-CN')

interface Fill {
  username?: string
  symbol: string
  side: string
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
    return d.toLocaleTimeString('en-US', { hour12: false })
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

  const load = useCallback(async () => {
    if (!isActive) {
      return
    }
    if (!isAuthenticated) {
      setFills([])
      return
    }
    try {
      const data = await api.getRecentFills()
      setFills(data.slice(0, 30))
    } catch {}
  }, [isActive, isAuthenticated])

  useEffect(() => {
    load()
    if (!isActive || !isAuthenticated) return
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [isActive, isAuthenticated, load])

  // Reload immediately when a new order is placed
  useEffect(() => {
    if (refreshTrigger == null || refreshTrigger === 0) return
    void load()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshTrigger])

  return (
    <div className="h-full flex flex-col bg-[#161616] select-none">
      {/* Header */}
      <div className="px-3 py-1.5 border-b border-[#3e3e42] shrink-0">
        <span className="text-[11px] font-semibold text-[#cccccc]">{t('recentTrades.title')}</span>
      </div>

      {/* Column labels */}
      <div className="grid px-2 py-1 text-[9px] text-[#555] uppercase tracking-wider shrink-0 border-b border-[#2a2a2a]"
        style={{ gridTemplateColumns: '1fr 1fr 52px 1fr 1fr 1fr 60px 1fr' }}>
        <span>{t('log.user')}</span>
        <span>{t('log.symbol')}</span>
        <span>{t('log.side')}</span>
        <span className="text-right">{t('log.qty')}</span>
        <span className="text-right">{t('log.price')}</span>
        <span className="text-right">{t('recentTrades.value')}</span>
        <span className="text-center">{t('log.status')}</span>
        <span className="text-right">{t('log.time')}</span>
      </div>

      {/* Rows */}
      <div className="flex-1 overflow-y-auto">
        {fills.length === 0 ? (
          <div className="px-3 py-4 text-[10px] text-[#444]">{t('recentTrades.empty')}</div>
        ) : (
          fills.map((f, i) => {
            const isBuy = f.side === 'BUY'
            const value = f.avg_price != null ? f.quantity * f.avg_price : null
            return (
              <div
                key={i}
                className="grid px-2 py-[2px] text-[10px] hover:bg-[#1e1e1e] font-mono tabular-nums"
                style={{ gridTemplateColumns: '1fr 1fr 52px 1fr 1fr 1fr 60px 1fr' }}
              >
                <span className="text-[#aaa] truncate pr-1">{f.username ?? '—'}</span>
                <span className="text-[#888] truncate pr-1">{f.symbol}</span>
                <span className={isBuy ? 'text-[#0ecb81]' : 'text-[#f6465d]'}>{f.side === 'BUY' ? t('side.buy') : t('side.sell')}</span>
                <span className="text-right text-[#aaa]">{fmtNum(f.quantity, 3)}</span>
                <span className="text-right text-[#aaa]">{(f.price != null && f.price > 0) ? fmtNum(f.price, 2) : (f.avg_price != null ? fmtNum(f.avg_price, 2) : '—')}</span>
                <span className="text-right text-[#aaa]">{value != null ? fmtNum(value, 2) : '—'}</span>
                <span className={`text-center truncate ${
                  f.status === 'FILLED' ? 'text-[#0ecb81]' :
                  f.status === 'NEW' || f.status === 'PARTIALLY_FILLED' ? 'text-[#f0b90b]' :
                  'text-[#555]'
                }`}>{f.status ?? '—'}</span>
                <span className="text-right text-[#555]">{fmtTime(f.created_at)}</span>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
