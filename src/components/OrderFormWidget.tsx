import { useState, useEffect, useMemo } from 'react'
import { api } from '../api/client'
import { useMarketStore } from '../store/marketStore'

type Side = 'BUY' | 'SELL'
type OrderType = 'LIMIT' | 'MARKET' | 'STOP'
type MarginType = 'CROSS' | 'ISOLATED'
type PositionDir = 'OPEN' | 'CLOSE'

// ── helpers ──────────────────────────────────────────────────────────────────

function fmt(n: number | null | undefined, decimals = 2): string {
  if (n == null || isNaN(n)) return '—'
  return n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

function fmtRate(r: number | null): string {
  if (r == null) return '—'
  return (r * 100).toFixed(4) + '%'
}

function nextFundingSeconds(): number {
  const now = Math.floor(Date.now() / 1000)
  const period = 8 * 3600
  return period - (now % period)
}

function fmtCountdown(s: number): string {
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
}

// ── Ticker strip ─────────────────────────────────────────────────────────────

function TickerStrip() {
  const { symbol, currentPrice, markPrice, fundingRate, klines } = useMarketStore()
  const [countdown, setCountdown] = useState(nextFundingSeconds())

  useEffect(() => {
    const t = setInterval(() => setCountdown(nextFundingSeconds()), 1000)
    return () => clearInterval(t)
  }, [])

  const change24h = useMemo(() => {
    if (klines.length < 2 || currentPrice == null) return null
    const open24 = klines[0].close
    return ((currentPrice - open24) / open24) * 100
  }, [klines, currentPrice])

  const isUp = change24h == null ? null : change24h >= 0
  const priceColor = isUp == null ? 'text-[#cccccc]' : isUp ? 'text-[#0ecb81]' : 'text-[#f6465d]'

  return (
    <div className="px-3 py-2.5 border-b border-[#3e3e42] bg-[#161616] shrink-0 select-none">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[13px] font-bold text-[#cccccc] tracking-wide">{symbol}</span>
        <span className="text-[9px] text-[#555] bg-[#252526] px-1.5 py-0.5 rounded uppercase tracking-wider">Perpetual</span>
      </div>
      <div className={`text-[22px] font-bold leading-none mb-2 tabular-nums ${priceColor}`}>
        {currentPrice != null ? fmt(currentPrice, currentPrice > 1000 ? 2 : 4) : '—'}
        {change24h != null && (
          <span className="text-[11px] font-normal ml-2 opacity-90">
            {isUp ? '+' : ''}{change24h.toFixed(2)}%
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-3">
        <div>
          <div className="text-[9px] text-[#444] uppercase tracking-wider mb-0.5">Mark Price</div>
          <div className="text-[11px] text-[#999] font-mono tabular-nums">{markPrice != null ? fmt(markPrice, 2) : '—'}</div>
        </div>
        <div>
          <div className="text-[9px] text-[#444] uppercase tracking-wider mb-0.5">Funding / Countdown</div>
          <div className="text-[11px] font-mono tabular-nums">
            <span className={fundingRate != null && fundingRate >= 0 ? 'text-[#0ecb81]' : 'text-[#f6465d]'}>
              {fmtRate(fundingRate)}
            </span>
            <span className="text-[#444] mx-1">·</span>
            <span className="text-[#666]">{fmtCountdown(countdown)}</span>
          </div>
        </div>
      </div>
    </div>
  )
}

export function OrderFormWidget({ onOrderPlaced }: { onOrderPlaced?: () => void }) {
  const { symbol, currentPrice, markPrice } = useMarketStore()

  const [side, setSide] = useState<Side>('BUY')
  const [marginType, setMarginType] = useState<MarginType>('CROSS')
  const [posDir, setPosDir] = useState<PositionDir>('OPEN')
  const [orderType, setOrderType] = useState<OrderType>('LIMIT')
  const [qty, setQty] = useState('')
  const [price, setPrice] = useState('')
  const [stopPrice, setStopPrice] = useState('')
  const [tp, setTp] = useState('')
  const [sl, setSl] = useState('')
  const [showTpSl, setShowTpSl] = useState(false)
  const [leverage, setLeverage] = useState(10)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null)

  const fillMarkPrice = () => {
    const ref = markPrice ?? currentPrice
    if (ref != null) setPrice(ref.toFixed(2))
  }

  const estimatedCost = useMemo(() => {
    const qtyNum = parseFloat(qty)
    const priceNum = orderType === 'MARKET' ? (currentPrice ?? 0) : parseFloat(price)
    if (!qtyNum || !priceNum) return null
    return (qtyNum * priceNum) / leverage
  }, [qty, price, orderType, currentPrice, leverage])

  const fillPct = (pct: number) => {
    const refPrice = orderType === 'MARKET'
      ? (currentPrice ?? 0)
      : (parseFloat(price) || (currentPrice ?? 0))
    if (!refPrice) return
    const availableUsdt = 1000
    const maxQty = (availableUsdt * leverage) / refPrice
    setQty((maxQty * pct).toFixed(4))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const qtyNum = parseFloat(qty)
    if (!qtyNum || qtyNum <= 0) { setResult({ ok: false, msg: 'Invalid quantity' }); return }
    setIsSubmitting(true)
    setResult(null)
    try {
      const body: Parameters<typeof api.submitOrder>[0] = {
        symbol, side,
        order_type: orderType === 'STOP' ? 'STOP_MARKET' : orderType,
        quantity: qtyNum,
        margin_type: marginType,
        position_direction: posDir,
      }
      if ((orderType === 'LIMIT' || orderType === 'STOP') && price) body.price = parseFloat(price)
      if (orderType === 'STOP' && stopPrice) body.stop_price = parseFloat(stopPrice)
      if (showTpSl && tp) body.tp_price = parseFloat(tp)
      if (showTpSl && sl) body.sl_price = parseFloat(sl)

      await api.submitOrder(body)
      setResult({ ok: true, msg: 'Order placed successfully' })
      setQty('')
      onOrderPlaced?.()
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (err as { message?: string })?.message || 'Failed'
      setResult({ ok: false, msg })
    } finally {
      setIsSubmitting(false)
    }
  }

  const isBuy = side === 'BUY'

  return (
    <div className="h-full flex flex-col bg-[#1a1a1a] overflow-hidden">

      {/* ── Ticker strip ── */}
      <TickerStrip />

      {/* ── BUY / SELL tabs ── */}
      <div className="grid grid-cols-2 shrink-0">
        <button onClick={() => setSide('BUY')}
          className={`py-2.5 text-[13px] font-bold tracking-wide transition-colors ${
            isBuy ? 'bg-[#0ecb81] text-white' : 'bg-[#1a2a22] text-[#0ecb81] hover:bg-[#1f3028]'
          }`}>
          Buy / Long
        </button>
        <button onClick={() => setSide('SELL')}
          className={`py-2.5 text-[13px] font-bold tracking-wide transition-colors ${
            !isBuy ? 'bg-[#f6465d] text-white' : 'bg-[#2a1a1e] text-[#f6465d] hover:bg-[#301f24]'
          }`}>
          Sell / Short
        </button>
      </div>

      {/* ── Controls row ── */}
      <div className="flex items-center gap-1 px-3 pt-2.5 shrink-0 flex-wrap">
        {/* Margin type */}
        <div className="flex rounded overflow-hidden border border-[#3e3e42]">
          {(['CROSS', 'ISOLATED'] as MarginType[]).map(m => (
            <button key={m} onClick={() => setMarginType(m)}
              className={`px-2 py-1 text-[10px] font-medium transition-colors ${
                marginType === m ? 'bg-[#2d2d30] text-[#cccccc]' : 'text-[#555] hover:text-[#888]'
              }`}>
              {m === 'CROSS' ? 'Cross' : 'Isolated'}
            </button>
          ))}
        </div>
        {/* Open / Close */}
        <div className="flex rounded overflow-hidden border border-[#3e3e42]">
          {(['OPEN', 'CLOSE'] as PositionDir[]).map(d => (
            <button key={d} onClick={() => setPosDir(d)}
              className={`px-2 py-1 text-[10px] font-medium transition-colors ${
                posDir === d ? 'bg-[#2d2d30] text-[#cccccc]' : 'text-[#555] hover:text-[#888]'
              }`}>
              {d}
            </button>
          ))}
        </div>
        {/* Leverage */}
        <div className="ml-auto flex items-center gap-1.5">
          <span className="text-[10px] text-[#444]">Lev</span>
          <select value={leverage} onChange={e => setLeverage(Number(e.target.value))}
            className="bg-[#252526] border border-[#3e3e42] text-[#cccccc] text-[11px] rounded px-1.5 py-0.5 outline-none cursor-pointer">
            {[1,2,3,5,10,20,25,50,75,100,125].map(l => (
              <option key={l} value={l}>{l}×</option>
            ))}
          </select>
        </div>
      </div>

      {/* ── Order type tabs ── */}
      <div className="flex gap-0 px-3 pt-2 shrink-0">
        {(['LIMIT', 'MARKET', 'STOP'] as OrderType[]).map(t => (
          <button key={t} onClick={() => setOrderType(t)}
            className={`py-1 px-2.5 text-[11px] rounded transition-colors mr-1 ${
              orderType === t
                ? 'bg-[#2d2d30] text-[#cccccc] border border-[#5a5a5e]'
                : 'text-[#555] hover:text-[#888] border border-transparent'
            }`}>
            {t}
          </button>
        ))}
      </div>

      {/* ── Form ── */}
      <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto px-3 pt-3 space-y-2.5 min-h-0">

        {/* Stop trigger price */}
        {orderType === 'STOP' && (
          <div>
            <label className="block text-[10px] text-[#555] uppercase tracking-wider mb-1">Trigger Price</label>
            <input type="number" value={stopPrice} onChange={e => setStopPrice(e.target.value)}
              placeholder="0.00" min="0" step="any"
              className="w-full bg-[#252526] border border-[#3e3e42] focus:border-[#007acc] text-[13px] text-[#cccccc] rounded px-2.5 py-1.5 outline-none selectable" />
          </div>
        )}

        {/* Limit / stop limit price */}
        {(orderType === 'LIMIT' || orderType === 'STOP') && (
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-[10px] text-[#555] uppercase tracking-wider">
                {orderType === 'STOP' ? 'Limit Price' : 'Price'}
              </label>
              <button type="button" onClick={fillMarkPrice}
                className="text-[9px] text-[#007acc] hover:text-[#1a9fff] transition-colors">
                Mark ↓
              </button>
            </div>
            <div className="relative">
              <input type="number" value={price} onChange={e => setPrice(e.target.value)}
                placeholder="0.00" min="0" step="any"
                className="w-full bg-[#252526] border border-[#3e3e42] focus:border-[#007acc] text-[13px] text-[#cccccc] rounded px-2.5 py-1.5 outline-none selectable pr-14" />
              <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-[#555]">USDT</span>
            </div>
          </div>
        )}

        {/* Market price info */}
        {orderType === 'MARKET' && currentPrice != null && (
          <div className="flex items-center justify-between px-2 py-1.5 bg-[#252526] rounded text-[11px]">
            <span className="text-[#555]">Market Price</span>
            <span className="text-[#cccccc] font-mono tabular-nums">{fmt(currentPrice, 2)}</span>
          </div>
        )}

        {/* Amount */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-[10px] text-[#555] uppercase tracking-wider">Amount</label>
            <span className="text-[9px] text-[#555]">{symbol.replace('USDT', '')}</span>
          </div>
          <input type="number" value={qty} onChange={e => setQty(e.target.value)}
            placeholder="0.0000" min="0" step="any"
            className="w-full bg-[#252526] border border-[#3e3e42] focus:border-[#007acc] text-[13px] text-[#cccccc] rounded px-2.5 py-1.5 outline-none selectable" />
        </div>

        {/* Percentage quick fill */}
        <div className="grid grid-cols-4 gap-1">
          {[0.25, 0.5, 0.75, 1.0].map(pct => (
            <button key={pct} type="button" onClick={() => fillPct(pct)}
              className="py-1 text-[10px] rounded bg-[#252526] text-[#666] hover:bg-[#2d2d30] hover:text-[#aaa] transition-colors border border-[#3e3e42]">
              {pct * 100}%
            </button>
          ))}
        </div>

        {/* Estimated margin cost */}
        {estimatedCost != null && (
          <div className="flex items-center justify-between px-2.5 py-1.5 bg-[#1e1e1e] border border-[#3e3e42] rounded">
            <span className="text-[10px] text-[#555]">Est. Margin ({leverage}×)</span>
            <span className="text-[11px] text-[#aaa] font-mono tabular-nums">{fmt(estimatedCost, 2)} USDT</span>
          </div>
        )}

        {/* TP / SL */}
        <div>
          <button type="button" onClick={() => setShowTpSl(v => !v)}
            className={`flex items-center gap-1.5 text-[10px] transition-colors ${showTpSl ? 'text-[#007acc]' : 'text-[#555] hover:text-[#888]'}`}>
            <span className={`flex items-center justify-center w-3 h-3 rounded-sm border text-[8px] leading-none ${showTpSl ? 'bg-[#007acc] border-[#007acc] text-white' : 'border-[#555]'}`}>
              {showTpSl ? '✓' : ''}
            </span>
            TP / SL
          </button>
        </div>

        {showTpSl && (
          <div className="space-y-2">
            <div>
              <label className="block text-[10px] text-[#555] uppercase tracking-wider mb-1">Take Profit</label>
              <div className="relative">
                <input type="number" value={tp} onChange={e => setTp(e.target.value)}
                  placeholder="0.00" min="0" step="any"
                  className="w-full bg-[#252526] border border-[#3e3e42] focus:border-[#0ecb81] text-[13px] text-[#cccccc] rounded px-2.5 py-1.5 outline-none selectable pr-10" />
                <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-[#0ecb81]">TP</span>
              </div>
            </div>
            <div>
              <label className="block text-[10px] text-[#555] uppercase tracking-wider mb-1">Stop Loss</label>
              <div className="relative">
                <input type="number" value={sl} onChange={e => setSl(e.target.value)}
                  placeholder="0.00" min="0" step="any"
                  className="w-full bg-[#252526] border border-[#3e3e42] focus:border-[#f6465d] text-[13px] text-[#cccccc] rounded px-2.5 py-1.5 outline-none selectable pr-10" />
                <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-[#f6465d]">SL</span>
              </div>
            </div>
          </div>
        )}

        {/* Result flash */}
        {result && (
          <div className={`text-[11px] px-2.5 py-1.5 rounded flex items-center gap-2 ${
            result.ok
              ? 'bg-[#0ecb81]/10 text-[#0ecb81] border border-[#0ecb81]/20'
              : 'bg-[#f6465d]/10 text-[#f6465d] border border-[#f6465d]/20'
          }`}>
            <span className="font-bold">{result.ok ? '✓' : '✗'}</span>
            {result.msg}
          </div>
        )}

        {/* Submit */}
        <button type="submit" disabled={isSubmitting || !qty}
          className={`w-full py-2.5 text-[13px] font-bold rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
            isBuy
              ? 'bg-[#0ecb81] hover:bg-[#0ab36d] text-white'
              : 'bg-[#f6465d] hover:bg-[#d93a4e] text-white'
          }`}>
          {isSubmitting
            ? 'Placing...'
            : `${isBuy ? 'Buy / Long' : 'Sell / Short'} ${symbol.replace('USDT', '')}`}
        </button>

        {/* Position info */}
        <div className="grid grid-cols-2 gap-x-2 pt-1">
          {/* Long side */}
          <div className="space-y-1">
            <div className="flex justify-between text-[9px]">
              <span className="text-[#555]">Liq. Price</span>
              <span className="text-[#0ecb81]">— USDC</span>
            </div>
            <div className="flex justify-between text-[9px]">
              <span className="text-[#555]">Margin</span>
              <span className="text-[#aaa]">— USDC</span>
            </div>
            <div className="flex justify-between text-[9px]">
              <span className="text-[#555]">Avail. Open</span>
              <span className="text-[#aaa]">— USDC</span>
            </div>
          </div>
          {/* Short side */}
          <div className="space-y-1">
            <div className="flex justify-between text-[9px]">
              <span className="text-[#555]">Liq. Price</span>
              <span className="text-[#f6465d]">— USDC</span>
            </div>
            <div className="flex justify-between text-[9px]">
              <span className="text-[#555]">Margin</span>
              <span className="text-[#aaa]">— USDC</span>
            </div>
            <div className="flex justify-between text-[9px]">
              <span className="text-[#555]">Avail. Open</span>
              <span className="text-[#aaa]">— USDC</span>
            </div>
          </div>
        </div>

        {/* ── Account section ── */}
        <div className="border-t border-[#3e3e42] pt-2 -mx-3 px-3">
          <div className="py-1 mb-1">
            <span className="text-[10px] font-semibold text-[#888] uppercase tracking-wider">Account</span>
          </div>
          <div className="space-y-1 pb-2">
            {[
              ['Margin Ratio',    '—'],
              ['Risk Rate',       '—'],
              ['Maint. Margin',   '—'],
              ['Total Equity',    '—'],
              ['Position Value',  '—'],
              ['Actual Leverage', '—'],
              ['Unrealized PnL',  '—'],
              ['Wallet Balance',  '—'],
            ].map(([k, v]) => (
              <div key={k} className="flex items-center justify-between">
                <span className="text-[10px] text-[#555]">{k}</span>
                <span className="text-[10px] text-[#666] font-mono">{v}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="h-2" />
      </form>
    </div>
  )
}
