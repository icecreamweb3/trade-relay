import { useState, useEffect } from 'react'
import { api } from '../api/client'
import { Locale, useTranslation } from '../i18n/translations'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'
import { useAuthStore } from '../store/authStore'

const PROFILE_ACCOUNT_ASSET = 'USDC'

interface Stats {
  total_pnl: number; win_rate: number; total_trades: number; total_commission: number; account_balance: number | null
  total_commission_by_asset: Array<{ asset: string; total: number }>
}

interface DailyPnl { date: string; pnl: number; net_pnl: number; account_balance: number | null; commission: number; trades: number; win_rate: number }

type DailyChartTab = 'pnl' | 'equity'

interface DailyLeaderboardEntry {
  rank: number
  username: string
  date: string
  pnl: number
  net_pnl: number
  account_balance: number | null
  trades: number
  win_rate: number
  commission: number
}

interface AllTimeLeaderboardEntry {
  rank: number
  username: string
  pnl: number
  net_pnl: number
  trades: number
  win_rate: number
  commission: number
}

type AllTimeRange = 7 | 30 | null

function truncateDecimal(value: number, digits = 2) {
  const factor = 10 ** digits
  return Math.trunc(value * factor) / factor
}

function formatTruncatedAmount(value: number, digits = 2) {
  return truncateDecimal(value, digits).toFixed(digits)
}

function formatSignedAmount(value: number, digits = 2) {
  const sign = value > 0 ? '+' : ''
  return `${sign}${formatTruncatedAmount(value, digits)}`
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
  if (!items || items.length === 0) return '0.00'
  return items
    .map((item) => `${formatTruncatedAmount(item.total, 2)} ${item.asset}`)
    .join(' · ')
}

function formatAccountBalance(value: number | null | undefined) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—'
  return `${formatTruncatedAmount(value, 2)} ${PROFILE_ACCOUNT_ASSET}`
}

function formatCompactBalance(value: number) {
  if (!Number.isFinite(value)) return '—'
  return `${formatTruncatedAmount(value, 2)} ${PROFILE_ACCOUNT_ASSET}`
}

function getNetProfit(pnl: number, commission: number) {
  return pnl - commission
}

function getTodayUtcDateString() {
  return new Date().toISOString().slice(0, 10)
}

function buildDailyLeaderboardRows(
  leaderboard: DailyLeaderboardEntry[],
  currentUsername: string | undefined,
  currentBalance: number | null | undefined,
) {
  const normalizedUsername = currentUsername?.trim().toLowerCase()
  if (!normalizedUsername) return leaderboard

  const hasCurrentUser = leaderboard.some((entry) => entry.username.trim().toLowerCase() === normalizedUsername)
  if (hasCurrentUser) return leaderboard

  const supplemented = [
    ...leaderboard,
    {
      rank: 0,
      username: currentUsername!.trim(),
      date: leaderboard[0]?.date ?? getTodayUtcDateString(),
      pnl: 0,
      net_pnl: 0,
      account_balance: typeof currentBalance === 'number' && !Number.isNaN(currentBalance) ? currentBalance : null,
      trades: 0,
      win_rate: 0,
      commission: 0,
    },
  ]

  supplemented.sort((left, right) => {
    if (right.pnl !== left.pnl) return right.pnl - left.pnl
    if (right.win_rate !== left.win_rate) return right.win_rate - left.win_rate
    if (right.trades !== left.trades) return right.trades - left.trades
    return left.username.localeCompare(right.username)
  })

  return supplemented.map((entry, index) => ({
    ...entry,
    rank: index + 1,
  }))
}

function buildEquitySeries(daily: DailyPnl[], currentBalance: number | null | undefined) {
  const result: Array<{ date: string; balance: number; netProfit: number }> = []
  let runningBalance = typeof currentBalance === 'number' && !Number.isNaN(currentBalance)
    ? currentBalance
    : null

  for (let index = daily.length - 1; index >= 0; index -= 1) {
    const entry = daily[index]
    const netProfit = entry.net_pnl
    const storedBalance = typeof entry.account_balance === 'number' && !Number.isNaN(entry.account_balance)
      ? entry.account_balance
      : null
    const balance = storedBalance ?? runningBalance

    if (balance == null) continue

    result.unshift({
      date: entry.date,
      balance,
      netProfit,
    })
    runningBalance = balance - netProfit
  }

  return result
}

