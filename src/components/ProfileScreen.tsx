import { useState, useEffect } from 'react'
import { api } from '../api/client'
import { Locale, useTranslation } from '../i18n/translations'

const UI_LANG = (window as unknown as { electronAPI?: { uiLang?: string } }).electronAPI?.uiLang
const locale: Locale = (UI_LANG === 'en' ? 'en' : 'zh-CN')

interface Stats {
  total_pnl: number; win_rate: number; total_trades: number; total_commission: number
  total_commission_by_asset: Array<{ asset: string; total: number }>
}

interface DailyPnl { date: string; pnl: number; trades: number }

function formatSignedAmount(value: number, digits = 2) {
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(digits)}`
}

function formatChartDate(value: string) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${month}/${day}`
}

function formatCommissionByAsset(items: Array<{ asset: string; total: number }>) {
  if (!items || items.length === 0) return '0.0000'
  return items
    .map((item) => `${item.total.toFixed(4)} ${item.asset}`)
    .join(' · ')
}

export function ProfileScreen() {
  const { t } = useTranslation(locale)
  const [stats, setStats] = useState<Stats | null>(null)
  const [daily, setDaily] = useState<DailyPnl[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        const overview = await api.getProfileOverview()
        setStats(overview.stats)
        setDaily(overview.daily_pnl)
      } catch { /* ignore */ }
      setLoading(false)
    }
    load()
  }, [])

  const maxPnl = Math.max(...daily.map(d => Math.abs(d.pnl)), 1)
  const hasSingleDay = daily.length === 1
  const chartAxisMax = formatSignedAmount(maxPnl, 2)
  const chartAxisMin = formatSignedAmount(-maxPnl, 2)

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
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
              <StatCard label={t('profile.totalPnl')} value={`${formatSignedAmount(stats.total_pnl, 2)} USDT`}
                color={stats.total_pnl >= 0 ? 'text-buy' : 'text-sell'} />
              <StatCard label={t('profile.winRate')} value={`${stats.win_rate.toFixed(1)}%`} />
              <StatCard label={t('profile.trades')} value={String(stats.total_trades)} />
              <StatCard label={t('profile.commission')} value={formatCommissionByAsset(stats.total_commission_by_asset)} />
            </div>
          )}

          {/* Daily PnL bar chart */}
          <div className="bg-[#252526] rounded border border-[#3e3e42] p-3">
            <div className="text-xs text-[#858585] mb-3">{t('profile.dailyPnl')}</div>
            {daily.length === 0 ? (
              <div className="text-xs text-[#858585] text-center py-4">{t('pos.empty')}</div>
            ) : (
              <div className="rounded border border-[#31343b] bg-[#202225] px-3 py-4">
                <div className="mb-3 flex items-center justify-between text-[11px] text-[#6f7682]">
                  <span>{daily[0].date}</span>
                  <span className="font-mono">range {chartAxisMin} / {chartAxisMax}</span>
                </div>
                <div className="mb-2 flex items-center justify-between text-[10px] text-[#59606c]">
                  <span>{chartAxisMax}</span>
                  <span>0.00</span>
                  <span>{chartAxisMin}</span>
                </div>
                <div className={`flex h-40 items-end gap-3 ${hasSingleDay ? 'justify-center' : 'justify-between'}`}>
                  {daily.map((d) => {
                    const barHeight = Math.max(6, Math.round((Math.abs(d.pnl) / maxPnl) * 48))
                    const isUp = d.pnl >= 0
                    const barStyle = isUp
                      ? { bottom: '50%', height: `${barHeight}px` }
                      : { top: '50%', height: `${barHeight}px` }

                    return (
                      <div
                        key={d.date}
                        className={`flex flex-col items-center gap-2 ${hasSingleDay ? 'w-[120px]' : 'min-w-[56px] max-w-[92px] flex-1'}`}
                        title={`${d.date}: ${d.pnl.toFixed(2)}`}
                      >
                        <div className={`text-[11px] font-mono ${isUp ? 'text-buy' : 'text-sell'}`}>
                          {formatSignedAmount(d.pnl, 2)}
                        </div>
                        <div className="relative h-28 w-full rounded-md border border-[#31343b] bg-[#17191d] px-2">
                          <div className="absolute inset-x-0 top-1/2 border-t border-dashed border-[#48505c]" />
                          <div
                            className={`absolute left-2 right-2 rounded-sm ${isUp ? 'bg-[#00c853]' : 'bg-[#ff1744]'} opacity-85 transition-opacity hover:opacity-100`}
                            style={barStyle}
                          />
                        </div>
                        <div className="text-[11px] font-mono text-[#8c93a1]">{formatChartDate(d.date)}</div>
                      </div>
                    )
                  })}
                </div>
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
    <div className="bg-[#252526] rounded border border-[#3e3e42] px-4 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]">
      <div className="mb-1 text-xs uppercase tracking-wide text-[#7f8896]">{label}</div>
      <div className={`text-lg font-semibold font-mono tabular-nums ${color || 'text-[#cccccc]'}`}>{value}</div>
    </div>
  )
}
