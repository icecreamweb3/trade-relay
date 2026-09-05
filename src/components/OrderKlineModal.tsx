import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { createPortal } from 'react-dom'
import { GripHorizontal, RotateCcw, X, ZoomIn, ZoomOut } from 'lucide-react'
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
  const floating = useFloatingPanel({ width: 520, height: 170 })

  return createPortal(
      <section ref={floating.panelRef} role="dialog" aria-modal="false" style={{ left: floating.position.x, top: floating.position.y, WebkitAppRegion: 'no-drag' } as React.CSSProperties} className="fixed z-[200] flex h-[170px] w-[520px] max-w-[calc(100vw-24px)] flex-col overflow-hidden rounded-lg border border-[#4a5361] bg-[#101318] shadow-[0_10px_40px_rgba(0,0,0,0.75)]">
        <header onMouseDown={floating.onMouseDown} style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties} className="flex cursor-move select-none items-center justify-between border-b border-[#2c323b] bg-[#171b21] px-4 py-3">
          <div className="flex items-center gap-2"><GripHorizontal size={16} className="text-[#6f7a89]" /><h2 className="text-base font-semibold text-[#e6e9ef]">{symbol} · {t('log.chart.title')}</h2></div>
          <button type="button" onClick={onClose} aria-label={t('common.close')} className="rounded p-1.5 text-[#9aa3b2] hover:bg-[#2b313b] hover:text-white"><X size={19} /></button>
        </header>
        <LoadingProgress progress={progress} label={t('log.chart.matchingPosition')} />
      </section>,
    document.body,
  )
}

