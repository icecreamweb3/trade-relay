/**
 * OrderBook — Binance Futures real-time depth (asks/bids)
 * Maintains a local order book from a REST snapshot plus diff depth stream.
 */
import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { useMarketStore } from '../store/marketStore'
import { Locale, useTranslation } from '../i18n/translations'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'

interface Level { price: number; qty: number; quoteQty: number; sum: number }
type RawLevel = [string, string]

interface DepthEvent {
  U: number
  u: number
  pu?: number
  b?: RawLevel[]
  a?: RawLevel[]
}

interface DepthSnapshot {
  lastUpdateId?: number
  bids?: RawLevel[]
  asks?: RawLevel[]
}

interface SpreadOption {
  label: string
  step: number
  displayValue: string
}

const SPREAD_OPTIONS: SpreadOption[] = [
  { label: '1x (0.1)', step: 0.1, displayValue: '0.1' },
  { label: '10x (1)', step: 1, displayValue: '1' },
  { label: '100x (10)', step: 10, displayValue: '10' },
  { label: '500x (50)', step: 50, displayValue: '50' },
  { label: '1000x (100)', step: 100, displayValue: '100' },
  { label: '10000x (1,000)', step: 1000, displayValue: '1,000' },
]

const QUOTE_ASSETS = ['USDT', 'USDC', 'FDUSD', 'BUSD', 'BTC', 'ETH'] as const
const BOOK_SNAPSHOT_LIMIT = 1000
const VISIBLE_LEVEL_COUNT = 19
const FSTREAM_COMBINED_STREAM_BASE = 'wss://fstream.binance.com/stream?streams='

function splitTradingSymbol(symbol: string) {
  const upperSymbol = symbol.toUpperCase()
  for (const quoteAsset of QUOTE_ASSETS) {
    if (upperSymbol.endsWith(quoteAsset) && upperSymbol.length > quoteAsset.length) {
      return { baseAsset: upperSymbol.slice(0, -quoteAsset.length), quoteAsset }
    }
  }
  return { baseAsset: upperSymbol, quoteAsset: 'USDT' }
}

function getPriceDecimals(step: number): number {
  if (step >= 1) return 0
  const text = String(step)
  return text.includes('.') ? text.split('.')[1].length : 0
}

function bucketPrice(price: number, step: number, side: 'ask' | 'bid'): number {
  if (step <= 0) return price
  const bucket = side === 'ask'
    ? Math.ceil(price / step) * step
    : Math.floor(price / step) * step
  return Number(bucket.toFixed(getPriceDecimals(step)))
}

function buildLevels(raw: RawLevel[], side: 'ask' | 'bid', priceStep: number): Level[] {
  const grouped = new Map<number, number>()

  raw
    .map(([p, q]) => ({ price: parseFloat(p), qty: parseFloat(q) }))
    .filter((level) => level.qty > 0)
    .forEach((level) => {
      const groupedPrice = bucketPrice(level.price, priceStep, side)
      grouped.set(groupedPrice, (grouped.get(groupedPrice) ?? 0) + level.qty)
    })

  const actualPrices = Array.from(grouped.keys()).sort((a, b) => side === 'ask' ? a - b : b - a)
  if (actualPrices.length === 0) {
    return []
  }

  const visibleActualPrices = actualPrices.slice(0, VISIBLE_LEVEL_COUNT)
  const firstPrice = visibleActualPrices[0]
  const lastPrice = visibleActualPrices[visibleActualPrices.length - 1]
  const direction = side === 'ask' ? 1 : -1
  const decimals = getPriceDecimals(priceStep)
  const levelCount = Math.min(
    VISIBLE_LEVEL_COUNT,
    Math.max(1, Math.round(Math.abs(lastPrice - firstPrice) / priceStep) + 1),
  )

  const levels = Array.from({ length: levelCount }, (_, index) => {
    const price = Number((firstPrice + (index * priceStep * direction)).toFixed(decimals))
    return {
      price,
      qty: grouped.get(price) ?? 0,
    }
  })

  let cum = 0
  return levels.map((level) => {
    const quoteQty = level.price * level.qty
    cum += quoteQty
    return { price: level.price, qty: level.qty, quoteQty, sum: cum }
  })
}

function fmt(n: number, dp = 1): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })
}

