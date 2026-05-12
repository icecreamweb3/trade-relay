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

function nextFundingSeconds(nextFundingTime: number | null): number {
  if (nextFundingTime != null && nextFundingTime > Date.now()) {
    return Math.floor((nextFundingTime - Date.now()) / 1000)
  }
  // fallback: local 8h cycle estimate
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
  const { symbol, currentPrice, markPrice, fundingRate, nextFundingTime, klines } = useMarketStore()
  const [countdown, setCountdown] = useState(() => nextFundingSeconds(nextFundingTime))

  useEffect(() => {
    setCountdown(nextFundingSeconds(nextFundingTime))
  }, [nextFundingTime])

  useEffect(() => {
    const t = setInterval(() => setCountdown(nextFundingSeconds(nextFundingTime)), 1000)
    return () => clearInterval(t)
  }, [nextFundingTime])

  const change24h = useMemo(() => {
    if (klines.length < 2 || currentPrice == null) return null
    const open24 = klines[0].close
    return ((currentPrice - open24) / open24) * 100
  }, [klines, currentPrice])

  const isUp = change24h == null ? null : change24h >= 0
  const priceColor = isUp == null ? 'text-[#EAECEF]' : isUp ? 'text-[#0ECB81]' : 'text-[#F6465D]'

  return (
    <div className="px-3 py-2.5 border-b border-[#2B2F36] bg-[#0B0E11] shrink-0 select-none">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[13px] font-bold text-[#EAECEF] tracking-wide">{symbol}</span>
        <span className="text-[9px] text-[#848E9C] bg-[#1E2026] px-1.5 py-0.5 rounded uppercase tracking-wider">Perpetual</span>
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
          <div className="text-[9px] text-[#848E9C] uppercase tracking-wider mb-0.5">Mark Price</div>
          <div className="text-[11px] text-[#EAECEF] font-mono tabular-nums">{markPrice != null ? fmt(markPrice, 2) : '—'}</div>
        </div>
        <div>
          <div className="text-[9px] text-[#848E9C] uppercase tracking-wider mb-0.5">Funding / Countdown</div>
          <div className="text-[11px] font-mono tabular-nums">
            <span className={fundingRate != null && fundingRate >= 0 ? 'text-[#0ECB81]' : 'text-[#F6465D]'}>
              {fmtRate(fundingRate)}
            </span>
            <span className="text-[#848E9C] mx-1">·</span>
            <span className="text-[#848E9C]">{fmtCountdown(countdown)}</span>
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
  const [sizeUnit, setSizeUnit] = useState<'USDT' | 'BASE'>('USDT')

  // Derived: base asset ticker (e.g. "BTC" from "BTCUSDT")
  const baseTicker = symbol.replace('USDT', '')

  const fillMarkPrice = () => {
    const ref = markPrice ?? currentPrice
    if (ref != null) setPrice(ref.toFixed(2))
  }

  const estimatedCost = useMemo(() => {
    const qtyNum = parseFloat(qty)
    const priceNum = orderType === 'MARKET' ? (currentPrice ?? 0) : parseFloat(price)
    if (!qtyNum || !priceNum) return null
    // qty is in USDT → convert to base first
    const baseQty = sizeUnit === 'USDT' ? qtyNum / priceNum : qtyNum
    return (baseQty * priceNum) / leverage
  }, [qty, price, orderType, currentPrice, leverage, sizeUnit])

  const fillPct = (pct: number) => {
    const refPrice = orderType === 'MARKET'
      ? (currentPrice ?? 0)
      : (parseFloat(price) || (currentPrice ?? 0))
    if (!refPrice) return
    const availableUsdt = 1000
    const maxBaseQty = (availableUsdt * leverage) / refPrice
    if (sizeUnit === 'USDT') {
      setQty((maxBaseQty * refPrice * pct).toFixed(2))
    } else {
      setQty((maxBaseQty * pct).toFixed(4))
    }
  }

  const handleSubmit = async (submitSide: Side) => {
    const qtyNum = parseFloat(qty)
    if (!qtyNum || qtyNum <= 0) { setResult({ ok: false, msg: 'Invalid quantity' }); return }

    // API 始终接收 base 数量（BTC）；若用户以 USDT 输入则按参考价格换算
    let baseQty = qtyNum
    if (sizeUnit === 'USDT') {
      const refPrice = orderType === 'MARKET'
        ? (currentPrice ?? 0)
        : (parseFloat(price) || (currentPrice ?? 0))
      if (!refPrice) { setResult({ ok: false, msg: 'Price unavailable for USDT conversion' }); return }
      baseQty = qtyNum / refPrice
    }

    setIsSubmitting(true)
    setResult(null)
    try {
      const body: Parameters<typeof api.submitOrder>[0] = {
        symbol, side: submitSide,
        order_type: orderType === 'STOP' ? 'STOP_MARKET' : orderType,
        quantity: baseQty,
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

  return (
    <div className="h-full flex flex-col bg-[#0B0E11] overflow-hidden">

      {/* ── Ticker strip ── */}
      <TickerStrip />

      {/* ── Open / Close tabs ── */}
      <div className="grid grid-cols-2 shrink-0 border-b border-[#2B2F36]">
        <button onClick={() => setPosDir('OPEN')}
          className={`py-2.5 text-[13px] font-bold tracking-wide transition-colors ${
            posDir === 'OPEN' ? 'text-[#EAECEF] border-b-2 border-[#F0B90B]' : 'text-[#848E9C] hover:text-[#EAECEF]'
          }`}>
          Open
        </button>
        <button onClick={() => setPosDir('CLOSE')}
          className={`py-2.5 text-[13px] font-bold tracking-wide transition-colors ${
            posDir === 'CLOSE' ? 'text-[#EAECEF] border-b-2 border-[#F0B90B]' : 'text-[#848E9C] hover:text-[#EAECEF]'
          }`}>
          Close
        </button>
      </div>

      {/* ── Controls row ── */}
      <div className="flex items-center gap-1 px-3 pt-2.5 shrink-0 flex-wrap">
        {/* Margin type */}
        <div className="flex rounded overflow-hidden border border-[#2B2F36]">
          {(['CROSS', 'ISOLATED'] as MarginType[]).map(m => (
            <button key={m} onClick={() => setMarginType(m)}
              className={`px-2 py-1 text-[10px] font-medium transition-colors ${
                marginType === m ? 'bg-[#1E2026] text-[#EAECEF]' : 'text-[#848E9C] hover:text-[#EAECEF]'
              }`}>
              {m === 'CROSS' ? 'Cross' : 'Isolated'}
            </button>
          ))}
        </div>
        {/* Leverage */}
        <div className="ml-auto flex items-center gap-1.5">
          <span className="text-[10px] text-[#848E9C]">Lev</span>
          <select value={leverage} onChange={e => setLeverage(Number(e.target.value))}
            className="bg-[#1E2026] border border-[#2B2F36] text-[#EAECEF] text-[11px] rounded px-1.5 py-0.5 outline-none cursor-pointer">
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
                ? 'text-[#EAECEF] border-b border-[#F0B90B]'
                : 'text-[#848E9C] hover:text-[#EAECEF] border border-transparent'
            }`}>
            {t}
          </button>
        ))}
      </div>

      {/* ── Form ── */}
      <form onSubmit={e => e.preventDefault()} className="flex-1 overflow-y-auto px-3 pt-3 space-y-2.5 min-h-0">

        {/* Stop trigger price */}
        {orderType === 'STOP' && (
          <div>
            <label className="block text-[10px] text-[#848E9C] uppercase tracking-wider mb-1">Trigger Price</label>
            <input type="number" value={stopPrice} onChange={e => setStopPrice(e.target.value)}
              placeholder="0.00" min="0" step="any"
              className="w-full bg-[#1E2026] border border-[#2B2F36] focus:border-[#F0B90B] text-[13px] text-[#EAECEF] rounded px-2.5 py-1.5 outline-none selectable" />
          </div>
        )}

        {/* Limit / stop limit price */}
        {(orderType === 'LIMIT' || orderType === 'STOP') && (
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-[10px] text-[#848E9C] uppercase tracking-wider">
                {orderType === 'STOP' ? 'Limit Price' : 'Price'}
              </label>
              <button type="button" onClick={fillMarkPrice}
                className="text-[9px] text-[#F0B90B] hover:text-[#D9A429] transition-colors">
                Mark ↓
              </button>
            </div>
            <div className="relative">
              <input type="number" value={price} onChange={e => setPrice(e.target.value)}
                placeholder="0.00" min="0" step="any"
                className="w-full bg-[#1E2026] border border-[#2B2F36] focus:border-[#F0B90B] text-[13px] text-[#EAECEF] rounded px-2.5 py-1.5 outline-none selectable pr-14" />
              <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-[#848E9C]">USDT</span>
            </div>
          </div>
        )}

        {/* Market price info */}
        {orderType === 'MARKET' && currentPrice != null && (
          <div className="flex items-center justify-between px-2 py-1.5 bg-[#1E2026] rounded text-[11px]">
            <span className="text-[#848E9C]">Market Price</span>
            <span className="text-[#EAECEF] font-mono tabular-nums">{fmt(currentPrice, 2)}</span>
          </div>
        )}

        {/* Size */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-[10px] text-[#848E9C] uppercase tracking-wider">Size</label>
          </div>
          <div className="flex">
            <input type="number" value={qty} onChange={e => setQty(e.target.value)}
              placeholder="0.0000" min="0" step="any"
              className="flex-1 min-w-0 bg-[#1E2026] border border-[#2B2F36] border-r-0 focus:border-[#F0B90B] text-[13px] text-[#EAECEF] rounded-l px-2.5 py-1.5 outline-none selectable" />
            <select
              value={sizeUnit}
              onChange={e => { setSizeUnit(e.target.value as 'USDT' | 'BASE'); setQty('') }}
              className="bg-[#2B2F36] border border-[#2B2F36] text-[#EAECEF] text-[11px] rounded-r px-2 py-1.5 outline-none cursor-pointer shrink-0">
              <option value="USDT">USDT</option>
              <option value="BASE">{baseTicker}</option>
            </select>
          </div>
        </div>

        {/* Percentage quick fill */}
        <div className="grid grid-cols-4 gap-1">
          {[0.25, 0.5, 0.75, 1.0].map(pct => (
            <button key={pct} type="button" onClick={() => fillPct(pct)}
              className="py-1 text-[10px] rounded bg-[#1E2026] text-[#848E9C] hover:bg-[#2B2F36] hover:text-[#EAECEF] transition-colors border border-[#2B2F36]">
              {pct * 100}%
            </button>
          ))}
        </div>

        {/* Estimated margin cost */}
        {estimatedCost != null && (
          <div className="flex items-center justify-between px-2.5 py-1.5 bg-[#161A1E] border border-[#2B2F36] rounded">
            <span className="text-[10px] text-[#848E9C]">Est. Margin ({leverage}×)</span>
            <span className="text-[11px] text-[#EAECEF] font-mono tabular-nums">{fmt(estimatedCost, 2)} USDT</span>
          </div>
        )}

        {/* TP / SL */}
        <div>
          <button type="button" onClick={() => setShowTpSl(v => !v)}
            className={`flex items-center gap-1.5 text-[10px] transition-colors ${showTpSl ? 'text-[#F0B90B]' : 'text-[#848E9C] hover:text-[#EAECEF]'}`}>
            <span className={`flex items-center justify-center w-3 h-3 rounded-sm border text-[8px] leading-none ${showTpSl ? 'bg-[#F0B90B] border-[#F0B90B] text-black' : 'border-[#848E9C]'}`}>
              {showTpSl ? '✓' : ''}
            </span>
            TP / SL
          </button>
        </div>

        {showTpSl && (
          <div className="space-y-2">
            <div>
              <label className="block text-[10px] text-[#848E9C] uppercase tracking-wider mb-1">Take Profit</label>
              <div className="relative">
                <input type="number" value={tp} onChange={e => setTp(e.target.value)}
                  placeholder="0.00" min="0" step="any"
                  className="w-full bg-[#1E2026] border border-[#2B2F36] focus:border-[#0ECB81] text-[13px] text-[#EAECEF] rounded px-2.5 py-1.5 outline-none selectable pr-10" />
                <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-[#0ECB81]">TP</span>
              </div>
            </div>
            <div>
              <label className="block text-[10px] text-[#848E9C] uppercase tracking-wider mb-1">Stop Loss</label>
              <div className="relative">
                <input type="number" value={sl} onChange={e => setSl(e.target.value)}
                  placeholder="0.00" min="0" step="any"
                  className="w-full bg-[#1E2026] border border-[#2B2F36] focus:border-[#F6465D] text-[13px] text-[#EAECEF] rounded px-2.5 py-1.5 outline-none selectable pr-10" />
                <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-[#F6465D]">SL</span>
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
        <div className="grid grid-cols-2 gap-2">
          {posDir === 'OPEN' ? (<>
            <button type="button" onClick={() => handleSubmit('BUY')} disabled={isSubmitting || !qty}
              className="py-2.5 text-[13px] font-bold rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed bg-[#0ecb81] hover:bg-[#0ab36d] text-white">
              {isSubmitting ? 'Placing...' : 'Open Long'}
            </button>
            <button type="button" onClick={() => handleSubmit('SELL')} disabled={isSubmitting || !qty}
              className="py-2.5 text-[13px] font-bold rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed bg-[#f6465d] hover:bg-[#d93a4e] text-white">
              {isSubmitting ? 'Placing...' : 'Open Short'}
            </button>
          </>) : (<>
            <button type="button" onClick={() => handleSubmit('SELL')} disabled={isSubmitting || !qty}
              className="py-2.5 text-[13px] font-bold rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed bg-[#0ecb81] hover:bg-[#0ab36d] text-white">
              {isSubmitting ? 'Placing...' : 'Close short'}
            </button>
            <button type="button" onClick={() => handleSubmit('BUY')} disabled={isSubmitting || !qty}
              className="py-2.5 text-[13px] font-bold rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed bg-[#f6465d] hover:bg-[#d93a4e] text-white">
              {isSubmitting ? 'Placing...' : 'Close long'}
            </button>
          </>)}
        </div>

        {/* Position info */}
        <div className="grid grid-cols-2 gap-x-2 pt-1">
          {/* Long side */}
          <div className="space-y-1">
            <div className="flex justify-between text-[9px]">
              <span className="text-[#848E9C]">Liq. Price</span>
              <span className="text-[#0ECB81]">— USDC</span>
            </div>
            <div className="flex justify-between text-[9px]">
              <span className="text-[#848E9C]">Margin</span>
              <span className="text-[#EAECEF]">— USDC</span>
            </div>
            <div className="flex justify-between text-[9px]">
              <span className="text-[#848E9C]">Avail. Open</span>
              <span className="text-[#EAECEF]">— USDC</span>
            </div>
          </div>
          {/* Short side */}
          <div className="space-y-1">
            <div className="flex justify-between text-[9px]">
              <span className="text-[#848E9C]">Liq. Price</span>
              <span className="text-[#F6465D]">— USDC</span>
            </div>
            <div className="flex justify-between text-[9px]">
              <span className="text-[#848E9C]">Margin</span>
              <span className="text-[#EAECEF]">— USDC</span>
            </div>
            <div className="flex justify-between text-[9px]">
              <span className="text-[#848E9C]">Avail. Open</span>
              <span className="text-[#EAECEF]">— USDC</span>
            </div>
          </div>
        </div>

        {/* ── Account section ── */}
        <div className="border-t border-[#2B2F36] pt-2 -mx-3 px-3">
          <div className="py-1 mb-1">
            <span className="text-[10px] font-semibold text-[#848E9C] uppercase tracking-wider">Account</span>
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
                <span className="text-[10px] text-[#848E9C]">{k}</span>
                <span className="text-[10px] text-[#EAECEF] font-mono">{v}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="h-2" />
      </form>
    </div>
  )
}
