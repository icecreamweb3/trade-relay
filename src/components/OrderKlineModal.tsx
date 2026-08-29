import { useEffect, useMemo, useRef, useState } from 'react'
import { RotateCcw, X, ZoomIn, ZoomOut } from 'lucide-react'
import { api, type ApiKline } from '../api/client'
import { useTranslation } from '../i18n/translations'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'
import type { PositionFillMarker, PositionWindow } from '../utils/orderChart'

const INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'] as const
const INTERVAL_MS: Record<string, number> = {
  '1m': 60_000,
  '5m': 300_000,
  '15m': 900_000,
  '1h': 3_600_000,
  '4h': 14_400_000,
  '1d': 86_400_000,
}

const KLINE_CACHE_TTL = 5 * 60_000
const klineCache = new Map<string, { expiresAt: number; data: ApiKline[] }>()

export function OrderKlineLoadingModal({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const locale = useUiPreferencesStore((state) => state.locale)
  const { t } = useTranslation(locale)
  const progress = useSimulatedProgress(12, 88)

  return (
    <div className="absolute inset-0 z-30 flex items-center justify-center bg-black/70 p-4">
      <section role="dialog" aria-modal="true" className="flex h-full max-h-[820px] w-full max-w-[1500px] flex-col overflow-hidden rounded-lg border border-[#3b414b] bg-[#101318] shadow-2xl">
        <header className="flex items-center justify-between border-b border-[#2c323b] bg-[#171b21] px-4 py-3">
          <h2 className="text-base font-semibold text-[#e6e9ef]">{symbol} · {t('log.chart.title')}</h2>
          <button type="button" onClick={onClose} aria-label={t('common.close')} className="rounded p-1.5 text-[#9aa3b2] hover:bg-[#2b313b] hover:text-white"><X size={19} /></button>
        </header>
        <LoadingProgress progress={progress} label={t('log.chart.matchingPosition')} />
      </section>
    </div>
  )
}

export function OrderKlineModal({ position, onClose }: { position: PositionWindow; onClose: () => void }) {
  const locale = useUiPreferencesStore((state) => state.locale)
  const { t } = useTranslation(locale)
  const [interval, setInterval] = useState('5m')
  const [klines, setKlines] = useState<ApiKline[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [loadingProgress, setLoadingProgress] = useState(8)

  const bounds = useMemo(() => {
    return buildThousandBarWindow(position, INTERVAL_MS[interval])
  }, [interval, position])

  useEffect(() => {
    let active = true
    const cacheKey = `${position.username}|${position.symbol}|${interval}|${bounds.start}|${bounds.end}`
    const cached = klineCache.get(cacheKey)
    if (cached && cached.expiresAt > Date.now()) {
      setKlines(cached.data)
      setError(cached.data.length === 0)
      setLoadingProgress(100)
      setLoading(false)
      return () => { active = false }
    }

    setLoading(true)
    setError(false)
    setLoadingProgress(8)
    const progressTimer = window.setInterval(() => {
      setLoadingProgress((current) => Math.min(92, current + Math.max(1, Math.round((92 - current) * 0.12))))
    }, 350)
    api.getHistoricalKlines({
      symbol: position.symbol,
      interval,
      start_time: bounds.start,
      end_time: bounds.end,
      username: position.username || undefined,
    }).then((data) => {
      if (!active) return
      klineCache.set(cacheKey, { expiresAt: Date.now() + KLINE_CACHE_TTL, data })
      setKlines(data)
      setError(data.length === 0)
      setLoadingProgress(100)
    }).catch(() => {
      if (active) setError(true)
    }).finally(() => {
      window.clearInterval(progressTimer)
      if (active) setLoading(false)
    })
    return () => {
      active = false
      window.clearInterval(progressTimer)
    }
  }, [bounds.end, bounds.start, interval, position.symbol, position.username])

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
    <div className="absolute inset-0 z-30 flex items-center justify-center bg-black/70 p-4" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section role="dialog" aria-modal="true" aria-label={t('log.chart.title')} className="flex h-full max-h-[820px] w-full max-w-[1500px] flex-col overflow-hidden rounded-lg border border-[#3b414b] bg-[#101318] shadow-2xl">
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
              return (
                <button key={item} type="button" onClick={() => setInterval(item)}
                  className={`rounded px-3 py-1 text-xs transition-colors ${interval === item ? 'bg-[#2f7cf6] text-white' : 'text-[#9aa3b2] hover:bg-[#252b33] hover:text-[#dce2ea]'}`}>
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
          {loading && <LoadingProgress progress={loadingProgress} label={t('log.chart.loading')} overlay />}
          {!loading && error && <div className="absolute inset-0 flex items-center justify-center text-sm text-[#f6465d]">{t('log.chart.failed')}</div>}
          {klines.length > 0 && <CandlestickChart
            klines={klines}
            markers={position.markers.filter((marker) => marker.timestamp >= bounds.start && marker.timestamp <= bounds.end)}
            startTime={position.startTime}
            endTime={position.endTime}
            locale={locale}
          />}
        </div>
      </section>
    </div>
  )
}

function LoadingProgress({ progress, label, overlay = false }: { progress: number; label: string; overlay?: boolean }) {
  return (
    <div className={`${overlay ? 'absolute inset-0 z-10 bg-[#101318]/85' : 'flex-1'} flex items-center justify-center`}>
      <div className="w-[min(420px,70%)]">
        <div className="mb-2 flex items-center justify-between text-xs text-[#a8b0bd]">
          <span>{label}</span><span className="font-mono">{Math.round(progress)}%</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-[#262d37]">
          <div className="h-full rounded-full bg-[#2f7cf6] transition-[width] duration-300 ease-out" style={{ width: `${progress}%` }} />
        </div>
      </div>
    </div>
  )
}

function useSimulatedProgress(initial: number, maximum: number): number {
  const [progress, setProgress] = useState(initial)
  useEffect(() => {
    const timer = window.setInterval(() => {
      setProgress((current) => Math.min(maximum, current + Math.max(1, Math.round((maximum - current) * 0.1))))
    }, 350)
    return () => window.clearInterval(timer)
  }, [maximum])
  return progress
}

function buildThousandBarWindow(position: PositionWindow, intervalMs: number): { start: number; end: number } {
  const barCount = 1000
  const windowSpan = intervalMs * barCount
  const holdingSpan = Math.max(0, position.endTime - position.startTime)
  let rawStart: number

  if (holdingSpan <= windowSpan * 0.9) {
    // Keep the whole holding period visible and distribute the spare candles
    // before and after it. The resulting request still contains 1000 bars.
    rawStart = position.startTime - (windowSpan - holdingSpan) / 2
  } else {
    // A fixed 1000-bar, 5-minute window cannot contain a very long position.
    // Focus on the row the user actually double-clicked instead.
    rawStart = position.focusTime - windowSpan / 2
  }

  let start = Math.max(0, Math.floor(rawStart / intervalMs) * intervalMs)
  let end = start + intervalMs * (barCount - 1)
  const latestAvailableBar = Math.floor(Date.now() / intervalMs) * intervalMs
  // Never spend part of the 1000-bar budget on future candles, otherwise a
  // recently closed/current position would receive fewer than 1000 rows.
  if (end > latestAvailableBar) {
    end = latestAvailableBar
    start = Math.max(0, end - intervalMs * (barCount - 1))
  }
  return { start, end }
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
  const [visibleRange, setVisibleRange] = useState(() => ({ start: 0, end: klines.length }))
  const dragRef = useRef<{ clientX: number; start: number; end: number } | null>(null)

  useEffect(() => {
    setVisibleRange({ start: 0, end: klines.length })
  }, [klines])

  const rangeStart = Math.max(0, Math.min(visibleRange.start, Math.max(0, klines.length - 1)))
  const rangeEnd = Math.max(rangeStart + 1, Math.min(visibleRange.end, klines.length))
  const visibleKlines = klines.slice(rangeStart, rangeEnd)
  const visibleCount = visibleKlines.length

  const zoom = (factor: number, anchorFraction = 0.5) => {
    const nextCount = Math.max(30, Math.min(klines.length, Math.round(visibleCount * factor)))
    const anchorIndex = rangeStart + visibleCount * anchorFraction
    let nextStart = Math.round(anchorIndex - nextCount * anchorFraction)
    nextStart = Math.max(0, Math.min(nextStart, klines.length - nextCount))
    setVisibleRange({ start: nextStart, end: nextStart + nextCount })
  }

  const resetZoom = () => setVisibleRange({ start: 0, end: klines.length })

  const width = 1400
  const height = 690
  const margin = { top: 48, right: 92, bottom: 54, left: 18 }
  const volumeHeight = 80
  const priceBottom = height - margin.bottom - volumeHeight - 28
  const plotWidth = width - margin.left - margin.right
  const priceHeight = priceBottom - margin.top
  const minTime = visibleKlines[0].open_time
  const maxTime = Math.max(visibleKlines[visibleKlines.length - 1].close_time, minTime + 1)
  const visibleMarkers = markers.filter((marker) => marker.timestamp >= minTime && marker.timestamp <= maxTime)
  const allPrices = visibleKlines.flatMap((bar) => [bar.low, bar.high]).concat(visibleMarkers.map((marker) => marker.price))
  const rawMin = Math.min(...allPrices)
  const rawMax = Math.max(...allPrices)
  const pricePadding = Math.max((rawMax - rawMin) * 0.09, rawMax * 0.0005)
  const minPrice = rawMin - pricePadding
  const maxPrice = rawMax + pricePadding
  const maxVolume = Math.max(...visibleKlines.map((bar) => bar.volume), 1)
  const candleWidth = Math.max(1, Math.min(18, (plotWidth / visibleKlines.length) * 0.7))
  const x = (time: number) => margin.left + ((time - minTime) / (maxTime - minTime)) * plotWidth
  const clampedX = (time: number) => Math.max(margin.left, Math.min(width - margin.right, x(time)))
  const y = (price: number) => margin.top + ((maxPrice - price) / (maxPrice - minPrice)) * priceHeight
  const volumeY = (volume: number) => height - margin.bottom - (volume / maxVolume) * volumeHeight
  const ema = computeEma(klines.map((bar) => bar.close), 20).slice(rangeStart, rangeEnd)
  const emaPoints = ema.map((value, index) => value == null ? null : `${x(visibleKlines[index].open_time)},${y(value)}`).filter(Boolean).join(' ')
  const timeTicks = Array.from({ length: 7 }, (_, index) => minTime + ((maxTime - minTime) * index) / 6)
  const priceTicks = Array.from({ length: 7 }, (_, index) => minPrice + ((maxPrice - minPrice) * index) / 6)

  return (
    <div className="relative h-full w-full overflow-hidden rounded bg-[#0d1014]">
      <div className="absolute left-3 top-3 z-10 flex items-center gap-1 rounded border border-[#343b46] bg-[#171b21]/95 p-1 text-[#aeb7c4] shadow">
        <button type="button" onClick={() => zoom(0.7)} title="Zoom in" className="rounded p-1 hover:bg-[#2b333e] hover:text-white"><ZoomIn size={15} /></button>
        <button type="button" onClick={() => zoom(1.4)} title="Zoom out" className="rounded p-1 hover:bg-[#2b333e] hover:text-white"><ZoomOut size={15} /></button>
        <button type="button" onClick={resetZoom} title="Reset" className="rounded p-1 hover:bg-[#2b333e] hover:text-white"><RotateCcw size={14} /></button>
        <span className="border-l border-[#3b424d] px-1.5 text-[10px] text-[#818b9a]">{visibleCount}/{klines.length}</span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className="h-full w-full select-none touch-none cursor-grab active:cursor-grabbing"
        role="img"
        onDoubleClick={resetZoom}
        onWheel={(event) => {
          event.preventDefault()
          const rect = event.currentTarget.getBoundingClientRect()
          const anchor = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)))
          zoom(event.deltaY < 0 ? 0.78 : 1.28, anchor)
        }}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId)
          dragRef.current = { clientX: event.clientX, start: rangeStart, end: rangeEnd }
        }}
        onPointerMove={(event) => {
          const drag = dragRef.current
          if (!drag) return
          const rect = event.currentTarget.getBoundingClientRect()
          const count = drag.end - drag.start
          const shift = Math.round(((drag.clientX - event.clientX) / Math.max(1, rect.width)) * count)
          const nextStart = Math.max(0, Math.min(drag.start + shift, klines.length - count))
          setVisibleRange({ start: nextStart, end: nextStart + count })
        }}
        onPointerUp={() => { dragRef.current = null }}
        onPointerCancel={() => { dragRef.current = null }}
      >
      <rect x={clampedX(startTime)} y={margin.top} width={Math.max(1, clampedX(endTime) - clampedX(startTime))} height={priceHeight} fill="#2f7cf6" opacity="0.045" />
      {priceTicks.map((price) => <g key={price}>
        <line x1={margin.left} x2={width - margin.right} y1={y(price)} y2={y(price)} stroke="#252b33" strokeWidth="1" />
        <text x={width - margin.right + 10} y={y(price) + 4} fill="#758091" fontSize="12">{formatPrice(price)}</text>
      </g>)}
      {timeTicks.map((time) => <g key={time}>
        <line x1={x(time)} x2={x(time)} y1={margin.top} y2={height - margin.bottom} stroke="#20262e" strokeWidth="1" />
        <text x={x(time)} y={height - 18} textAnchor="middle" fill="#758091" fontSize="12">{formatAxisTime(time, locale)}</text>
      </g>)}

      {visibleKlines.map((bar) => {
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
      {startTime >= minTime && startTime <= maxTime && <line x1={x(startTime)} x2={x(startTime)} y1={margin.top} y2={height - margin.bottom} stroke="#4d8dff" strokeDasharray="5 5" opacity="0.65" />}
      {endTime >= minTime && endTime <= maxTime && <line x1={x(endTime)} x2={x(endTime)} y1={margin.top} y2={height - margin.bottom} stroke="#4d8dff" strokeDasharray="5 5" opacity="0.65" />}

      {visibleMarkers.map((marker, index) => {
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
    </div>
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