export function OrderKlineModal({ position, onClose, standalone = false }: { position: PositionWindow; onClose: () => void; standalone?: boolean }) {
  const locale = useUiPreferencesStore((state) => state.locale)
  const { t } = useTranslation(locale)
  const [interval, setInterval] = useState('5m')
  const [klines, setKlines] = useState<ApiKline[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [loadingProgress, setLoadingProgress] = useState(8)
  const [showBarNumbers, setShowBarNumbers] = useState(false)
  const floating = useFloatingPanel()

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

  return createPortal(
      <section
        ref={standalone ? undefined : floating.panelRef}
        role="dialog"
        aria-modal="false"
        aria-label={t('log.chart.title')}
        style={standalone
          ? ({ left: 0, top: 0, WebkitAppRegion: 'no-drag' } as React.CSSProperties)
          : ({ left: floating.position.x, top: floating.position.y, WebkitAppRegion: 'no-drag' } as React.CSSProperties)}
        className={standalone
          ? 'fixed inset-0 flex h-screen w-screen flex-col overflow-hidden bg-[#101318]'
          : 'fixed z-[200] flex h-[93.6vh] min-h-[672px] max-h-[96vh] w-[88vw] min-w-[620px] max-w-[1500px] resize flex-col overflow-hidden rounded-lg border border-[#4a5361] bg-[#101318] shadow-[0_10px_40px_rgba(0,0,0,0.75)]'}
      >
        <header
          onMouseDown={standalone ? undefined : floating.onMouseDown}
          style={{ WebkitAppRegion: standalone ? 'drag' : 'no-drag' } as React.CSSProperties}
          className="flex cursor-move select-none items-center justify-between border-b border-[#2c323b] bg-[#171b21] px-4 py-3"
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <GripHorizontal size={16} className="shrink-0 text-[#6f7a89]" />
              <h2 className="text-base font-semibold text-[#e6e9ef]">{position.symbol} · {t('log.chart.title')}</h2>
              <span className={`rounded px-2 py-0.5 text-xs font-semibold ${position.positionSide === 'SHORT' ? 'bg-[#f6465d]/15 text-[#f6465d]' : 'bg-[#0ecb81]/15 text-[#0ecb81]'}`}>{sideLabel}</span>
              {position.isOpen && <span className="rounded bg-[#f0b90b]/15 px-2 py-0.5 text-xs text-[#f0b90b]">{t('log.chart.openPosition')}</span>}
            </div>
            <div className="mt-1 truncate text-xs text-[#8b94a5]">
              {position.username} · {formatDateTime(position.startTime)} — {position.isOpen ? t('log.chart.now') : formatDateTime(position.endTime)} · {duration}
            </div>
          </div>
          <button type="button" onClick={onClose} style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties} aria-label={t('common.close')} className="rounded p-1.5 text-[#9aa3b2] hover:bg-[#2b313b] hover:text-white"><X size={19} /></button>
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
            <label className="flex cursor-pointer select-none items-center gap-2 text-[#aab2bf]">
              <button
                type="button"
                role="switch"
                aria-checked={showBarNumbers}
                onClick={() => setShowBarNumbers((visible) => !visible)}
                className={`relative h-4 w-8 rounded-full transition-colors ${showBarNumbers ? 'bg-[#2f7cf6]' : 'bg-[#39414d]'}`}
              >
                <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-white shadow transition-transform ${showBarNumbers ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
              </button>
              <span>{t('log.chart.showBarNumbers')}</span>
            </label>
            <span className="text-[#1687ff]">↑ {t('side.buy')}</span>
            <span className="text-[#f6465d]">↓ {t('side.sell')}</span>
            <span className="text-[#d95b8b]">EMA 20</span>
          </div>
        </div>

        <div className="relative min-h-[280px] flex-1 p-3">
          {loading && <LoadingProgress progress={loadingProgress} label={t('log.chart.loading')} overlay />}
          {!loading && error && <div className="absolute inset-0 flex items-center justify-center text-sm text-[#f6465d]">{t('log.chart.failed')}</div>}
          {klines.length > 0 && <CandlestickChart
            klines={klines}
            markers={position.markers.filter((marker) => marker.timestamp >= bounds.start && marker.timestamp <= bounds.end)}
            startTime={position.startTime}
            endTime={position.endTime}
            locale={locale}
            showBarNumbers={showBarNumbers}
          />}
        </div>

        <FillRecords symbol={position.symbol} markers={position.markers} t={t} />
      </section>,
    document.body,
  )
}

function FillRecords({
  symbol,
  markers,
  t,
}: {
  symbol: string
  markers: PositionFillMarker[]
  t: (key: string, vars?: Record<string, string | number>) => string
}) {
  const sortedMarkers = [...markers].sort((left, right) => left.timestamp - right.timestamp || left.id - right.id)

  return (
    <section className="max-h-[190px] shrink-0 overflow-auto border-t border-[#2c323b] bg-[#11151b]" aria-label={t('log.chart.fills')}>
      <div className="sticky top-0 z-10 flex items-center border-b border-[#252b33] bg-[#171b21] px-4 py-2">
        <h3 className="text-xs font-semibold text-[#dce2ea]">{t('log.chart.fills')}</h3>
        <span className="ml-2 rounded bg-[#2b313b] px-1.5 py-0.5 text-[10px] tabular-nums text-[#9aa3b2]">{sortedMarkers.length}</span>
      </div>
      <table className="w-full min-w-[1320px] text-left text-xs">
        <thead className="text-[#7f8998]">
          <tr className="border-b border-[#252b33]">
            <th className="px-4 py-2 font-medium">{t('log.symbol')}</th>
            <th className="px-3 py-2 font-medium">{t('log.createdAt')}</th>
            <th className="px-3 py-2 font-medium">{t('log.filledAt')}</th>
            <th className="px-3 py-2 font-medium">{t('log.side')}</th>
            <th className="px-3 py-2 font-medium">{t('log.type')}</th>
            <th className="px-3 py-2 text-right font-medium">{t('log.qty')}</th>
            <th className="px-3 py-2 font-medium">{t('log.dir')}</th>
            <th className="px-3 py-2 text-right font-medium">{t('log.price')}</th>
            <th className="px-3 py-2 text-right font-medium">{t('log.filledPrice')}</th>
            <th className="px-3 py-2 text-right font-medium">{t('log.notional')}</th>
            <th className="px-3 py-2 text-right font-medium">{t('log.realizedPnl')}</th>
            <th className="px-3 py-2 text-right font-medium">{t('trade.commission')}</th>
            <th className="px-4 py-2 font-medium">{t('trade.commissionAsset')}</th>
          </tr>
        </thead>
        <tbody>
          {sortedMarkers.map((marker) => {
            const isBuy = String(marker.side).toUpperCase() === 'BUY'
            const isClose = marker.tradeDirection.toUpperCase() === 'CLOSE' || marker.action === 'EXIT'
            return (
              <tr key={`${marker.id}-${marker.action}`} className="border-b border-[#20262e] last:border-b-0 hover:bg-[#1a1f27]">
                <td className="whitespace-nowrap px-4 py-2 font-semibold text-[#dfe4eb]">{symbol}</td>
                <td className="whitespace-nowrap px-3 py-2 tabular-nums text-[#8993a2]">{formatFillDateTime(marker.createdAt)}</td>
                <td className="whitespace-nowrap px-3 py-2 tabular-nums text-[#8993a2]">{formatFillDateTime(marker.timestamp)}</td>
                <td className={`whitespace-nowrap px-3 py-2 font-semibold ${isBuy ? 'text-[#1687ff]' : 'text-[#f6465d]'}`}>
                  {t(isBuy ? 'side.buy' : 'side.sell')}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-[#8993a2]">{formatFillOrderType(marker.orderType, t)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono tabular-nums text-[#c8ced8]">{formatCompactNumber(marker.quantity)}</td>
                <td className={`whitespace-nowrap px-3 py-2 font-medium ${isClose ? 'text-[#f6465d]' : 'text-[#0ecb81]'}`}>
                  {t(isClose ? 'order.close' : 'order.open')}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono tabular-nums text-[#c8ced8]">{marker.orderPrice == null ? t('log.market') : marker.orderPrice.toFixed(2)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono tabular-nums text-[#e0e5ec]">{marker.price.toFixed(2)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono tabular-nums text-[#c8ced8]">{(marker.price * marker.quantity).toFixed(2)}</td>
                <td className={`whitespace-nowrap px-3 py-2 text-right font-mono tabular-nums ${fillNumberTone(marker.realizedPnl)}`}>{formatFillNumber(marker.realizedPnl)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono tabular-nums text-[#8993a2]">{formatFillNumber(marker.commission, false)}</td>
                <td className="whitespace-nowrap px-4 py-2 font-mono text-[#8993a2]">{marker.commissionAsset ?? '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}

function formatFillDateTime(timestamp: number | null): string {
  if (timestamp == null) return '—'
  const date = new Date(timestamp)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}/${pad(date.getDate())}/${date.getFullYear()}, ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function formatFillOrderType(orderType: string, t: (key: string) => string): string {
  switch (orderType.toUpperCase()) {
    case 'LIMIT': return t('type.limit')
    case 'MARKET': return t('type.market')
    case 'STOP': return t('type.stop')
    case 'STOP_MARKET': return t('type.stopMarket')
    case 'TAKE_PROFIT': return t('type.takeProfit')
    case 'TAKE_PROFIT_MARKET': return t('type.takeProfitMarket')
    default: return orderType || '—'
  }
}

function formatFillNumber(value: number | null, signed = true): string {
  if (value == null) return '—'
  return `${signed && value > 0 ? '+' : ''}${value.toFixed(4)}`
}

function fillNumberTone(value: number | null): string {
  if (value == null || value === 0) return 'text-[#8993a2]'
  return value > 0 ? 'text-[#0ecb81]' : 'text-[#f6465d]'
}

function useFloatingPanel(initialSize?: { width: number; height: number }) {
  const panelRef = useRef<HTMLElement>(null)
  const dragRef = useRef<{ clientX: number; clientY: number; x: number; y: number } | null>(null)
  const [position, setPosition] = useState(() => ({
    x: initialSize ? Math.max(12, (window.innerWidth - initialSize.width) / 2) : Math.max(12, window.innerWidth * 0.06),
    y: initialSize ? Math.max(12, (window.innerHeight - initialSize.height) / 2) : Math.max(12, window.innerHeight * 0.02),
  }))

  useEffect(() => {
    const handleMouseMove = (event: MouseEvent) => {
      const drag = dragRef.current
      if (!drag) return
      const panel = panelRef.current
      const width = panel?.offsetWidth ?? 620
      const height = panel?.offsetHeight ?? 360
      const nextX = drag.x + event.clientX - drag.clientX
      const nextY = drag.y + event.clientY - drag.clientY
      setPosition({
        x: Math.max(0, Math.min(nextX, window.innerWidth - Math.min(width, window.innerWidth))),
        y: Math.max(0, Math.min(nextY, window.innerHeight - Math.min(height, window.innerHeight))),
      })
    }
    const handleMouseUp = () => {
      dragRef.current = null
      document.body.style.cursor = ''
    }
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
      document.body.style.cursor = ''
    }
  }, [])

  const onMouseDown = (event: ReactMouseEvent<HTMLElement>) => {
    if (event.button !== 0 || (event.target as HTMLElement).closest('button')) return
    dragRef.current = {
      clientX: event.clientX,
      clientY: event.clientY,
      x: position.x,
      y: position.y,
    }
    document.body.style.cursor = 'move'
    event.preventDefault()
    event.stopPropagation()
  }

  return { panelRef, position, onMouseDown }
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
  showBarNumbers,
}: {
  klines: ApiKline[]
  markers: PositionFillMarker[]
  startTime: number
  endTime: number
  locale: string
  showBarNumbers: boolean
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
  const barNumberStep = Math.max(1, Math.ceil(24 / Math.max(1, plotWidth / visibleCount)))
  const x = (time: number) => margin.left + ((time - minTime) / (maxTime - minTime)) * plotWidth
  const clampedX = (time: number) => Math.max(margin.left, Math.min(width - margin.right, x(time)))
  const y = (price: number) => margin.top + ((maxPrice - price) / (maxPrice - minPrice)) * priceHeight
  const volumeY = (volume: number) => height - margin.bottom - (volume / maxVolume) * volumeHeight
  const ema = computeEma(klines.map((bar) => bar.close), 20).slice(rangeStart, rangeEnd)
  const emaPoints = ema.map((value, index) => value == null ? null : `${x(visibleKlines[index].open_time)},${y(value)}`).filter(Boolean).join(' ')
  const timeTicks = buildAlignedTimeTicks(minTime, maxTime, visibleKlines)
  const priceTicks = Array.from({ length: 7 }, (_, index) => minPrice + ((maxPrice - minPrice) * index) / 6)
  const markerPlacements = visibleMarkers.map((marker, index, list) => {
    const bar = findMarkerBar(visibleKlines, marker.timestamp)
    const sameBarLane = list.slice(0, index).filter((previous) => {
      const previousBar = findMarkerBar(visibleKlines, previous.timestamp)
      return previous.side === marker.side && previousBar.open_time === bar.open_time
    }).length
    return { marker, bar, lane: sameBarLane }
  })

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
      {showBarNumbers && visibleKlines.map((bar, index) => {
        const isEdge = index === 0 || index === visibleKlines.length - 1
        if (!isEdge && (rangeStart + index) % barNumberStep !== 0) return null
        const centerX = x(bar.open_time + (bar.close_time - bar.open_time) / 2)
        return <text
          key={`bar-number-${bar.open_time}`}
          x={centerX}
          y={Math.max(margin.top + 11, y(bar.high) - 6)}
          textAnchor="middle"
          fill="#f97316"
          fontSize="9"
          fontWeight="500"
          pointerEvents="none"
        >#{rangeStart + index + 1}</text>
      })}
      {emaPoints && <polyline points={emaPoints} fill="none" stroke="#d95b8b" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />}
      <line x1={margin.left} x2={width - margin.right} y1={height - margin.bottom - volumeHeight} y2={height - margin.bottom - volumeHeight} stroke="#303741" />
      {startTime >= minTime && startTime <= maxTime && <line x1={x(startTime)} x2={x(startTime)} y1={margin.top} y2={height - margin.bottom} stroke="#4d8dff" strokeDasharray="5 5" opacity="0.65" />}
      {endTime >= minTime && endTime <= maxTime && <line x1={x(endTime)} x2={x(endTime)} y1={margin.top} y2={height - margin.bottom} stroke="#4d8dff" strokeDasharray="5 5" opacity="0.65" />}

      {markerPlacements.map(({ marker, bar, lane }) => {
        const markerX = Math.max(margin.left + 20, Math.min(width - margin.right - 20, x(bar.open_time + (bar.close_time - bar.open_time) / 2)))
        const isBuy = String(marker.side).toUpperCase() === 'BUY'
        const color = isBuy ? '#1687ff' : '#f6465d'
        const candleEdgeY = isBuy ? y(bar.low) : y(bar.high)
        const tipY = isBuy ? candleEdgeY + 3 : candleEdgeY - 3
        const stemY = isBuy ? tipY + 9 + lane * 17 : tipY - 9 - lane * 17
        const firstLabelY = isBuy ? stemY + 12 : stemY - 16
        return <g key={`${marker.id}-${marker.action}`}>
          <line x1={markerX} x2={markerX} y1={tipY} y2={stemY} stroke={color} strokeWidth="1.5" />
          <path d={isBuy
            ? `M ${markerX - 3.5} ${tipY + 5} L ${markerX} ${tipY} L ${markerX + 3.5} ${tipY + 5}`
            : `M ${markerX - 3.5} ${tipY - 5} L ${markerX} ${tipY} L ${markerX + 3.5} ${tipY - 5}`}
            fill="none" stroke={color} strokeWidth="1.5" />
          <text x={markerX} y={firstLabelY} textAnchor="middle" fill="#e4e8ee" fontSize="10.5" fontWeight="600">
            <tspan x={markerX}>{formatCompactNumber(marker.quantity)}</tspan>
            <tspan x={markerX} dy="11" fill="#aab2bf">@ {formatPrice(marker.price)}</tspan>
          </text>
          <title>{`${String(marker.side).toUpperCase()} · ${marker.action === 'ENTRY' ? 'Entry' : 'Exit'} ${formatCompactNumber(marker.quantity)} @ ${marker.price} · ${formatDateTime(marker.timestamp)}`}</title>
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

function findMarkerBar(klines: ApiKline[], timestamp: number): ApiKline {
  let low = 0
  let high = klines.length - 1
  while (low <= high) {
    const middle = Math.floor((low + high) / 2)
    const bar = klines[middle]
    if (timestamp < bar.open_time) high = middle - 1
    else if (timestamp > bar.close_time) low = middle + 1
    else return bar
  }
  const index = Math.max(0, Math.min(klines.length - 1, low))
  const candidate = klines[index]
  const previous = klines[Math.max(0, index - 1)]
  return Math.abs(candidate.open_time - timestamp) < Math.abs(previous.close_time - timestamp) ? candidate : previous
}

function buildAlignedTimeTicks(minTime: number, maxTime: number, klines: ApiKline[]): number[] {
  const fiveMinutes = 5 * 60_000
  const inferredInterval = klines.length > 1
    ? Math.max(1, klines[1].open_time - klines[0].open_time)
    : Math.max(1, klines[0].close_time - klines[0].open_time + 1)
  const alignmentUnit = inferredInterval <= 30 * 60_000
    ? fiveMinutes
    : inferredInterval < 24 * 60 * 60_000 ? 60 * 60_000 : 24 * 60 * 60_000
  const targetStep = (maxTime - minTime) / 7
  const step = Math.max(alignmentUnit, Math.ceil(targetStep / alignmentUnit) * alignmentUnit)
  const first = Math.ceil(minTime / step) * step
  const ticks: number[] = []
  for (let time = first; time <= maxTime; time += step) ticks.push(time)
  return ticks
}

function formatPrice(value: number): string {
  if (Math.abs(value) >= 1000) return value.toLocaleString('en-US', { maximumFractionDigits: 2 })
  if (Math.abs(value) >= 1) return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')
  return value.toPrecision(5)
}

function formatCompactNumber(value: number): string {
  const truncated = Math.trunc((value + Number.EPSILON) * 1000) / 1000
  return truncated.toLocaleString('en-US', { maximumFractionDigits: 3 })
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
