import { useEffect, useMemo, useState } from 'react'
import { X } from 'lucide-react'
import { api, type ApiKline } from '../api/client'
import { useTranslation } from '../i18n/translations'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'
import { chooseKlineInterval, type PositionFillMarker, type PositionWindow } from '../utils/orderChart'

const INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'] as const
const INTERVAL_MS: Record<string, number> = {
  '1m': 60_000,
  '5m': 300_000,
  '15m': 900_000,
  '1h': 3_600_000,
  '4h': 14_400_000,
  '1d': 86_400_000,
}

export function OrderKlineModal({ position, onClose }: { position: PositionWindow; onClose: () => void }) {
  const locale = useUiPreferencesStore((state) => state.locale)
  const { t } = useTranslation(locale)
  const [interval, setInterval] = useState(() => chooseKlineInterval(position.startTime, position.endTime))
  const [klines, setKlines] = useState<ApiKline[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  const bounds = useMemo(() => {
    const duration = Math.max(INTERVAL_MS[interval] * 20, position.endTime - position.startTime)
    const padding = Math.max(INTERVAL_MS[interval] * 12, duration * 0.12)
    return {
      start: Math.max(0, Math.floor(position.startTime - padding)),
      end: Math.ceil(position.endTime + padding),
    }
  }, [interval, position.endTime, position.startTime])

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(false)
    api.getHistoricalKlines({
      symbol: position.symbol,
      interval,
      start_time: bounds.start,
      end_time: bounds.end,
      username: position.username || undefined,
    }).then((data) => {
      if (!active) return
      setKlines(data)
      setError(data.length === 0)
    }).catch(() => {
      if (active) setError(true)
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [bounds.end, bounds.start, interval, position.symbol])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const duration = formatDuration(position.endTime - position.startTime, locale)
  const sideLabel = position.positionSide === 'LONG' ? t('log.chart.long')
    : position.positionSide === 'SHORT' ? t('log.chart.short') : t('log.chart.unknownSide')

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section role="dialog" aria-modal="true" aria-label={t('log.chart.title')} className="flex h-[min(820px,94vh)] w-[min(1500px,96vw)] flex-col overflow-hidden rounded-lg border border-[#3b414b] bg-[#101318] shadow-2xl">
        <header className="flex items-center justify-between border-b border-[#2c323b] bg-[#171b21] px-4 py-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold text-[#e6e9ef]">{position.symbol} · {t('log.chart.title')}</h2>
              <span className={`rounded px-2 py-0.5 text-xs font-semibold ${position.positionSide === 'SHORT' ? 'bg-[#f6465d]/15 text-[#f6465d]' : 'bg-[#0ecb81]/15 text-[#0ecb81]'}`}>{sideLabel}</span>
              {position.isOpen && <span className="rounded bg-[#f0b90b]/15 px-2 py-0.5 text-xs text-[#f0b90b]">{t('log.chart.openPosition')}</span>}
            </div>
            <div className="mt-1 truncate text-xs text-[#8b94a5]">
              {position.username} · {formatDateTime(position.startTime)} — {position.isOpen ? t('log.chart.now') : formatDateTime(position.endTime)} · {duration}
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label={t('common.close')} className="rounded p-1.5 text-[#9aa3b2] hover:bg-[#2b313b] hover:text-white"><X size={19} /></button>
        </header>

        <div className="flex items-center justify-between border-b border-[#252b33] px-4 py-2">
          <div className="flex gap-1">
            {INTERVALS.map((item) => {
              const expectedBars = (bounds.end - bounds.start) / INTERVAL_MS[item]
              const disabled = expectedBars > 5000
              return (
                <button key={item} type="button" disabled={disabled} onClick={() => setInterval(item)} title={disabled ? t('log.chart.rangeTooLarge') : undefined}
                  className={`rounded px-3 py-1 text-xs transition-colors ${interval === item ? 'bg-[#2f7cf6] text-white' : 'text-[#9aa3b2] hover:bg-[#252b33] hover:text-[#dce2ea]'} disabled:cursor-not-allowed disabled:opacity-30`}>
                  {item}
                </button>
              )
            })}
          </div>
          <div className="flex items-center gap-4 text-xs">
            <span className="text-[#0ecb81]">↑ {t('log.chart.entry')}</span>
            <span className="text-[#f6465d]">↓ {t('log.chart.exit')}</span>
            <span className="text-[#d95b8b]">EMA 20</span>
          </div>
        </div>

        <div className="relative min-h-0 flex-1 p-3">
          {loading && <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#101318]/75 text-sm text-[#9aa3b2]">{t('log.chart.loading')}</div>}
          {!loading && error && <div className="absolute inset-0 flex items-center justify-center text-sm text-[#f6465d]">{t('log.chart.failed')}</div>}
          {klines.length > 0 && <CandlestickChart klines={klines} markers={position.markers} startTime={position.startTime} endTime={position.endTime} locale={locale} />}
        </div>
      </section>
    </div>
  )
}

function CandlestickChart({
  klines,
  markers,
  startTime,
  endTime,
  locale,
}: {
  klines: ApiKline[]
  markers: PositionFillMarker[]
  startTime: number
  endTime: number
  locale: string
}) {
  const width = 1400
  const height = 690
  const margin = { top: 48, right: 92, bottom: 54, left: 18 }
  const volumeHeight = 80
  const priceBottom = height - margin.bottom - volumeHeight - 28
  const plotWidth = width - margin.left - margin.right
  const priceHeight = priceBottom - margin.top
  const allPrices = klines.flatMap((bar) => [bar.low, bar.high]).concat(markers.map((marker) => marker.price))
  const rawMin = Math.min(...allPrices)
  const rawMax = Math.max(...allPrices)
  const pricePadding = Math.max((rawMax - rawMin) * 0.09, rawMax * 0.0005)
  const minPrice = rawMin - pricePadding
  const maxPrice = rawMax + pricePadding
  const minTime = klines[0].open_time
  const maxTime = Math.max(klines[klines.length - 1].close_time, minTime + 1)
  const maxVolume = Math.max(...klines.map((bar) => bar.volume), 1)
  const candleWidth = Math.max(1, Math.min(13, (plotWidth / klines.length) * 0.7))
  const x = (time: number) => margin.left + ((time - minTime) / (maxTime - minTime)) * plotWidth
  const y = (price: number) => margin.top + ((maxPrice - price) / (maxPrice - minPrice)) * priceHeight
  const volumeY = (volume: number) => height - margin.bottom - (volume / maxVolume) * volumeHeight
  const ema = computeEma(klines.map((bar) => bar.close), 20)
  const emaPoints = ema.map((value, index) => value == null ? null : `${x(klines[index].open_time)},${y(value)}`).filter(Boolean).join(' ')
  const timeTicks = Array.from({ length: 7 }, (_, index) => minTime + ((maxTime - minTime) * index) / 6)
  const priceTicks = Array.from({ length: 7 }, (_, index) => minPrice + ((maxPrice - minPrice) * index) / 6)

  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="h-full w-full select-none rounded bg-[#0d1014]" role="img">
      <rect x={x(startTime)} y={margin.top} width={Math.max(1, x(endTime) - x(startTime))} height={priceHeight} fill="#2f7cf6" opacity="0.045" />
      {priceTicks.map((price) => <g key={price}>
        <line x1={margin.left} x2={width - margin.right} y1={y(price)} y2={y(price)} stroke="#252b33" strokeWidth="1" />
        <text x={width - margin.right + 10} y={y(price) + 4} fill="#758091" fontSize="12">{formatPrice(price)}</text>
      </g>)}
      {timeTicks.map((time) => <g key={time}>
        <line x1={x(time)} x2={x(time)} y1={margin.top} y2={height - margin.bottom} stroke="#20262e" strokeWidth="1" />
        <text x={x(time)} y={height - 18} textAnchor="middle" fill="#758091" fontSize="12">{formatAxisTime(time, locale)}</text>
      </g>)}

      {klines.map((bar) => {
        const rising = bar.close >= bar.open
        const color = rising ? '#0ecb81' : '#f6465d'
        const centerX = x(bar.open_time + (bar.close_time - bar.open_time) / 2)
        const bodyTop = y(Math.max(bar.open, bar.close))
        const bodyHeight = Math.max(1.5, Math.abs(y(bar.open) - y(bar.close)))
        return <g key={bar.open_time}>
          <line x1={centerX} x2={centerX} y1={y(bar.high)} y2={y(bar.low)} stroke={color} strokeWidth="1" />
          <rect x={centerX - candleWidth / 2} y={bodyTop} width={candleWidth} height={bodyHeight} fill={color} />
          <rect x={centerX - candleWidth / 2} y={volumeY(bar.volume)} width={candleWidth} height={height - margin.bottom - volumeY(bar.volume)} fill={color} opacity="0.3" />
          <title>{`${formatDateTime(bar.open_time)}  O ${bar.open}  H ${bar.high}  L ${bar.low}  C ${bar.close}`}</title>
        </g>
      })}
      {emaPoints && <polyline points={emaPoints} fill="none" stroke="#d95b8b" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />}
      <line x1={margin.left} x2={width - margin.right} y1={height - margin.bottom - volumeHeight} y2={height - margin.bottom - volumeHeight} stroke="#303741" />
      <line x1={x(startTime)} x2={x(startTime)} y1={margin.top} y2={height - margin.bottom} stroke="#4d8dff" strokeDasharray="5 5" opacity="0.65" />
      <line x1={x(endTime)} x2={x(endTime)} y1={margin.top} y2={height - margin.bottom} stroke="#4d8dff" strokeDasharray="5 5" opacity="0.65" />

      {markers.map((marker, index) => {
        const markerX = Math.max(margin.left + 28, Math.min(width - margin.right - 28, x(marker.timestamp)))
        const priceY = y(marker.price)
        const entry = marker.action === 'ENTRY'
        const color = entry ? '#1687ff' : '#f6465d'
        const lane = index % 3
        const tipY = entry ? priceY + 7 : priceY - 7
        const labelY = entry ? priceY + 43 + lane * 24 : priceY - 37 - lane * 24
        const lineEndY = entry ? labelY - 18 : labelY + 12
        return <g key={`${marker.id}-${marker.action}`}>
          <line x1={markerX} x2={markerX} y1={tipY} y2={lineEndY} stroke={color} strokeWidth="2" />
          <path d={entry
            ? `M ${markerX - 6} ${priceY + 14} L ${markerX} ${priceY + 5} L ${markerX + 6} ${priceY + 14}`
            : `M ${markerX - 6} ${priceY - 14} L ${markerX} ${priceY - 5} L ${markerX + 6} ${priceY - 14}`}
            fill="none" stroke={color} strokeWidth="2.5" />
          <text x={markerX} y={labelY} textAnchor="middle" fill="#e4e8ee" fontSize="12" fontWeight="600">
            <tspan x={markerX}>{formatCompactNumber(marker.quantity)}</tspan>
            <tspan x={markerX} dy="14" fill="#aab2bf">@ {formatPrice(marker.price)}</tspan>
          </text>
          <title>{`${entry ? 'Entry' : 'Exit'} ${marker.quantity} @ ${marker.price} · ${formatDateTime(marker.timestamp)}`}</title>
        </g>
      })}
    </svg>
  )
}

function computeEma(values: number[], period: number): Array<number | null> {
  const multiplier = 2 / (period + 1)
  let current: number | null = null
  return values.map((value, index) => {
    current = current == null ? value : value * multiplier + current * (1 - multiplier)
    return index < period - 1 ? null : current
  })
}

function formatPrice(value: number): string {
  if (Math.abs(value) >= 1000) return value.toLocaleString('en-US', { maximumFractionDigits: 2 })
  if (Math.abs(value) >= 1) return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')
  return value.toPrecision(5)
}

function formatCompactNumber(value: number): string {
  return value.toLocaleString('en-US', { maximumFractionDigits: 6 })
}

function formatDateTime(timestamp: number): string {
  const date = new Date(timestamp)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function formatAxisTime(timestamp: number, locale: string): string {
  const date = new Date(timestamp)
  const sameDay = new Date(timestamp).toDateString() === new Date(timestamp - 6 * 60 * 60 * 1000).toDateString()
  return new Intl.DateTimeFormat(locale === 'zh-CN' ? 'zh-CN' : 'en-US', sameDay
    ? { hour: '2-digit', minute: '2-digit', hour12: false }
    : { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}

function formatDuration(durationMs: number, locale: string): string {
  const totalMinutes = Math.max(0, Math.round(durationMs / 60_000))
  const days = Math.floor(totalMinutes / 1440)
  const hours = Math.floor((totalMinutes % 1440) / 60)
  const minutes = totalMinutes % 60
  if (locale === 'zh-CN') return `${days ? `${days}天 ` : ''}${hours ? `${hours}小时 ` : ''}${minutes}分钟`
  return `${days ? `${days}d ` : ''}${hours ? `${hours}h ` : ''}${minutes}m`
}
