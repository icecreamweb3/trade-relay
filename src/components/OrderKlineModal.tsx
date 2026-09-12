import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react'
import { createPortal } from 'react-dom'
import { GripHorizontal, RotateCcw, X, ZoomIn, ZoomOut } from 'lucide-react'
import { api, type ApiKline, type ApiPositionReviewInput } from '../api/client'
import { useTranslation } from '../i18n/translations'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'
import { parseUtcTimestamp } from '../utils/datetime'
import type { PositionFillMarker, PositionWindow } from '../utils/orderChart'

const INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'] as const
const SETUP_OPTIONS = [
  'SPIKE_AND_CHANNEL',
  'WEDGE_REVERSAL_3_PUSH',
  'TWENTY_GAP_BARS',
  'TRIANGLES',
  'EXPANDING_TRIANGLES',
  'INSIDE_INSIDE',
  'INSIDE_OUTSIDE_INSIDE',
  'TWO_BAR_REVERSAL',
  'BULL_BEAR_FLAG',
  'DOUBLE_TOP_BOTTOM_FLAG',
  'OTHER',
] as const
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
const BEIJING_OFFSET_MS = 8 * 60 * 60_000
const US_OPENING_RANGE_START_MINUTE = 21 * 60 + 30
const US_OPENING_RANGE_DURATION_MS = 30 * 60_000
const BEIJING_EVENING_SESSION_END_MINUTE = 6 * 60

interface OpeningRange {
  start: number
  end: number
  high: number
  low: number
}

interface SignalCandleSelection {
  interval: '1m' | '5m' | '15m' | '1h' | '4h' | '1d'
  openTime: number
  number: number
}

/** Most recent Beijing-time [21:30, 22:00) window at the position entry. */
export function buildBeijingUsOpeningRangeWindow(referenceTime: number): { start: number; end: number } {
  const beijingDate = new Date(referenceTime + BEIJING_OFFSET_MS)
  const beijingDayStartAsUtc = Date.UTC(
    beijingDate.getUTCFullYear(),
    beijingDate.getUTCMonth(),
    beijingDate.getUTCDate(),
  )
  let start = beijingDayStartAsUtc - BEIJING_OFFSET_MS + US_OPENING_RANGE_START_MINUTE * 60_000
  if (referenceTime < start) start -= 24 * 60 * 60_000
  return { start, end: start + US_OPENING_RANGE_DURATION_MS }
}

/** Whether a position overlaps the latest 21:30–06:00 Beijing evening session. */
export function shouldShowOpeningRangeByDefault(startTime: number, endTime = startTime): boolean {
  const openingRangeWindow = buildBeijingUsOpeningRangeWindow(endTime)
  const eveningSessionEnd = openingRangeWindow.start
    + (24 * 60 - US_OPENING_RANGE_START_MINUTE + BEIJING_EVENING_SESSION_END_MINUTE) * 60_000
  return endTime >= openingRangeWindow.start && startTime < eveningSessionEnd
}