export function ProfileScreen() {
  const locale = useUiPreferencesStore((state) => state.locale)
  const { t } = useTranslation(locale)
  const currentUser = useAuthStore((state) => state.user)
  const [stats, setStats] = useState<Stats | null>(null)
  const [daily, setDaily] = useState<DailyPnl[]>([])
  const [dailyChartTab, setDailyChartTab] = useState<DailyChartTab>('pnl')
  const [leaderboard, setLeaderboard] = useState<DailyLeaderboardEntry[]>([])
  const [allTimeLeaderboard, setAllTimeLeaderboard] = useState<AllTimeLeaderboardEntry[]>([])
  const [allTimeRange, setAllTimeRange] = useState<AllTimeRange>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try {
        const overview = await api.getProfileOverview(allTimeRange)
        setStats(overview.stats)
        setDaily(Array.isArray(overview.daily_pnl) ? overview.daily_pnl : [])
        setLeaderboard(Array.isArray(overview.daily_leaderboard) ? overview.daily_leaderboard : [])
        setAllTimeLeaderboard(Array.isArray(overview.all_time_leaderboard) ? overview.all_time_leaderboard : [])
      } catch { /* ignore */ }
      setLoading(false)
    }
    load()
  }, [allTimeRange])

  const maxPnl = Math.max(...daily.map(d => Math.abs(d.net_pnl)), 1)
  const hasSingleDay = daily.length === 1
  const dailyBarChartWidth = hasSingleDay ? 480 : Math.max(daily.length * 72, 480)
  const chartAxisMax = formatSignedAmount(maxPnl, 2)
  const equitySeries = buildEquitySeries(daily, stats?.account_balance)
  const displayedLeaderboard = buildDailyLeaderboardRows(leaderboard, currentUser?.username, stats?.account_balance)

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
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-5">
              <StatCard label={t('profile.accountBalance')} value={formatAccountBalance(stats.account_balance)} />
              <StatCard label={t('profile.totalPnl')} value={`${formatSignedAmount(stats.total_pnl, 2)} ${PROFILE_ACCOUNT_ASSET}`}
                color={stats.total_pnl >= 0 ? 'text-buy' : 'text-sell'} />
              <StatCard label={t('profile.winRate')} value={`${stats.win_rate.toFixed(1)}%`} />
              <StatCard label={t('profile.trades')} value={String(stats.total_trades)} />
              <StatCard label={t('profile.commission')} value={formatCommissionByAsset(stats.total_commission_by_asset)} />
            </div>
          )}

          {/* Daily PnL bar chart */}
          <div className="min-w-0 bg-[#252526] rounded border border-[#3e3e42] p-3">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="text-xs text-[#858585]">{t('profile.dailyPnl')}</div>
              <div className="flex items-center gap-2">
                <ChartTabButton
                  label={t('profile.dailyPnlTab')}
                  active={dailyChartTab === 'pnl'}
                  onClick={() => setDailyChartTab('pnl')}
                />
                <ChartTabButton
                  label={t('profile.accountEquityTab')}
                  active={dailyChartTab === 'equity'}
                  onClick={() => setDailyChartTab('equity')}
                />
              </div>
            </div>
            {daily.length === 0 ? (
              <div className="text-xs text-[#858585] text-center py-4">{t('pos.empty')}</div>
            ) : dailyChartTab === 'equity' ? (
              <EquityCurveChart
                series={equitySeries}
                emptyLabel={t('profile.accountEquityUnavailable')}
              />
            ) : (
              <div className="overflow-x-auto rounded border border-[#31343b] bg-[#202225] px-3 py-4">
                <div style={{ minWidth: `${dailyBarChartWidth}px` }}>
                  <div className="mb-3 flex items-center justify-between text-[11px] text-[#6f7682]">
                    <span>{daily[0].date}</span>
                    <span className="font-mono">range 0.00 / {chartAxisMax}</span>
                  </div>
                  <div className="mb-2 flex items-center justify-between text-[10px] text-[#59606c]">
                    <span>{chartAxisMax}</span>
                    <span>0.00</span>
                  </div>
                  <div className={`flex h-40 items-end gap-3 ${hasSingleDay ? 'justify-center' : 'justify-start'}`}>
                    {daily.map((d) => {
                      const barHeight = Math.max(10, Math.round((Math.abs(d.net_pnl) / maxPnl) * 88))
                      const isUp = d.net_pnl >= 0
                      const barStyle = { height: `${barHeight}px` }

                      return (
                        <div
                          key={d.date}
                          className={`flex flex-col items-center gap-2 ${hasSingleDay ? 'w-[120px]' : 'w-[60px] shrink-0'}`}
                          title={`${d.date}: ${formatTruncatedAmount(d.net_pnl, 2)}`}
                        >
                          <div className={`text-[11px] font-mono ${isUp ? 'text-buy' : 'text-sell'}`}>
                            {formatSignedAmount(d.net_pnl, 2)}
                          </div>
                          <div className="flex h-28 w-full items-end justify-center px-3">
                            <div
                              className={`w-full rounded-t-sm ${isUp ? 'bg-[#00c853]' : 'bg-[#ff1744]'} opacity-90 transition-opacity hover:opacity-100`}
                              style={barStyle}
                            />
                          </div>
                          <div className="text-[11px] font-mono text-[#8c93a1]">{formatChartDate(d.date)}</div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="bg-[#252526] rounded border border-[#3e3e42] p-3">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-xs text-[#858585]">{t('profile.dailyLeaderboard')}</div>
              <div className="text-[11px] font-mono text-[#6f7682]">UTC</div>
            </div>
            {displayedLeaderboard.length === 0 ? (
              <div className="text-xs text-[#858585] text-center py-4">{t('pos.empty')}</div>
            ) : (
              <div className="overflow-x-auto rounded border border-[#31343b] bg-[#202225]">
                <div className="min-w-[1030px]">
                  <div className="grid grid-cols-[64px_minmax(160px,1.1fr)_136px_120px_120px_104px_104px_132px] gap-3 border-b border-[#31343b] px-3 py-2 text-[11px] uppercase tracking-wide text-[#6f7682]">
                    <span>{t('profile.rank')}</span>
                    <span>{t('profile.user')}</span>
                    <span className="text-right">{t('profile.accountBalance')}</span>
                    <span className="text-right">{t('profile.totalPnl')}</span>
                    <span className="text-right">{t('profile.netPnl')}</span>
                    <span className="text-right">{t('profile.trades')}</span>
                    <span className="text-right">{t('profile.winRate')}</span>
                    <span className="text-right">{t('profile.commission')}</span>
                  </div>
                  {displayedLeaderboard.map((entry) => (
                    <div
                      key={`${entry.date}-${entry.username}-${entry.rank}`}
                      className="grid grid-cols-[64px_minmax(160px,1.1fr)_136px_120px_120px_104px_104px_132px] gap-3 border-b border-[#2a2d33] px-3 py-2 text-sm text-[#cccccc] last:border-b-0"
                    >
                      <span className="font-mono text-[#8c93a1]">#{entry.rank}</span>
                      <span className="truncate pr-2">{entry.username}</span>
                      <span className="text-right font-mono tabular-nums text-[#c7ccd4]">{formatAccountBalance(entry.account_balance)}</span>
                      <span className={`text-right font-mono tabular-nums ${entry.pnl >= 0 ? 'text-buy' : 'text-sell'}`}>
                        {formatSignedAmount(entry.pnl, 2)}
                      </span>
                      <span className={`text-right font-mono tabular-nums ${entry.net_pnl >= 0 ? 'text-buy' : 'text-sell'}`}>
                        {formatSignedAmount(entry.net_pnl, 2)}
                      </span>
                      <span className="text-right font-mono tabular-nums text-[#c7ccd4]">{entry.trades}</span>
                      <span className="text-right font-mono tabular-nums text-[#c7ccd4]">{entry.win_rate.toFixed(1)}%</span>
                      <span className="text-right font-mono tabular-nums text-[#c7ccd4]">{formatTruncatedAmount(entry.commission, 2)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="bg-[#252526] rounded border border-[#3e3e42] p-3">
            <div className="mb-3 flex items-center justify-between">
              <div className="text-xs text-[#858585]">{t('profile.allTimeLeaderboard')}</div>
              <div className="flex items-center gap-2 text-[11px] font-mono text-[#6f7682]">
                <LeaderboardRangeButton
                  label={t('profile.range7d')}
                  active={allTimeRange === 7}
                  onClick={() => setAllTimeRange(7)}
                />
                <LeaderboardRangeButton
                  label={t('profile.range30d')}
                  active={allTimeRange === 30}
                  onClick={() => setAllTimeRange(30)}
                />
                <LeaderboardRangeButton
                  label={t('profile.rangeAll')}
                  active={allTimeRange === null}
                  onClick={() => setAllTimeRange(null)}
                />
              </div>
            </div>
            {allTimeLeaderboard.length === 0 ? (
              <div className="text-xs text-[#858585] text-center py-4">{t('pos.empty')}</div>
            ) : (
              <div className="overflow-x-auto rounded border border-[#31343b] bg-[#202225]">
                <div className="min-w-[900px]">
                  <div className="grid grid-cols-[64px_minmax(160px,1.2fr)_120px_120px_104px_104px_132px] gap-3 border-b border-[#31343b] px-3 py-2 text-[11px] uppercase tracking-wide text-[#6f7682]">
                    <span>{t('profile.rank')}</span>
                    <span>{t('profile.user')}</span>
                    <span className="text-right">{t('profile.totalPnl')}</span>
                    <span className="text-right">{t('profile.netPnl')}</span>
                    <span className="text-right">{t('profile.trades')}</span>
                    <span className="text-right">{t('profile.winRate')}</span>
                    <span className="text-right">{t('profile.commission')}</span>
                  </div>
                  {allTimeLeaderboard.map((entry) => (
                    <div
                      key={`${entry.username}-${entry.rank}`}
                      className="grid grid-cols-[64px_minmax(160px,1.2fr)_120px_120px_104px_104px_132px] gap-3 border-b border-[#2a2d33] px-3 py-2 text-sm text-[#cccccc] last:border-b-0"
                    >
                      <span className="font-mono text-[#8c93a1]">#{entry.rank}</span>
                      <span className="truncate pr-2">{entry.username}</span>
                      <span className={`text-right font-mono tabular-nums ${entry.pnl >= 0 ? 'text-buy' : 'text-sell'}`}>
                        {formatSignedAmount(entry.pnl, 2)}
                      </span>
                      <span className={`text-right font-mono tabular-nums ${entry.net_pnl >= 0 ? 'text-buy' : 'text-sell'}`}>
                        {formatSignedAmount(entry.net_pnl, 2)}
                      </span>
                      <span className="text-right font-mono tabular-nums text-[#c7ccd4]">{entry.trades}</span>
                      <span className="text-right font-mono tabular-nums text-[#c7ccd4]">{entry.win_rate.toFixed(1)}%</span>
                      <span className="text-right font-mono tabular-nums text-[#c7ccd4]">{formatTruncatedAmount(entry.commission, 2)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function LeaderboardRangeButton({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded border px-2 py-1 text-[11px] transition-colors ${
        active
          ? 'border-[#5a6573] bg-[#30343b] text-[#e6e9ef]'
          : 'border-[#3e434c] bg-transparent text-[#8a92a0] hover:border-[#4b5562] hover:text-[#c7ccd4]'
      }`}
    >
      {label}
    </button>
  )
}

function ChartTabButton({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded border px-2 py-1 text-[11px] transition-colors ${
        active
          ? 'border-[#5a6573] bg-[#30343b] text-[#e6e9ef]'
          : 'border-[#3e434c] bg-transparent text-[#8a92a0] hover:border-[#4b5562] hover:text-[#c7ccd4]'
      }`}
    >
      {label}
    </button>
  )
}

function EquityCurveChart({
  series,
  emptyLabel,
}: {
  series: Array<{ date: string; balance: number; netProfit: number }>
  emptyLabel: string
}) {
  if (series.length === 0) {
    return <div className="text-xs text-[#858585] text-center py-4">{emptyLabel}</div>
  }

  const balances = series.map((entry) => entry.balance)
  const minBalance = Math.min(...balances)
  const maxBalance = Math.max(...balances)
  const span = Math.max(maxBalance - minBalance, 1)
  const width = Math.max((series.length - 1) * 120, 480)
  const height = 220
  const paddingX = 36
  const paddingTop = 24
  const paddingBottom = 34
  const innerWidth = width - paddingX * 2
  const innerHeight = height - paddingTop - paddingBottom
  const stepX = series.length > 1 ? innerWidth / (series.length - 1) : 0
  const points = series.map((entry, index) => {
    const x = paddingX + stepX * index
    const y = paddingTop + ((maxBalance - entry.balance) / span) * innerHeight
    return { x, y, ...entry }
  })
  const polylinePoints = points.map((point) => `${point.x},${point.y}`).join(' ')

  return (
    <div className="rounded border border-[#31343b] bg-[#202225] px-3 py-4">
      <div className="mb-3 flex items-center justify-between text-[11px] text-[#6f7682]">
        <span>{series[0]?.date ?? '—'}</span>
        <span className="font-mono">range {formatCompactBalance(minBalance)} / {formatCompactBalance(maxBalance)}</span>
      </div>
      <div className="mb-2 flex items-center justify-between text-[10px] text-[#59606c]">
        <span>{formatCompactBalance(maxBalance)}</span>
        <span>{formatCompactBalance(minBalance)}</span>
      </div>
      <div className="overflow-x-auto">
        <div style={{ minWidth: `${width}px` }}>
          <svg viewBox={`0 0 ${width} ${height}`} className="h-56 w-full min-w-full">
            <defs>
              <linearGradient id="equity-fill" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor="#0ecb81" stopOpacity="0.28" />
                <stop offset="100%" stopColor="#0ecb81" stopOpacity="0.02" />
              </linearGradient>
            </defs>
            <line x1={paddingX} y1={paddingTop} x2={paddingX} y2={height - paddingBottom} stroke="#2c3138" strokeWidth="1" />
            <line x1={paddingX} y1={height - paddingBottom} x2={width - paddingX} y2={height - paddingBottom} stroke="#2c3138" strokeWidth="1" />
            <path
              d={`M ${points[0]?.x ?? paddingX} ${height - paddingBottom} L ${polylinePoints} L ${points[points.length - 1]?.x ?? paddingX} ${height - paddingBottom} Z`}
              fill="url(#equity-fill)"
            />
            <polyline
              fill="none"
              stroke="#0ecb81"
              strokeWidth="2.5"
              strokeLinejoin="round"
              strokeLinecap="round"
              points={polylinePoints}
            />
            {points.map((point) => {
              const isUp = point.netProfit >= 0
              return (
                <g key={point.date}>
                  <circle cx={point.x} cy={point.y} r="4" fill={isUp ? '#0ecb81' : '#f6465d'} stroke="#171a1f" strokeWidth="2" />
                  <text x={point.x} y={point.y - 10} textAnchor="middle" className="fill-[#c7ccd4] text-[10px] font-mono">
                    {formatTruncatedAmount(point.balance, 2)}
                  </text>
                  <text x={point.x} y={height - 12} textAnchor="middle" className="fill-[#8c93a1] text-[10px] font-mono">
                    {formatChartDate(point.date)}
                  </text>
                </g>
              )
            })}
          </svg>
        </div>
      </div>
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