function fmtCompact(n: number, dp = 2): string {
  const abs = Math.abs(n)
  if (abs >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(dp)}B`
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(dp)}M`
  if (abs >= 1_000) return `${(n / 1_000).toFixed(dp)}K`
  return fmt(n, dp)
}

function applyBookUpdates(book: Map<string, string>, updates: RawLevel[] = []): void {
  for (const [price, qty] of updates) {
    if (parseFloat(qty) <= 0) {
      book.delete(price)
      continue
    }
    book.set(price, qty)
  }
}

function sortBook(book: Map<string, string>, side: 'ask' | 'bid'): RawLevel[] {
  return Array.from(book.entries())
    .sort((left, right) => side === 'ask'
      ? parseFloat(left[0]) - parseFloat(right[0])
      : parseFloat(right[0]) - parseFloat(left[0]))
    .map(([price, qty]) => [price, qty])
}

export function OrderBook({ onPriceSelect }: { onPriceSelect?: (price: number) => void }) {
  const locale = useUiPreferencesStore((state) => state.locale)
  const orderBookDepthMode = useUiPreferencesStore((state) => state.orderBookDepthMode)
  const { t } = useTranslation(locale)
  const { symbol, currentPrice, setCurrentPrice } = useMarketStore()
  const { baseAsset, quoteAsset } = splitTradingSymbol(symbol)
  const [spreadStep, setSpreadStep] = useState<number>(SPREAD_OPTIONS[0].step)
  const [isSpreadMenuOpen, setIsSpreadMenuOpen] = useState(false)
  const [rawAsks, setRawAsks] = useState<RawLevel[]>([])
  const [rawBids, setRawBids] = useState<RawLevel[]>([])
  const spreadMenuRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const handlePointerDown = (event: MouseEvent) => {
      if (!spreadMenuRef.current?.contains(event.target as Node)) {
        setIsSpreadMenuOpen(false)
      }
    }

    document.addEventListener('mousedown', handlePointerDown)
    return () => document.removeEventListener('mousedown', handlePointerDown)
  }, [])

  useEffect(() => {
    const sym = symbol.toLowerCase()
    const wsUrl = `${FSTREAM_COMBINED_STREAM_BASE}${sym}@depth@100ms`

    let alive = true
    let ws: WebSocket | null = null
    let retryTimer: number | null = null
    let bufferedEvents: DepthEvent[] = []
    let lastAppliedUpdateId = 0
    let snapshotLoaded = false
    let snapshotRequestId = 0
    const asksBook = new Map<string, string>()
    const bidsBook = new Map<string, string>()

    const clearRetryTimer = () => {
      if (retryTimer != null) {
        window.clearTimeout(retryTimer)
        retryTimer = null
      }
    }

    const scheduleSnapshotRetry = () => {
      if (!alive || retryTimer != null) {
        return
      }
      retryTimer = window.setTimeout(() => {
        retryTimer = null
        if (alive) {
          void syncSnapshot()
        }
      }, 1000)
    }

    const publishOrderBook = () => {
      const nextAsks = sortBook(asksBook, 'ask')
      const nextBids = sortBook(bidsBook, 'bid')
      setRawAsks(nextAsks)
      setRawBids(nextBids)

      const bestAsk = nextAsks.length > 0 ? parseFloat(nextAsks[0][0]) : null
      const bestBid = nextBids.length > 0 ? parseFloat(nextBids[0][0]) : null
      if (bestAsk != null && bestBid != null) {
        setCurrentPrice(symbol, (bestAsk + bestBid) / 2)
      }
    }

    const applyDepthEvent = (event: DepthEvent): boolean => {
      if (event.u <= lastAppliedUpdateId) {
        return true
      }

      const expectedNextUpdateId = lastAppliedUpdateId + 1
      const bridgesExpectedRange = event.U <= expectedNextUpdateId && event.u >= expectedNextUpdateId
      const followsPreviousEvent = event.pu == null || event.pu === lastAppliedUpdateId
      if (!bridgesExpectedRange && !followsPreviousEvent) {
        return false
      }

      applyBookUpdates(bidsBook, event.b)
      applyBookUpdates(asksBook, event.a)
      lastAppliedUpdateId = event.u
      publishOrderBook()
      return true
    }

    const syncSnapshot = async () => {
      const requestId = ++snapshotRequestId
      snapshotLoaded = false
      lastAppliedUpdateId = 0
      bufferedEvents = []
      clearRetryTimer()

      try {
        const snapshot = await api.getOrderBookDepth(symbol.toUpperCase(), BOOK_SNAPSHOT_LIMIT)
        if (!alive || requestId !== snapshotRequestId) {
          return
        }

        asksBook.clear()
        bidsBook.clear()
        applyBookUpdates(bidsBook, snapshot.bids)
        applyBookUpdates(asksBook, snapshot.asks)
        lastAppliedUpdateId = snapshot.lastUpdateId ?? 0
        publishOrderBook()

        const pendingEvents = bufferedEvents
          .filter((event) => event.u > lastAppliedUpdateId)
          .sort((left, right) => left.U - right.U)

        const startIndex = pendingEvents.length === 0
          ? 0
          : pendingEvents.findIndex((event) => event.U <= lastAppliedUpdateId + 1 && event.u >= lastAppliedUpdateId + 1)

        if (pendingEvents.length > 0 && startIndex === -1) {
          scheduleSnapshotRetry()
          return
        }

        snapshotLoaded = true
        bufferedEvents = []

        for (const event of pendingEvents.slice(startIndex)) {
          if (!applyDepthEvent(event)) {
            scheduleSnapshotRetry()
            return
          }
        }
      } catch {
        scheduleSnapshotRetry()
      }
    }

    const connect = () => {
      ws = new WebSocket(wsUrl)

      ws.onopen = () => {
        bufferedEvents = []
        void syncSnapshot()
      }

      ws.onmessage = (event) => {
        if (!alive) {
          return
        }
        try {
          const payload = JSON.parse(event.data) as DepthEvent | { data?: DepthEvent }
          const data = (typeof payload === 'object' && payload !== null && 'data' in payload
            ? payload.data
            : payload) as DepthEvent | undefined
          if (!data) {
            return
          }
          if (!snapshotLoaded) {
            bufferedEvents.push(data)
            return
          }
          if (!applyDepthEvent(data)) {
            void syncSnapshot()
          }
        } catch {
          return
        }
      }

      ws.onerror = () => {}

      ws.onclose = () => {
        if (!alive) {
          return
        }
        clearRetryTimer()
        retryTimer = window.setTimeout(() => {
          retryTimer = null
          if (alive) {
            connect()
          }
        }, 3000)
      }
    }

    connect()

    return () => {
      alive = false
      clearRetryTimer()
      ws?.close()
    }
  }, [symbol, setCurrentPrice])

  const asks = [...buildLevels(rawAsks, 'ask', spreadStep)].reverse()
  const bids = buildLevels(rawBids, 'bid', spreadStep)
  const getDepthValue = (level: Level) => orderBookDepthMode === 'level' ? level.quoteQty : level.sum
  const maxSum = Math.max(
    ...asks.map(getDepthValue),
    ...bids.map(getDepthValue),
    0.001,
  )

  const spread = asks.length && bids.length
    ? asks[asks.length - 1].price - bids[0].price
    : null

  const spreadPct = spread != null && bids.length
    ? ((spread / bids[0].price) * 100).toFixed(3)
    : null

  const priceDp = getPriceDecimals(spreadStep)
  const selectedSpreadOption = SPREAD_OPTIONS.find((option) => option.step === spreadStep) ?? SPREAD_OPTIONS[0]

  return (
    <div className="h-full flex flex-col bg-[#161616] text-[11px] select-none min-w-0">
      <div className="px-2 py-1.5 border-b border-[#3e3e42] shrink-0 flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold text-[#cccccc]">{t('orderbook.title')}</span>
        <div ref={spreadMenuRef} className="relative">
          <button
            type="button"
            onClick={() => setIsSpreadMenuOpen((current) => !current)}
            className="flex min-w-[66px] items-center justify-between rounded-sm border border-[#2f333a] bg-[#1f2125] px-2 py-[3px] font-mono text-[12px] text-[#e6e9ef] shadow-[inset_0_1px_0_rgba(255,255,255,0.02)] hover:border-[#464c56]"
            aria-haspopup="listbox"
            aria-expanded={isSpreadMenuOpen}
            aria-label={t('orderbook.spread')}
          >
            <span>{selectedSpreadOption.displayValue}</span>
            <span className={`ml-2 text-[10px] text-[#c9ced8] transition-transform ${isSpreadMenuOpen ? 'rotate-180' : ''}`}>▼</span>
          </button>

          {isSpreadMenuOpen && (
            <div className="absolute right-0 top-[calc(100%+6px)] z-20 min-w-[88px] overflow-hidden rounded-md border border-[#2f333a] bg-[#1f2125] py-1 shadow-[0_10px_30px_rgba(0,0,0,0.45)]">
              {SPREAD_OPTIONS.map((option) => {
                const active = option.step === spreadStep
                return (
                  <button
                    key={option.step}
                    type="button"
                    onClick={() => {
                      setSpreadStep(option.step)
                      setIsSpreadMenuOpen(false)
                    }}
                    className={`flex w-full items-center justify-between px-3 py-2 font-mono text-[12px] ${active ? 'text-[#f3f5f8]' : 'text-[#aeb5c2] hover:bg-[#2a2d33] hover:text-[#f3f5f8]'}`}
                    role="option"
                    aria-selected={active}
                  >
                    <span>{option.displayValue}</span>
                    <span className={`ml-3 text-[#f3f5f8] ${active ? 'opacity-100' : 'opacity-0'}`}>✓</span>
                  </button>
                )
              })}
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 px-2 py-1 text-[9px] text-[#555] uppercase tracking-wider shrink-0 border-b border-[#2a2a2a]">
        <span>{t('orderbook.price')}({quoteAsset})</span>
        <span className="text-right">{t('orderbook.size')}({quoteAsset})</span>
        <span className="text-right">{t('orderbook.sum')}({quoteAsset})</span>
      </div>

      <div className="flex-1 overflow-hidden flex flex-col justify-end">
        {asks.map((level, index) => {
          const barWidth = (getDepthValue(level) / maxSum) * 100
          return (
            <button
              key={`ask-${level.price}-${index}`}
              type="button"
              onClick={() => onPriceSelect?.(level.price)}
              className="relative grid grid-cols-3 w-full px-2 py-[1px] hover:bg-[#252526] cursor-pointer text-left"
            >
              <div
                className="absolute right-0 top-0 h-full bg-[#f6465d]/10"
                style={{ width: `${barWidth}%` }}
              />
              <span className="text-[#f6465d] font-mono tabular-nums z-10">{fmt(level.price, priceDp)}</span>
              <span className="text-right text-[#aaa] font-mono tabular-nums z-10">{fmtCompact(level.quoteQty, 2)}</span>
              <span className="text-right text-[#666] font-mono tabular-nums z-10">{fmtCompact(level.sum, 2)}</span>
            </button>
          )
        })}
      </div>

      <div className="px-2 py-1 bg-[#1a1a1a] border-y border-[#3e3e42] shrink-0 flex items-center justify-between">
        <span className="text-[#cccccc] font-bold font-mono tabular-nums text-[12px]">
          {currentPrice != null ? fmt(currentPrice, 1) : '—'}
        </span>
        {spread != null && (
          <span className="text-[9px] text-[#555]">
            {t('orderbook.spread')} {fmt(spread, 1)} ({spreadPct}%)
          </span>
        )}
      </div>

      <div className="flex-1 overflow-hidden flex flex-col">
        {bids.map((level, index) => {
          const barWidth = (getDepthValue(level) / maxSum) * 100
          return (
            <button
              key={`bid-${level.price}-${index}`}
              type="button"
              onClick={() => onPriceSelect?.(level.price)}
              className="relative grid grid-cols-3 w-full px-2 py-[1px] hover:bg-[#252526] cursor-pointer text-left"
            >
              <div
                className="absolute right-0 top-0 h-full bg-[#0ecb81]/10"
                style={{ width: `${barWidth}%` }}
              />
              <span className="text-[#0ecb81] font-mono tabular-nums z-10">{fmt(level.price, priceDp)}</span>
              <span className="text-right text-[#aaa] font-mono tabular-nums z-10">{fmtCompact(level.quoteQty, 2)}</span>
              <span className="text-right text-[#666] font-mono tabular-nums z-10">{fmtCompact(level.sum, 2)}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