export function calculateOpeningRange(klines: ApiKline[], start: number, end: number): OpeningRange | null {
  const rangeBars = klines.filter((bar) => bar.open_time >= start && bar.open_time < end)
  if (rangeBars.length === 0) return null
  return {
    start,
    end,
    high: Math.max(...rangeBars.map((bar) => bar.high)),
    low: Math.min(...rangeBars.map((bar) => bar.low)),
  }
}

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
  const [showOpeningRange, setShowOpeningRange] = useState(
    () => shouldShowOpeningRangeByDefault(position.startTime, position.endTime),
  )
  const [showOpeningRangeUpperExtensions, setShowOpeningRangeUpperExtensions] = useState(false)
  const [showOpeningRangeLowerExtensions, setShowOpeningRangeLowerExtensions] = useState(false)
  const [openingRangeKlines, setOpeningRangeKlines] = useState<ApiKline[]>([])
  const [signalCandle, setSignalCandle] = useState<SignalCandleSelection | null>(null)
  const floating = useFloatingPanel()

  const bounds = useMemo(() => {
    return buildThousandBarWindow(position, INTERVAL_MS[interval])
  }, [interval, position])

  const openingRangeWindow = useMemo(
    () => buildBeijingUsOpeningRangeWindow(position.endTime),
    [position.endTime],
  )
  const openingRange = useMemo(
    () => calculateOpeningRange(openingRangeKlines, openingRangeWindow.start, openingRangeWindow.end),
    [openingRangeKlines, openingRangeWindow.end, openingRangeWindow.start],
  )

  useEffect(() => {
    setShowOpeningRange(shouldShowOpeningRangeByDefault(position.startTime, position.endTime))
    setShowOpeningRangeUpperExtensions(false)
    setShowOpeningRangeLowerExtensions(false)
  }, [position.endTime, position.startTime])

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
    let active = true
    if (!showOpeningRange) {
      setOpeningRangeKlines([])
      return () => { active = false }
    }
    const requestEnd = openingRangeWindow.end - 1
    const cacheKey = `${position.username}|${position.symbol}|opening-range-1m|${openingRangeWindow.start}|${requestEnd}`
    const cached = klineCache.get(cacheKey)
    if (cached && cached.expiresAt > Date.now()) {
      setOpeningRangeKlines(cached.data)
      return () => { active = false }
    }

    setOpeningRangeKlines([])
    api.getHistoricalKlines({
      symbol: position.symbol,
      interval: '1m',
      start_time: openingRangeWindow.start,
      end_time: requestEnd,
      username: position.username || undefined,
    }).then((data) => {
      if (!active) return
      klineCache.set(cacheKey, { expiresAt: Date.now() + KLINE_CACHE_TTL, data })
      setOpeningRangeKlines(data)
    }).catch(() => {
      // The main position chart remains usable if this optional reference range
      // cannot be loaded (for example, for a newly listed instrument).
      if (active) setOpeningRangeKlines([])
    })
    return () => { active = false }
  }, [openingRangeWindow.end, openingRangeWindow.start, position.symbol, position.username, showOpeningRange])

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

        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#252b33] px-4 py-2">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <div className="flex shrink-0 gap-1">
              {INTERVALS.map((item) => {
                return (
                  <button key={item} type="button" onClick={() => setInterval(item)}
                    className={`rounded px-3 py-1 text-xs transition-colors ${interval === item ? 'bg-[#2f7cf6] text-white' : 'text-[#9aa3b2] hover:bg-[#252b33] hover:text-[#dce2ea]'}`}>
                    {item}
                  </button>
                )
              })}
            </div>
            <div className="flex shrink-0 items-center gap-3 whitespace-nowrap text-xs">
              <span className="text-[#1687ff]">↑ {t('side.buy')}</span>
              <span className="text-[#f6465d]">↓ {t('side.sell')}</span>
              <span className="text-[#d95b8b]">EMA 20</span>
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2 text-xs">
            <label className="flex shrink-0 cursor-pointer select-none items-center gap-2 whitespace-nowrap text-[#aab2bf]">
              <button
                type="button"
                role="switch"
                aria-checked={showBarNumbers}
                onClick={() => setShowBarNumbers((visible) => !visible)}
                className={`relative h-4 w-8 shrink-0 rounded-full transition-colors ${showBarNumbers ? 'bg-[#2f7cf6]' : 'bg-[#39414d]'}`}
              >
                <span className={`absolute left-0 top-0.5 h-3 w-3 rounded-full bg-white shadow transition-transform ${showBarNumbers ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
              </button>
              <span>{t('log.chart.showBarNumbers')}</span>
            </label>
            <label className="flex shrink-0 cursor-pointer select-none items-center gap-3 whitespace-nowrap text-[#f59e0b]">
              <button
                type="button"
                role="switch"
                aria-checked={showOpeningRange}
                onClick={() => setShowOpeningRange((visible) => !visible)}
                className={`relative h-4 w-8 shrink-0 rounded-full transition-colors ${showOpeningRange ? 'bg-[#d97706]' : 'bg-[#39414d]'}`}
              >
                <span className={`absolute left-0 top-0.5 h-3 w-3 rounded-full bg-white shadow transition-transform ${showOpeningRange ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
              </button>
              <span>{t('log.chart.usOpeningRange')}</span>
            </label>
            <label className={`flex shrink-0 select-none items-center gap-2 whitespace-nowrap text-[#ef8c9c] ${showOpeningRange ? 'cursor-pointer' : 'cursor-not-allowed opacity-45'}`}>
              <button
                type="button"
                role="switch"
                aria-checked={showOpeningRangeUpperExtensions}
                disabled={!showOpeningRange}
                onClick={() => setShowOpeningRangeUpperExtensions((visible) => !visible)}
                className={`relative h-4 w-8 shrink-0 rounded-full transition-colors ${showOpeningRangeUpperExtensions ? 'bg-[#d94a64]' : 'bg-[#39414d]'}`}
              >
                <span className={`absolute left-0 top-0.5 h-3 w-3 rounded-full bg-white shadow transition-transform ${showOpeningRangeUpperExtensions ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
              </button>
              <span>{t('log.chart.usOpeningRangeUpper')}</span>
            </label>
            <label className={`flex shrink-0 select-none items-center gap-2 whitespace-nowrap text-[#62a8e5] ${showOpeningRange ? 'cursor-pointer' : 'cursor-not-allowed opacity-45'}`}>
              <button
                type="button"
                role="switch"
                aria-checked={showOpeningRangeLowerExtensions}
                disabled={!showOpeningRange}
                onClick={() => setShowOpeningRangeLowerExtensions((visible) => !visible)}
                className={`relative h-4 w-8 shrink-0 rounded-full transition-colors ${showOpeningRangeLowerExtensions ? 'bg-[#2477b8]' : 'bg-[#39414d]'}`}
              >
                <span className={`absolute left-0 top-0.5 h-3 w-3 rounded-full bg-white shadow transition-transform ${showOpeningRangeLowerExtensions ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
              </button>
              <span>{t('log.chart.usOpeningRangeLower')}</span>
            </label>
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
            openingRange={showOpeningRange ? openingRange : null}
            showOpeningRangeUpperExtensions={showOpeningRangeUpperExtensions}
            showOpeningRangeLowerExtensions={showOpeningRangeLowerExtensions}
            candleInterval={interval as SignalCandleSelection['interval']}
            selectedSignalCandle={signalCandle}
            onSelectSignalCandle={(bar, number) => {
              setSignalCandle({ interval: interval as SignalCandleSelection['interval'], openTime: bar.open_time, number })
              setShowBarNumbers(true)
            }}
          />}
        </div>

        <FillRecords symbol={position.symbol} markers={position.markers} t={t} />
        {position.positionId != null && <PositionReviewForm positionId={position.positionId} entryPrice={position.entryPrice} positionSide={position.positionSide} plannedStopPrice={position.plannedStopPrice} signalCandle={signalCandle} onSignalCandleLoaded={setSignalCandle} t={t} />}
      </section>,
    document.body,
  )
}

type ReviewDraft = {
  market_state: '' | 'TREND' | 'RANGE' | 'CLIMAX_REVERSAL'
  setup_name: string
  entry_rationale: string
  signal_candle_trigger: string
  signal_candle_interval: '' | SignalCandleSelection['interval']
  signal_candle_open_time: string
  signal_candle_number: number | null
  opportunity_grade: '' | 'A' | 'B' | 'C'
  estimated_win_probability: '' | '20' | '40' | '60' | '80'
  first_target_price: string
  is_planned_trade: '' | 'YES' | 'NO'
  first_entry_pnl_state: '' | 'PROFIT' | 'LOSS' | 'BREAKEVEN' | 'NOT_APPLICABLE'
  planned_stop_price: string
  actual_stop_fill_price: string
  first_target: string
  structural_target: string
  final_exit_reason: string
  discipline_trigger: '' | 'NONE' | 'COOLDOWN' | 'STOP_TRADING' | 'BOTH'
}

const EMPTY_REVIEW: ReviewDraft = {
  market_state: '', setup_name: '', entry_rationale: '', signal_candle_trigger: '',
  signal_candle_interval: '', signal_candle_open_time: '', signal_candle_number: null,
  opportunity_grade: '', estimated_win_probability: '', first_target_price: '', is_planned_trade: '', first_entry_pnl_state: '',
  planned_stop_price: '', actual_stop_fill_price: '', first_target: '', structural_target: '',
  final_exit_reason: '', discipline_trigger: '',
}

interface OpportunityScoreResult {
  status: 'incomplete' | 'invalid' | 'qualified' | 'unqualified'
  rewardRisk: number | null
  expectedValue: number | null
  score: number | null
  grade: 'A' | 'B' | 'C' | null
}

function calculateReviewOpportunityScore(
  entryPrice: number | null | undefined,
  stopPrice: number | null,
  targetPrice: number | null,
  probabilityPercent: number | null,
  side: PositionWindow['positionSide'],
): OpportunityScoreResult {
  const empty = (status: 'incomplete' | 'invalid'): OpportunityScoreResult => ({
    status, rewardRisk: null, expectedValue: null, score: null, grade: null,
  })
  if (entryPrice == null || stopPrice == null || targetPrice == null || probabilityPercent == null) return empty('incomplete')
  if (![entryPrice, stopPrice, targetPrice].every((value) => Number.isFinite(value) && value > 0)) return empty('invalid')
  if (side === 'LONG' && !(stopPrice < entryPrice && entryPrice < targetPrice)) return empty('invalid')
  if (side === 'SHORT' && !(targetPrice < entryPrice && entryPrice < stopPrice)) return empty('invalid')
  if (side !== 'LONG' && side !== 'SHORT') return empty('invalid')
  const risk = Math.abs(entryPrice - stopPrice)
  if (risk <= 0) return empty('invalid')
  const rewardRisk = Math.abs(targetPrice - entryPrice) / risk
  const probability = probabilityPercent / 100
  const expectedValue = probability * rewardRisk - (1 - probability)
  const score = Math.max(0, Math.min(100, 50 + 25 * expectedValue))
  const grade = expectedValue >= 1 ? 'A' : expectedValue >= 0.4 ? 'B' : expectedValue > 0 ? 'C' : null
  return {
    status: grade ? 'qualified' : 'unqualified',
    rewardRisk,
    expectedValue,
    score,
    grade,
  }
}

function formatOpportunityScore(result: OpportunityScoreResult, t: (key: string) => string): string {
  if (result.status === 'incomplete') return t('review.score.incomplete')
  if (result.status === 'invalid') return t('review.score.invalid')
  const grade = result.grade ?? t('review.score.unqualified')
  const expectedValue = result.expectedValue ?? 0
  return `${grade} · ${(result.score ?? 0).toFixed(0)}/100 · RR ${(result.rewardRisk ?? 0).toFixed(2)} · EV ${expectedValue >= 0 ? '+' : ''}${expectedValue.toFixed(2)}R`
}

function PositionReviewForm({ positionId, entryPrice, positionSide, plannedStopPrice, signalCandle, onSignalCandleLoaded, t }: {
  positionId: number
  entryPrice?: number | null
  positionSide: PositionWindow['positionSide']
  plannedStopPrice?: number | null
  signalCandle: SignalCandleSelection | null
  onSignalCandleLoaded: (selection: SignalCandleSelection | null) => void
  t: (key: string, vars?: Record<string, string | number>) => string
}) {
  const [draft, setDraft] = useState<ReviewDraft>(EMPTY_REVIEW)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState<'idle' | 'saved' | 'error'>('idle')

  useEffect(() => {
    let active = true
    const authoritativePlannedStop = plannedStopPrice?.toString() ?? ''
    onSignalCandleLoaded(null)
    setDraft({ ...EMPTY_REVIEW, planned_stop_price: authoritativePlannedStop })
    setLoading(true)
    setStatus('idle')
    api.getPositionReview(positionId).then((review) => {
      if (!active || !review) return
      const savedSignalCandleOpenTime = parseUtcTimestamp(review.signal_candle_open_time)
      const savedSignalCandle = review.signal_candle_interval && savedSignalCandleOpenTime && review.signal_candle_number
        ? {
            interval: review.signal_candle_interval,
            openTime: savedSignalCandleOpenTime.getTime(),
            number: review.signal_candle_number,
          }
        : null
      onSignalCandleLoaded(savedSignalCandle)
      setDraft({
        market_state: review.market_state ?? '',
        setup_name: review.setup_name ?? '',
        entry_rationale: review.entry_rationale ?? '',
        signal_candle_trigger: review.signal_candle_trigger ?? '',
        signal_candle_interval: review.signal_candle_interval ?? '',
        signal_candle_open_time: review.signal_candle_open_time ?? '',
        signal_candle_number: review.signal_candle_number ?? null,
        opportunity_grade: review.opportunity_grade ?? '',
        estimated_win_probability: review.estimated_win_probability?.toString() as ReviewDraft['estimated_win_probability'] ?? '',
        first_target_price: review.first_target_price?.toString() ?? '',
        is_planned_trade: review.is_planned_trade == null ? '' : review.is_planned_trade ? 'YES' : 'NO',
        first_entry_pnl_state: review.first_entry_pnl_state ?? '',
        planned_stop_price: authoritativePlannedStop,
        actual_stop_fill_price: review.actual_stop_fill_price?.toString() ?? '',
        first_target: review.first_target ?? '',
        structural_target: review.structural_target ?? '',
        final_exit_reason: review.final_exit_reason ?? '',
        discipline_trigger: review.discipline_trigger ?? '',
      })
    }).catch(() => {
      if (active) setStatus('error')
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [plannedStopPrice, positionId])

  useEffect(() => {
    if (!signalCandle) return
    setDraft((current) => ({
      ...current,
      signal_candle_interval: signalCandle.interval,
      signal_candle_open_time: new Date(signalCandle.openTime).toISOString(),
      signal_candle_number: signalCandle.number,
    }))
    setStatus('idle')
  }, [signalCandle])

  const update = <K extends keyof ReviewDraft>(field: K, value: ReviewDraft[K]) => {
    setDraft((current) => ({ ...current, [field]: value }))
    setStatus('idle')
  }
  const textOrNull = (value: string) => value.trim() || null
  const priceOrNull = (value: string) => value.trim() ? Number(value) : null
  const opportunityScore = useMemo(() => calculateReviewOpportunityScore(
    entryPrice,
    priceOrNull(draft.planned_stop_price),
    priceOrNull(draft.first_target_price),
    draft.estimated_win_probability === '' ? null : Number(draft.estimated_win_probability),
    positionSide,
  ), [draft.estimated_win_probability, draft.first_target_price, draft.planned_stop_price, entryPrice, positionSide])

  const save = async () => {
    if (saving) return
    setSaving(true)
    setStatus('idle')
    const body: ApiPositionReviewInput = {
      market_state: draft.market_state || null,
      setup_name: textOrNull(draft.setup_name),
      entry_rationale: textOrNull(draft.entry_rationale),
      signal_candle_trigger: textOrNull(draft.signal_candle_trigger),
      signal_candle_interval: draft.signal_candle_interval || null,
      signal_candle_open_time: draft.signal_candle_open_time || null,
      signal_candle_number: draft.signal_candle_number,
      opportunity_grade: opportunityScore.grade,
      estimated_win_probability: draft.estimated_win_probability === '' ? null : Number(draft.estimated_win_probability) as 20 | 40 | 60 | 80,
      first_target_price: priceOrNull(draft.first_target_price),
      planned_reward_risk: opportunityScore.rewardRisk,
      expected_value_r: opportunityScore.expectedValue,
      opportunity_score: opportunityScore.score,
      is_planned_trade: draft.is_planned_trade === '' ? null : draft.is_planned_trade === 'YES',
      first_entry_pnl_state: draft.first_entry_pnl_state || null,
      planned_stop_price: priceOrNull(draft.planned_stop_price),
      actual_stop_fill_price: priceOrNull(draft.actual_stop_fill_price),
      first_target: textOrNull(draft.first_target),
      structural_target: textOrNull(draft.structural_target),
      final_exit_reason: textOrNull(draft.final_exit_reason),
      discipline_trigger: draft.discipline_trigger || null,
    }
    if ((body.planned_stop_price != null && (!Number.isFinite(body.planned_stop_price) || body.planned_stop_price <= 0))
      || (body.actual_stop_fill_price != null && (!Number.isFinite(body.actual_stop_fill_price) || body.actual_stop_fill_price <= 0))
      || (body.first_target_price != null && (!Number.isFinite(body.first_target_price) || body.first_target_price <= 0))) {
      setStatus('error')
      setSaving(false)
      return
    }
    try {
      await api.savePositionReview(positionId, body)
      setStatus('saved')
    } catch {
      setStatus('error')
    } finally {
      setSaving(false)
    }
  }

  const inputClass = 'h-8 w-full rounded border border-[#343b46] bg-[#151a21] px-2 text-xs text-[#dce2ea] outline-none focus:border-[#2f7cf6] disabled:opacity-50'
  const areaClass = `${inputClass} min-h-[52px] resize-y py-1.5`
  const label = (key: string, tipKey: string, control: React.ReactNode) => <label className="min-w-0"><span className="mb-1 flex items-center gap-1 text-[11px] text-[#8f99a8]">{t(key)}<span tabIndex={0} role="note" aria-label={t(tipKey)} title={t(tipKey)} className="inline-flex h-3.5 w-3.5 shrink-0 cursor-help items-center justify-center rounded-full border border-[#657083] text-[9px] font-semibold leading-none text-[#9da7b6] outline-none hover:border-[#2f7cf6] hover:text-[#69a4ff] focus:border-[#2f7cf6] focus:text-[#69a4ff]">?</span></span>{control}</label>

  return (
    <section className="max-h-[285px] shrink-0 overflow-auto border-t border-[#2c323b] bg-[#11151b] px-4 py-3" aria-label={t('review.title')}>
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2"><h3 className="text-xs font-semibold text-[#dce2ea]">{t('review.title')}</h3><span className="text-[10px] text-[#697382]">Position #{positionId}</span></div>
        <div className="flex items-center gap-2">
          {loading && <span className="text-[11px] text-[#8f99a8]">{t('review.loading')}</span>}
          {status === 'saved' && <span className="text-[11px] text-[#0ecb81]">{t('review.saved')}</span>}
          {status === 'error' && <span className="text-[11px] text-[#f6465d]">{t('review.failed')}</span>}
          <button type="button" onClick={() => void save()} disabled={loading || saving} className="rounded bg-[#2f7cf6] px-3 py-1.5 text-xs font-medium text-white hover:bg-[#438af7] disabled:opacity-50">{saving ? t('review.saving') : t('review.save')}</button>
        </div>
      </div>
      <div className="grid grid-cols-4 gap-x-3 gap-y-2">
        {label('review.marketState', 'review.tip.marketState', <select value={draft.market_state} onChange={(e) => update('market_state', e.target.value as ReviewDraft['market_state'])} className={inputClass}><option value="">{t('review.unset')}</option><option value="TREND">{t('review.market.trend')}</option><option value="RANGE">{t('review.market.range')}</option><option value="CLIMAX_REVERSAL">{t('review.market.climaxReversal')}</option></select>)}
        {label('review.setupName', 'review.tip.setupName', <select value={draft.setup_name} onChange={(e) => update('setup_name', e.target.value)} className={inputClass}><option value="">{t('review.unset')}</option>{draft.setup_name && !SETUP_OPTIONS.some((value) => value === draft.setup_name) && <option value={draft.setup_name}>{draft.setup_name}</option>}{SETUP_OPTIONS.map((value, index) => <option key={value} value={value}>{index + 1}. {t(`review.setup.${value}`)}</option>)}</select>)}
        {label('review.grade', 'review.tip.grade', <div className={`${inputClass} flex items-center ${opportunityScore.grade === 'A' ? 'text-[#0ecb81]' : opportunityScore.grade === 'B' ? 'text-[#69a4ff]' : opportunityScore.grade === 'C' ? 'text-[#f0b90b]' : opportunityScore.status === 'unqualified' ? 'text-[#f6465d]' : 'text-[#8f99a8]'}`}>{formatOpportunityScore(opportunityScore, t)}</div>)}
        {label('review.estimatedWinProbability', 'review.tip.estimatedWinProbability', <select value={draft.estimated_win_probability} onChange={(e) => update('estimated_win_probability', e.target.value as ReviewDraft['estimated_win_probability'])} className={inputClass}><option value="">{t('review.unset')}</option><option value="20">20%</option><option value="40">40%</option><option value="60">60%</option><option value="80">80%</option></select>)}
        {label('review.plannedTrade', 'review.tip.plannedTrade', <select value={draft.is_planned_trade} onChange={(e) => update('is_planned_trade', e.target.value as ReviewDraft['is_planned_trade'])} className={inputClass}><option value="">{t('review.unset')}</option><option value="YES">{t('review.yes')}</option><option value="NO">{t('review.no')}</option></select>)}
        {label('review.entryRationale', 'review.tip.entryRationale', <textarea value={draft.entry_rationale} onChange={(e) => update('entry_rationale', e.target.value)} className={areaClass} maxLength={5000} />)}
        {label('review.signalTrigger', 'review.tip.signalTrigger', <div><textarea value={draft.signal_candle_trigger} onChange={(e) => update('signal_candle_trigger', e.target.value)} className={areaClass} maxLength={5000} />{draft.signal_candle_interval && draft.signal_candle_open_time && draft.signal_candle_number != null ? <div className="mt-1 truncate text-[10px] text-[#69a4ff]">{draft.signal_candle_interval} · #{draft.signal_candle_number} · {formatStoredUtcDateTime(draft.signal_candle_open_time)}</div> : <div className="mt-1 text-[10px] text-[#7f8998]">{t('review.signalSelectHint')}</div>}</div>)}
        {label('review.firstEntryPnl', 'review.tip.firstEntryPnl', <select value={draft.first_entry_pnl_state} onChange={(e) => update('first_entry_pnl_state', e.target.value as ReviewDraft['first_entry_pnl_state'])} className={inputClass}><option value="">{t('review.unset')}</option><option value="PROFIT">{t('review.pnl.profit')}</option><option value="LOSS">{t('review.pnl.loss')}</option><option value="BREAKEVEN">{t('review.pnl.breakeven')}</option><option value="NOT_APPLICABLE">{t('review.notApplicable')}</option></select>)}
        {label('review.discipline', 'review.tip.discipline', <select value={draft.discipline_trigger} onChange={(e) => update('discipline_trigger', e.target.value as ReviewDraft['discipline_trigger'])} className={inputClass}><option value="">{t('review.unset')}</option><option value="NONE">{t('review.discipline.none')}</option><option value="COOLDOWN">{t('review.discipline.cooldown')}</option><option value="STOP_TRADING">{t('review.discipline.stop')}</option><option value="BOTH">{t('review.discipline.both')}</option></select>)}
        {label('review.plannedStop', 'review.tip.plannedStop', <input type="number" value={draft.planned_stop_price} readOnly title={t('review.plannedStopSource')} className={`${inputClass} cursor-not-allowed bg-[#20252d] text-[#aeb7c4]`} />)}
        {label('review.actualStop', 'review.tip.actualStop', <input type="number" min="0" step="any" value={draft.actual_stop_fill_price} onChange={(e) => update('actual_stop_fill_price', e.target.value)} className={inputClass} />)}
        {label('review.firstTargetPrice', 'review.tip.firstTargetPrice', <input type="number" min="0" step="any" value={draft.first_target_price} onChange={(e) => update('first_target_price', e.target.value)} className={inputClass} />)}
        {label('review.firstTarget', 'review.tip.firstTarget', <input value={draft.first_target} onChange={(e) => update('first_target', e.target.value)} className={inputClass} maxLength={255} />)}
        {label('review.structuralTarget', 'review.tip.structuralTarget', <input value={draft.structural_target} onChange={(e) => update('structural_target', e.target.value)} className={inputClass} maxLength={255} />)}
        <div className="col-span-4">{label('review.exitReason', 'review.tip.exitReason', <textarea value={draft.final_exit_reason} onChange={(e) => update('final_exit_reason', e.target.value)} className={areaClass} maxLength={5000} />)}</div>
      </div>
    </section>
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
  openingRange,
  showOpeningRangeUpperExtensions,
  showOpeningRangeLowerExtensions,
  candleInterval,
  selectedSignalCandle,
  onSelectSignalCandle,
}: {
  klines: ApiKline[]
  markers: PositionFillMarker[]
  startTime: number
  endTime: number
  locale: string
  showBarNumbers: boolean
  openingRange: OpeningRange | null
  showOpeningRangeUpperExtensions: boolean
  showOpeningRangeLowerExtensions: boolean
  candleInterval: SignalCandleSelection['interval']
  selectedSignalCandle: SignalCandleSelection | null
  onSelectSignalCandle: (bar: ApiKline, number: number) => void
}) {
  const [visibleRange, setVisibleRange] = useState(() => ({ start: 0, end: klines.length }))
  const dragRef = useRef<{ clientX: number; start: number; end: number; moved: boolean } | null>(null)

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
  const openingRangeApplies = openingRange != null && maxTime >= openingRange.start
  const openingRangeHeight = openingRange ? openingRange.high - openingRange.low : 0
  const upperExtensionPrices = openingRangeApplies && openingRange && showOpeningRangeUpperExtensions
    ? [openingRange.high + openingRangeHeight, openingRange.high + openingRangeHeight * 2]
    : []
  const lowerExtensionPrices = openingRangeApplies && openingRange && showOpeningRangeLowerExtensions
    ? [openingRange.low - openingRangeHeight, openingRange.low - openingRangeHeight * 2]
    : []
  const referencePrices = openingRangeApplies && openingRange
    ? [openingRange.low, openingRange.high, ...upperExtensionPrices, ...lowerExtensionPrices]
    : []
  const allPrices = visibleKlines.flatMap((bar) => [bar.low, bar.high])
    .concat(visibleMarkers.map((marker) => marker.price), referencePrices)
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
          dragRef.current = { clientX: event.clientX, start: rangeStart, end: rangeEnd, moved: false }
        }}
        onPointerMove={(event) => {
          const drag = dragRef.current
          if (!drag) return
          if (Math.abs(event.clientX - drag.clientX) > 3) drag.moved = true
          const rect = event.currentTarget.getBoundingClientRect()
          const count = drag.end - drag.start
          const shift = Math.round(((drag.clientX - event.clientX) / Math.max(1, rect.width)) * count)
          const nextStart = Math.max(0, Math.min(drag.start + shift, klines.length - count))
          setVisibleRange({ start: nextStart, end: nextStart + count })
        }}
        onPointerUp={(event) => {
          const drag = dragRef.current
          dragRef.current = null
          if (!drag || drag.moved) return
          const rect = event.currentTarget.getBoundingClientRect()
          const svgX = ((event.clientX - rect.left) / Math.max(1, rect.width)) * width
          if (svgX < margin.left || svgX > width - margin.right) return
          const targetTime = minTime + ((svgX - margin.left) / plotWidth) * (maxTime - minTime)
          let nearestIndex = 0
          let nearestDistance = Number.POSITIVE_INFINITY
          visibleKlines.forEach((bar, index) => {
            const center = bar.open_time + (bar.close_time - bar.open_time) / 2
            const distance = Math.abs(center - targetTime)
            if (distance < nearestDistance) {
              nearestIndex = index
              nearestDistance = distance
            }
          })
          onSelectSignalCandle(visibleKlines[nearestIndex], rangeStart + nearestIndex + 1)
        }}
        onPointerCancel={() => { dragRef.current = null }}
      >
      <rect x={clampedX(startTime)} y={margin.top} width={Math.max(1, clampedX(endTime) - clampedX(startTime))} height={priceHeight} fill="#2f7cf6" opacity="0.045" />
      {openingRangeApplies && openingRange && <g pointerEvents="none">
        <rect
          x={clampedX(openingRange.start)}
          y={y(openingRange.high)}
          width={Math.max(1, clampedX(openingRange.end) - clampedX(openingRange.start))}
          height={Math.max(1, y(openingRange.low) - y(openingRange.high))}
          fill="#f59e0b"
          opacity="0.16"
        />
        <rect
          x={clampedX(openingRange.end)}
          y={y(openingRange.high)}
          width={Math.max(0, width - margin.right - clampedX(openingRange.end))}
          height={Math.max(1, y(openingRange.low) - y(openingRange.high))}
          fill="#f59e0b"
          opacity="0.045"
        />
        <line x1={clampedX(openingRange.start)} x2={width - margin.right} y1={y(openingRange.high)} y2={y(openingRange.high)} stroke="#f59e0b" strokeWidth="1.25" strokeDasharray="6 4" opacity="0.9" />
        <line x1={clampedX(openingRange.start)} x2={width - margin.right} y1={y(openingRange.low)} y2={y(openingRange.low)} stroke="#f59e0b" strokeWidth="1.25" strokeDasharray="6 4" opacity="0.9" />
        <line x1={clampedX(openingRange.start)} x2={clampedX(openingRange.start)} y1={y(openingRange.high)} y2={y(openingRange.low)} stroke="#f59e0b" strokeWidth="1" opacity="0.65" />
        <line x1={clampedX(openingRange.end)} x2={clampedX(openingRange.end)} y1={y(openingRange.high)} y2={y(openingRange.low)} stroke="#f59e0b" strokeWidth="1" opacity="0.65" />
        <text x={width - margin.right - 6} y={y(openingRange.high) - 5} textAnchor="end" fill="#fbbf24" fontSize="10.5" fontWeight="600">ORH {formatPrice(openingRange.high)}</text>
        <text x={width - margin.right - 6} y={y(openingRange.low) + 13} textAnchor="end" fill="#fbbf24" fontSize="10.5" fontWeight="600">ORL {formatPrice(openingRange.low)}</text>
      </g>}
      {openingRangeApplies && openingRange && showOpeningRangeUpperExtensions && <OpeningRangeExtensions
        direction="upper"
        origin={openingRange.high}
        rangeHeight={openingRangeHeight}
        startX={clampedX(openingRange.end)}
        endX={width - margin.right}
        y={y}
      />}
      {openingRangeApplies && openingRange && showOpeningRangeLowerExtensions && <OpeningRangeExtensions
        direction="lower"
        origin={openingRange.low}
        rangeHeight={openingRangeHeight}
        startX={clampedX(openingRange.end)}
        endX={width - margin.right}
        y={y}
      />}
      {priceTicks.map((price) => <g key={price}>
        <line x1={margin.left} x2={width - margin.right} y1={y(price)} y2={y(price)} stroke="#252b33" strokeWidth="1" />
        <text x={width - margin.right + 10} y={y(price) + 4} fill="#758091" fontSize="12">{formatPrice(price)}</text>
      </g>)}
      {timeTicks.map((time) => <g key={time}>
        <line x1={x(time)} x2={x(time)} y1={margin.top} y2={height - margin.bottom} stroke="#20262e" strokeWidth="1" />
        <text x={x(time)} y={height - 18} textAnchor="middle" fill="#758091" fontSize="12">{formatAxisTime(time, locale)}</text>
      </g>)}

      {selectedSignalCandle?.interval === candleInterval && visibleKlines.some((bar) => bar.open_time === selectedSignalCandle.openTime) && (() => {
        const selectedBar = visibleKlines.find((bar) => bar.open_time === selectedSignalCandle.openTime)!
        const selectedX = x(selectedBar.open_time + (selectedBar.close_time - selectedBar.open_time) / 2)
        return <rect x={selectedX - Math.max(3, candleWidth / 2 + 2)} y={margin.top} width={Math.max(6, candleWidth + 4)} height={priceHeight} fill="#2f7cf6" opacity="0.18" pointerEvents="none" />
      })()}

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

function OpeningRangeExtensions({
  direction,
  origin,
  rangeHeight,
  startX,
  endX,
  y,
}: {
  direction: 'upper' | 'lower'
  origin: number
  rangeHeight: number
  startX: number
  endX: number
  y: (price: number) => number
}) {
  if (!(rangeHeight > 0) || endX <= startX) return null
  const multiplier = direction === 'upper' ? 1 : -1
  const first = origin + rangeHeight * multiplier
  const second = origin + rangeHeight * multiplier * 2
  const color = direction === 'upper' ? '#ef6b7f' : '#4b9bd8'
  const fill = direction === 'upper' ? '#d94a64' : '#2477b8'
  const bandTop = direction === 'upper' ? y(first) : y(origin)
  const secondBandTop = direction === 'upper' ? y(second) : y(first)

  return <g pointerEvents="none">
    <rect x={startX} y={bandTop} width={endX - startX} height={Math.abs(y(first) - y(origin))} fill={fill} opacity="0.045" />
    <rect x={startX} y={secondBandTop} width={endX - startX} height={Math.abs(y(second) - y(first))} fill={fill} opacity="0.025" />
    <line x1={startX} x2={endX} y1={y(first)} y2={y(first)} stroke={color} strokeWidth="1.1" strokeDasharray="4 4" opacity="0.85" />
    <line x1={startX} x2={endX} y1={y(second)} y2={y(second)} stroke={color} strokeWidth="1.1" strokeDasharray="4 4" opacity="0.85" />
    <text x={endX - 6} y={y(first) - 5} textAnchor="end" fill={color} fontSize="10.5" fontWeight="600">
      OR {direction === 'upper' ? '+' : '-'}1× {formatPrice(first)}
    </text>
    <text x={endX - 6} y={y(second) - 5} textAnchor="end" fill={color} fontSize="10.5" fontWeight="600">
      OR {direction === 'upper' ? '+' : '-'}2× {formatPrice(second)}
    </text>
  </g>
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

function formatStoredUtcDateTime(value: string): string {
  const parsed = parseUtcTimestamp(value)
  return parsed ? formatDateTime(parsed.getTime()) : value
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
