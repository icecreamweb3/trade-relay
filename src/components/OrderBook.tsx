/**
 * OrderBook — Binance Futures real-time depth (asks/bids)
 * Connects directly to Binance futures WS from the renderer process.
 */
import { useEffect, useRef, useState } from 'react'
import { useMarketStore } from '../store/marketStore'
import { Locale, useTranslation } from '../i18n/translations'

const UI_LANG = (window as unknown as { electronAPI?: { uiLang?: string } }).electronAPI?.uiLang
const locale: Locale = (UI_LANG === 'en' ? 'en' : 'zh-CN')

interface Level { price: number; qty: number; sum: number }

const QUOTE_ASSETS = ['USDT', 'USDC', 'FDUSD', 'BUSD', 'BTC', 'ETH'] as const

function splitTradingSymbol(symbol: string) {
  const upperSymbol = symbol.toUpperCase()
  for (const quoteAsset of QUOTE_ASSETS) {
    if (upperSymbol.endsWith(quoteAsset) && upperSymbol.length > quoteAsset.length) {
      return { baseAsset: upperSymbol.slice(0, -quoteAsset.length), quoteAsset }
    }
  }
  return { baseAsset: upperSymbol, quoteAsset: 'USDT' }
}

function buildLevels(raw: [string, string][], side: 'ask' | 'bid'): Level[] {
  const sorted = raw
    .map(([p, q]) => ({ price: parseFloat(p), qty: parseFloat(q) }))
    .filter(l => l.qty > 0)
    .sort((a, b) => side === 'ask' ? a.price - b.price : b.price - a.price)
    .slice(0, 19)

  let cum = 0
  return sorted.map(l => {
    cum += l.qty
    return { price: l.price, qty: l.qty, sum: cum }
  })
}

function fmt(n: number, dp = 1): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })
}

export function OrderBook({ onPriceSelect }: { onPriceSelect?: (price: number) => void }) {
  const { t } = useTranslation(locale)
  const { symbol, currentPrice, setCurrentPrice } = useMarketStore()
  const { baseAsset, quoteAsset } = splitTradingSymbol(symbol)
  const [asks, setAsks] = useState<Level[]>([])
  const [bids, setBids] = useState<Level[]>([])
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    const sym = symbol.toLowerCase()
    const url = `wss://fstream.binance.com/ws/${sym}@depth20@100ms`

    let alive = true
    let ws: WebSocket

    function connect() {
      ws = new WebSocket(url)
      wsRef.current = ws

      ws.onmessage = (e) => {
        if (!alive) return
        try {
          const data = JSON.parse(e.data)
          const newAsks = buildLevels(data.a ?? [], 'ask')
          const newBids = buildLevels(data.b ?? [], 'bid')
          if (newAsks.length > 0 && newBids.length > 0) {
            setCurrentPrice(symbol, (newAsks[0].price + newBids[0].price) / 2)
          }
          // For display: asks reversed so highest ask is at top, bids start from highest
          setAsks([...newAsks].reverse())
          setBids(newBids)
        } catch {}
      }
      ws.onerror = () => {}
      ws.onclose = () => {
        if (alive) setTimeout(() => { if (alive) connect() }, 3000)
      }
    }
    connect()

    return () => {
      alive = false
      ws?.close()
    }
  }, [symbol, setCurrentPrice])

  const maxSum = Math.max(
    asks[asks.length - 1]?.sum ?? 0,
    bids[0]?.sum ?? 0,
    0.001,
  )

  const spread = asks.length && bids.length
    ? asks[asks.length - 1].price - bids[0].price
    : null

  const spreadPct = spread != null && bids.length
    ? ((spread / bids[0].price) * 100).toFixed(3)
    : null

  return (
    <div className="h-full flex flex-col bg-[#161616] text-[11px] select-none min-w-0">
      {/* Header */}
      <div className="px-2 py-1.5 border-b border-[#3e3e42] shrink-0">
        <span className="text-[11px] font-semibold text-[#cccccc]">{t('orderbook.title')}</span>
      </div>

      {/* Column labels */}
      <div className="grid grid-cols-3 px-2 py-1 text-[9px] text-[#555] uppercase tracking-wider shrink-0 border-b border-[#2a2a2a]">
        <span>{t('orderbook.price')}({quoteAsset})</span>
        <span className="text-right">{t('orderbook.size')}({baseAsset})</span>
        <span className="text-right">{t('orderbook.sum')}({baseAsset})</span>
      </div>

      {/* Asks (sells) — red, highest at top, lowest ask at bottom near spread */}
      <div className="flex-1 overflow-hidden flex flex-col justify-end">
        {asks.map((lvl, i) => {
          const barW = (lvl.sum / maxSum) * 100
          return (
            <button
              key={i}
              type="button"
              onClick={() => onPriceSelect?.(lvl.price)}
              className="relative grid grid-cols-3 w-full px-2 py-[1px] hover:bg-[#252526] cursor-pointer text-left"
            >
              <div
                className="absolute right-0 top-0 h-full bg-[#f6465d]/10"
                style={{ width: `${barW}%` }}
              />
              <span className="text-[#f6465d] font-mono tabular-nums z-10">{fmt(lvl.price, 1)}</span>
              <span className="text-right text-[#aaa] font-mono tabular-nums z-10">{fmt(lvl.qty, 4)}</span>
              <span className="text-right text-[#666] font-mono tabular-nums z-10">{fmt(lvl.sum, 2)}</span>
            </button>
          )
        })}
      </div>

      {/* Spread row */}
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

      {/* Bids (buys) — green */}
      <div className="flex-1 overflow-hidden flex flex-col">
        {bids.map((lvl, i) => {
          const barW = (lvl.sum / maxSum) * 100
          return (
            <button
              key={i}
              type="button"
              onClick={() => onPriceSelect?.(lvl.price)}
              className="relative grid grid-cols-3 w-full px-2 py-[1px] hover:bg-[#252526] cursor-pointer text-left"
            >
              <div
                className="absolute right-0 top-0 h-full bg-[#0ecb81]/10"
                style={{ width: `${barW}%` }}
              />
              <span className="text-[#0ecb81] font-mono tabular-nums z-10">{fmt(lvl.price, 1)}</span>
              <span className="text-right text-[#aaa] font-mono tabular-nums z-10">{fmt(lvl.qty, 4)}</span>
              <span className="text-right text-[#666] font-mono tabular-nums z-10">{fmt(lvl.sum, 2)}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
