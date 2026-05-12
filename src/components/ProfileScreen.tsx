import { useState, useEffect } from 'react'
import { api } from '../api/client'
import { Locale, useTranslation } from '../i18n/translations'

const UI_LANG = (window as unknown as { electronAPI?: { uiLang?: string } }).electronAPI?.uiLang
const locale: Locale = (UI_LANG === 'en' ? 'en' : 'zh-CN')

interface Stats {
  total_pnl: number; win_rate: number; total_trades: number; total_commission: number
}

interface DailyPnl { date: string; pnl: number; trades: number }

export function ProfileScreen() {
  const { t } = useTranslation(locale)
  const [stats, setStats] = useState<Stats | null>(null)
  const [daily, setDaily] = useState<DailyPnl[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        const [s, d] = await Promise.all([api.getProfileStats(), api.getDailyPnl()])
        setStats(s)
        setDaily(d)
      } catch { /* ignore */ }
      setLoading(false)
    }
    load()
  }, [])

  const maxPnl = Math.max(...daily.map(d => Math.abs(d.pnl)), 1)

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e] overflow-auto">
      <div className="px-4 py-2 border-b border-[#3e3e42] shrink-0">
        <span className="text-sm font-semibold text-[#cccccc]">{t('profile.title')}</span>
      </div>

      {loading ? (
        <div className="flex-1 flex items-center justify-center text-[#858585]">{t('profile.loading')}</div>
      ) : (
        <div className="flex-1 overflow-auto p-4 space-y-4">
          {/* Stats row */}
          {stats && (
            <div className="grid grid-cols-4 gap-3">
              <StatCard label={t('profile.totalPnl')} value={`${stats.total_pnl >= 0 ? '+' : ''}${stats.total_pnl.toFixed(2)} USDT`}
                color={stats.total_pnl >= 0 ? 'text-buy' : 'text-sell'} />
              <StatCard label={t('profile.winRate')} value={`${stats.win_rate.toFixed(1)}%`} />
              <StatCard label={t('profile.trades')} value={String(stats.total_trades)} />
              <StatCard label={t('profile.commission')} value={`${stats.total_commission.toFixed(4)} USDT`} />
            </div>
          )}

          {/* Daily PnL bar chart */}
          <div className="bg-[#252526] rounded border border-[#3e3e42] p-3">
            <div className="text-xs text-[#858585] mb-3">{t('profile.dailyPnl')}</div>
            {daily.length === 0 ? (
              <div className="text-xs text-[#858585] text-center py-4">{t('pos.empty')}</div>
            ) : (
              <div className="flex items-end gap-1 h-32">
                {daily.map((d) => {
                  const h = Math.round((Math.abs(d.pnl) / maxPnl) * 112)
                  const isUp = d.pnl >= 0
                  return (
                    <div key={d.date} className="flex-1 flex flex-col items-center gap-0.5" title={`${d.date}: ${d.pnl.toFixed(2)}`}>
                      <div
                        className={`w-full rounded-sm ${isUp ? 'bg-[#00c853]' : 'bg-[#ff1744]'} opacity-80 hover:opacity-100`}
                        style={{ height: `${Math.max(2, h)}px` }}
                      />
                    </div>
                  )
                })}
              </div>
            )}
            {daily.length > 0 && (
              <div className="flex justify-between text-xs text-[#858585] mt-1">
                <span>{daily[0].date}</span>
                <span>{daily[daily.length - 1].date}</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function StatCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-[#252526] rounded border border-[#3e3e42] px-3 py-2">
      <div className="text-xs text-[#858585] mb-1">{label}</div>
      <div className={`text-sm font-semibold font-mono ${color || 'text-[#cccccc]'}`}>{value}</div>
    </div>
  )
}
