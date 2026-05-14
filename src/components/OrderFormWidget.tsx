import { useState, useEffect, useMemo, useRef } from 'react'
import { api } from '../api/client'
import { useMarketStore } from '../store/marketStore'
import { useAuthStore } from '../store/authStore'
import { useToastStore } from '../store/toastStore'
import { Locale, useTranslation } from '../i18n/translations'

const UI_LANG = (window as unknown as { electronAPI?: { uiLang?: string } }).electronAPI?.uiLang
const locale: Locale = (UI_LANG === 'en' ? 'en' : 'zh-CN')

type Side = 'BUY' | 'SELL'
type OrderType = 'LIMIT' | 'MARKET' | 'STOP'
type MarginType = 'CROSS' | 'ISOLATED'
type PositionDir = 'OPEN' | 'CLOSE'

interface AccountSummary {
  symbol?: string | null
  base_asset?: string | null
  quote_asset?: string | null
  configured_leverage?: number | null
  long_position_qty?: number | null
  short_position_qty?: number | null
  long_position_value?: number | null
  short_position_value?: number | null
  rest_mark_price?: number | null
  available_balance: number | null
  margin_ratio: number | null
  risk_rate: number | null
  maint_margin: number | null
  total_equity: number | null
  position_value: number | null
  actual_leverage: number | null
  unrealized_pnl: number | null
  wallet_balance: number | null
  has_api_credentials: boolean
  message?: string | null
}

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

function describeRequestError(error: unknown) {
  if (error instanceof Error) {
    const axiosLike = error as Error & {
      code?: string
      response?: { status?: number; data?: unknown }
    }
    return {
      name: error.name,
      message: error.message,
      stack: error.stack,
      code: axiosLike.code ?? null,
      status: axiosLike.response?.status ?? null,
      data: axiosLike.response?.data ?? null,
    }
  }
  return { value: error }
}

function getLeverageStorageKey(username: string, symbol: string) {
  return `trade-relay:leverage:${username}:${symbol.toUpperCase()}`
}

function readStoredLeverage(username: string, symbol: string): number | null {
  try {
    const raw = window.localStorage.getItem(getLeverageStorageKey(username, symbol))
    if (!raw) return null
    const value = Number.parseInt(raw, 10)
    return Number.isFinite(value) && value >= 1 && value <= 125 ? value : null
  } catch {
    return null
  }
}

function writeStoredLeverage(username: string, symbol: string, leverage: number) {
  try {
    window.localStorage.setItem(getLeverageStorageKey(username, symbol), String(leverage))
  } catch {
    // Ignore storage errors so leverage changes still work without persistence.
  }
}

// ── Ticker strip ─────────────────────────────────────────────────────────────

function TickerStrip() {
  const { t } = useTranslation(locale)
  const { symbol, currentPrice, klines } = useMarketStore()

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
        <span className="text-[9px] text-[#848E9C] bg-[#1E2026] px-1.5 py-0.5 rounded uppercase tracking-wider">{t('order.perpetual')}</span>
      </div>
      <div className={`text-[22px] font-bold leading-none mb-2 tabular-nums ${priceColor}`}>
        {currentPrice != null ? fmt(currentPrice, currentPrice > 1000 ? 2 : 4) : '—'}
        {change24h != null && (
          <span className="text-[11px] font-normal ml-2 opacity-90">
            {isUp ? '+' : ''}{change24h.toFixed(2)}%
          </span>
        )}
      </div>
    </div>
  )
}

export function OrderFormWidget({
  onOrderPlaced,
  selectedOrderBookPrice,
  isActive = true,
  sizeUnit,
  onSizeUnitChange,
}: {
  onOrderPlaced?: () => void
  selectedOrderBookPrice?: { value: number; token: number } | null
  isActive?: boolean
  sizeUnit: 'QUOTE' | 'BASE'
  onSizeUnitChange: (nextUnit: 'QUOTE' | 'BASE') => void
}) {
  const { t } = useTranslation(locale)
  const { symbol, currentPrice, markPrice } = useMarketStore()
  const { user } = useAuthStore()
  const { baseAsset, quoteAsset } = useMemo(() => splitTradingSymbol(symbol), [symbol])

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
  const [leverageUpdating, setLeverageUpdating] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [accountSummary, setAccountSummary] = useState<AccountSummary | null>(null)
  const [accountLoading, setAccountLoading] = useState(false)
  const [marketConfirm, setMarketConfirm] = useState<{ side: Side; baseQty: number; body: Parameters<typeof api.submitOrder>[0] } | null>(null)
  const showToast = useToastStore((state) => state.showToast)
  const lastAccountErrorRef = useRef<string | null>(null)

  useEffect(() => {
    let alive = true

    const emptyAccountSummary: AccountSummary = {
      symbol,
      base_asset: baseAsset,
      quote_asset: quoteAsset,
      configured_leverage: null,
      long_position_qty: null,
      short_position_qty: null,
      available_balance: null,
      margin_ratio: null,
      risk_rate: null,
      maint_margin: null,
      total_equity: null,
      position_value: null,
      actual_leverage: null,
      unrealized_pnl: null,
      wallet_balance: null,
      has_api_credentials: false,
      message: null,
    }

    if (!isActive) {
      setAccountLoading(false)
      return () => { alive = false }
    }

    if (!user?.username) {
      setAccountSummary(emptyAccountSummary)
      setAccountLoading(false)
      return () => { alive = false }
    }

    const loadAccountSummary = async () => {
      setAccountLoading(true)
      try {
        const data = await api.getAccountSummary(symbol)
        if (data?.message) {
          window.electronAPI?.logToMain?.('warn', 'account summary returned message', {
            username: user?.username ?? null,
            symbol,
            response: data,
          })
        }
        if (alive) setAccountSummary(data)
      } catch (error) {
        window.electronAPI?.logToMain?.('error', 'account summary request failed', {
          username: user?.username ?? null,
          symbol,
          error: describeRequestError(error),
        })
        if (alive) {
          setAccountSummary((current) => current ? {
            ...current,
            message: t('order.error.loadAccount'),
          } : {
            ...emptyAccountSummary,
            message: t('order.error.loadAccount'),
          })
        }
      } finally {
        if (alive) setAccountLoading(false)
      }
    }

    loadAccountSummary()
    const timer = setInterval(loadAccountSummary, 15000)
    return () => { alive = false; clearInterval(timer) }
  }, [isActive, user?.username, symbol, baseAsset, quoteAsset])

  const baseTicker = baseAsset

  const fillMarkPrice = () => {
    const ref = markPrice ?? currentPrice
    if (ref != null) setPrice(ref.toFixed(2))
  }

  useEffect(() => {
    if (!user?.username) {
      setLeverage(10)
      return
    }

    const storedLeverage = readStoredLeverage(user.username, symbol)
    setLeverage(storedLeverage ?? 10)
  }, [user?.username, symbol])

  useEffect(() => {
    if (accountSummary?.configured_leverage && accountSummary.configured_leverage !== leverage) {
      if (user?.username) {
        writeStoredLeverage(user.username, symbol, accountSummary.configured_leverage)
      }
      setLeverage(accountSummary.configured_leverage)
    }
  }, [accountSummary?.configured_leverage, leverage, symbol, user?.username])

  useEffect(() => {
    if (!user?.username) return
    writeStoredLeverage(user.username, symbol, leverage)
  }, [leverage, symbol, user?.username])

  useEffect(() => {
    const message = accountSummary?.message ?? null
    if (!message) {
      lastAccountErrorRef.current = null
      return
    }
    if (!accountSummary?.has_api_credentials) return
    if (lastAccountErrorRef.current === message) return
    lastAccountErrorRef.current = message
    showToast('error', message)
  }, [accountSummary?.message, accountSummary?.has_api_credentials, showToast])

  const handleLeverageChange = async (nextLeverage: number) => {
    const previousLeverage = leverage
    setLeverage(nextLeverage)

    if (!user?.username) return

    setLeverageUpdating(true)
    try {
      await api.setAccountLeverage(symbol, nextLeverage)
      window.electronAPI?.logToMain?.('info', 'account leverage updated', {
        username: user.username,
        symbol,
        leverage: nextLeverage,
      })
      writeStoredLeverage(user.username, symbol, nextLeverage)
      setAccountSummary((current) => current ? { ...current, configured_leverage: nextLeverage } : current)
    } catch (error) {
      setLeverage(previousLeverage)
      writeStoredLeverage(user.username, symbol, previousLeverage)
      window.electronAPI?.logToMain?.('error', 'account leverage update failed', {
        username: user?.username ?? null,
        symbol,
        leverage: nextLeverage,
        error: describeRequestError(error),
      })
      const msg =
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (error as { message?: string })?.message || t('order.error.failed')
      showToast('error', msg)
    } finally {
      setLeverageUpdating(false)
    }
  }

  useEffect(() => {
    if (selectedOrderBookPrice?.value == null) return
    setPrice(String(selectedOrderBookPrice.value))
  }, [selectedOrderBookPrice])

  const estimatedCost = useMemo(() => {
    const qtyNum = parseFloat(qty)
    const priceNum = orderType === 'MARKET' ? (currentPrice ?? 0) : parseFloat(price)
    if (!qtyNum || !priceNum) return null
    // qty is in USDT → convert to base first
    const baseQty = sizeUnit === 'QUOTE' ? qtyNum / priceNum : qtyNum
    return (baseQty * priceNum) / leverage
  }, [qty, price, orderType, currentPrice, leverage, sizeUnit])

  const fillPct = (pct: number) => {
    if (posDir === 'CLOSE') {
      const longQty = accountSummary?.long_position_qty ?? 0
      const shortQty = accountSummary?.short_position_qty ?? 0
      const posQty = Math.max(longQty, shortQty)
      if (sizeUnit === 'QUOTE') {
        const refPrice = orderType === 'MARKET'
          ? (currentPrice ?? markPrice ?? accountSummary?.rest_mark_price ?? 0)
          : (parseFloat(price) || (currentPrice ?? markPrice ?? accountSummary?.rest_mark_price ?? 0))
        if (!refPrice) return
        setQty((posQty * refPrice * pct).toFixed(2))
      } else {
        setQty((Math.round(posQty * pct * 1000) / 1000).toFixed(3))
      }
      return
    }
    const refPrice = orderType === 'MARKET'
      ? (currentPrice ?? markPrice ?? accountSummary?.rest_mark_price ?? 0)
      : (parseFloat(price) || (currentPrice ?? markPrice ?? accountSummary?.rest_mark_price ?? 0))
    if (!refPrice) return
    const availableQuoteBalance = accountSummary?.available_balance ?? 0
    const maxBaseQty = (availableQuoteBalance * leverage) / refPrice
    if (sizeUnit === 'QUOTE') {
      setQty((maxBaseQty * refPrice * pct).toFixed(2))
    } else {
      setQty((Math.floor(maxBaseQty * pct * 1000) / 1000).toFixed(3))
    }
  }

  const referencePrice = useMemo(() => {
    if (orderType === 'MARKET') return currentPrice ?? markPrice ?? null
    const typedPrice = parseFloat(price)
    return Number.isFinite(typedPrice) && typedPrice > 0 ? typedPrice : (markPrice ?? currentPrice ?? null)
  }, [orderType, currentPrice, markPrice, price])

  const openAvailableDisplay = useMemo(() => {
    const availableBalance = accountSummary?.available_balance ?? null
    if (availableBalance == null) return '—'
    if (sizeUnit === 'QUOTE') return withAsset(availableBalance * leverage, quoteAsset)
    if (!referencePrice) return '—'
    return `${fmt((availableBalance * leverage) / referencePrice, 3)} ${baseTicker}`
  }, [accountSummary?.available_balance, leverage, sizeUnit, quoteAsset, referencePrice, baseTicker])

  const longCloseDisplay = useMemo(() => {
    const qty = accountSummary?.long_position_qty ?? null
    if (qty == null) return '—'
    if (sizeUnit === 'BASE') return `${fmt(qty, 3)} ${baseTicker}`
    if (qty === 0) return withAsset(0, quoteAsset)
    // prefer server-side notional (qty × REST markPrice at last account poll)
    const notional = accountSummary?.long_position_value ?? null
    if (notional != null) return withAsset(notional, quoteAsset)
    // fallback: use REST markPrice from account summary, then WS markPrice
    const price = accountSummary?.rest_mark_price ?? markPrice ?? currentPrice
    if (!price) return `${fmt(qty, 3)} ${baseTicker}`
    return withAsset(qty * price, quoteAsset)
  }, [accountSummary?.long_position_qty, accountSummary?.long_position_value, accountSummary?.rest_mark_price, sizeUnit, markPrice, currentPrice, quoteAsset, baseTicker])

  const shortCloseDisplay = useMemo(() => {
    const qty = accountSummary?.short_position_qty ?? null
    if (qty == null) return '—'
    if (sizeUnit === 'BASE') return `${fmt(qty, 3)} ${baseTicker}`
    if (qty === 0) return withAsset(0, quoteAsset)
    const notional = accountSummary?.short_position_value ?? null
    if (notional != null) return withAsset(notional, quoteAsset)
    const price = accountSummary?.rest_mark_price ?? markPrice ?? currentPrice
    if (!price) return `${fmt(qty, 3)} ${baseTicker}`
    return withAsset(qty * price, quoteAsset)
  }, [accountSummary?.short_position_qty, accountSummary?.short_position_value, accountSummary?.rest_mark_price, sizeUnit, markPrice, currentPrice, quoteAsset, baseTicker])

  // Unrealized PnL adjusted in real-time using the latest available price.
  // base_pnl comes from the REST poll; we add the delta caused by price movement
  // since that poll: delta = net_qty × (live_price − rest_mark_price).
  const liveUnrealizedPnl = useMemo(() => {
    const basePnl = accountSummary?.unrealized_pnl ?? null
    if (basePnl == null) return null
    const restMark = accountSummary?.rest_mark_price ?? null
    const livePrice = markPrice ?? currentPrice
    if (restMark == null || livePrice == null) return basePnl
    const longQty = accountSummary?.long_position_qty ?? 0
    const shortQty = accountSummary?.short_position_qty ?? 0
    const netQty = longQty - shortQty
    return basePnl + netQty * (livePrice - restMark)
  }, [
    accountSummary?.unrealized_pnl,
    accountSummary?.rest_mark_price,
    accountSummary?.long_position_qty,
    accountSummary?.short_position_qty,
    markPrice,
    currentPrice,
  ])

  const handleSubmit = async (submitSide: Side) => {
    const qtyNum = parseFloat(qty)
    if (!qtyNum || qtyNum <= 0) { showToast('error', t('order.error.invalidQuantity')); return }

    // API 始终接收 base 数量（BTC）；若用户以 USDT 输入则按参考价格换算
    let baseQty = qtyNum
    if (sizeUnit === 'QUOTE') {
      let refPrice = orderType === 'MARKET'
        // For market orders use the best available price: WS last price → mark price → REST mark price
        ? (currentPrice ?? markPrice ?? accountSummary?.rest_mark_price ?? 0)
        : (parseFloat(price) || (currentPrice ?? markPrice ?? accountSummary?.rest_mark_price ?? 0))
      // If still no price (WS not ready and account summary stale), fetch a fresh mark price
      if (!refPrice && orderType === 'MARKET') {
        try {
          refPrice = await api.getMarkPrice(symbol)
        } catch { /* ignore */ }
      }
      if (!refPrice) { showToast('error', t('order.error.priceUnavailable')); return }
      baseQty = qtyNum / refPrice
    }
    // Truncate to 0.001 (BTC contract step size).
    // CLOSE orders: use Math.round because qty→USDC→qty round-trip must be lossless.
    // OPEN orders: use Math.floor to never exceed available balance.
    if (posDir === 'CLOSE') {
      baseQty = Math.round(baseQty * 1000) / 1000
    } else {
      baseQty = Math.floor(baseQty * 1000) / 1000
    }
    if (baseQty <= 0) { showToast('error', t('order.error.invalidQuantity')); return }

    const body: Parameters<typeof api.submitOrder>[0] = {
      symbol, side: submitSide,
      order_type: orderType === 'STOP' ? 'STOP_MARKET' : orderType,
      quantity: baseQty,
      leverage,
      margin_type: marginType,
      position_direction: posDir,
    }
    if ((orderType === 'LIMIT' || orderType === 'STOP') && price) body.price = parseFloat(price)
    if (orderType === 'STOP' && stopPrice) body.stop_price = parseFloat(stopPrice)
    if (showTpSl && tp) body.tp_price = parseFloat(tp)
    if (showTpSl && sl) body.sl_price = parseFloat(sl)

    // Market orders require confirmation before submission
    if (orderType === 'MARKET') {
      setMarketConfirm({ side: submitSide, baseQty, body })
      return
    }

    await doSubmit(submitSide, baseQty, body)
  }

  const doSubmit = async (submitSide: Side, baseQty: number, body: Parameters<typeof api.submitOrder>[0]) => {
    setIsSubmitting(true)
    try {
      window.electronAPI?.logToMain?.('info', 'submit order', {
        username: user?.username ?? null,
        body,
        sizeUnit,
        inputQty: qty,
        baseQty,
      })

      await api.submitOrder(body)
      showToast('success', t('order.success'))
      setQty('')
      onOrderPlaced?.()
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (err as { message?: string })?.message || t('order.error.failed')
      window.electronAPI?.logToMain?.('error', 'submit order failed', {
        username: user?.username ?? null,
        symbol,
        side: submitSide,
        posDir,
        error: describeRequestError(err),
      })
      showToast('error', msg)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="relative h-full flex flex-col bg-[#0B0E11] overflow-hidden">

      {/* ── Ticker strip ── */}
      <TickerStrip />

      {/* ── Market order confirmation modal ── */}
      {marketConfirm && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/70" style={{ position: 'absolute' }}>
          <div className="bg-[#1E2026] border border-[#474D57] rounded-lg shadow-xl w-72 p-5 space-y-4">
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-bold text-[#EAECEF]">{t('order.market.confirm.title')}</span>
              <span className={`ml-auto text-[11px] font-bold px-2 py-0.5 rounded ${
                marketConfirm.side === 'BUY' ? 'bg-[#0ecb81]/20 text-[#0ecb81]' : 'bg-[#f6465d]/20 text-[#f6465d]'
              }`}>{marketConfirm.side === 'BUY' ? t('side.buy') : t('side.sell')}</span>
            </div>
            <div className="space-y-2 text-[11px]">
              <div className="flex justify-between">
                <span className="text-[#848E9C]">{t('log.symbol')}</span>
                <span className="text-[#EAECEF] font-mono">{symbol}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#848E9C]">{t('order.size')}</span>
                <span className="text-[#EAECEF] font-mono">{marketConfirm.baseQty} {baseAsset}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#848E9C]">{t('order.marketPrice')}</span>
                <span className="text-[#F0B90B] font-mono">{currentPrice != null ? fmt(currentPrice, 2) : '—'} {quoteAsset}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-[#848E9C]">{t('order.open')} / {t('order.close')}</span>
                <span className="text-[#EAECEF]">{posDir === 'OPEN' ? t('order.open') : t('order.close')}</span>
              </div>
            </div>
            <p className="text-[10px] text-[#F6465D] bg-[#F6465D]/10 rounded px-2.5 py-1.5 leading-relaxed">
              {t('order.market.confirm.warning')}
            </p>
            <div className="grid grid-cols-2 gap-2 pt-1">
              <button
                onClick={() => setMarketConfirm(null)}
                className="py-2 text-[12px] font-medium rounded border border-[#474D57] text-[#848E9C] hover:text-[#EAECEF] hover:border-[#848E9C] transition-colors">
                {t('order.market.confirm.cancel')}
              </button>
              <button
                onClick={async () => {
                  const c = marketConfirm
                  setMarketConfirm(null)
                  await doSubmit(c.side, c.baseQty, c.body)
                }}
                disabled={isSubmitting}
                className={`py-2 text-[12px] font-bold rounded transition-colors disabled:opacity-50 ${
                  marketConfirm.side === 'BUY'
                    ? 'bg-[#0ecb81] hover:bg-[#0ab36d] text-white'
                    : 'bg-[#f6465d] hover:bg-[#d93a4e] text-white'
                }`}>
                {isSubmitting ? t('order.submitting') : t('order.market.confirm.submit')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Open / Close tabs ── */}
      <div className="grid grid-cols-2 shrink-0 border-b border-[#2B2F36]">
        <button onClick={() => setPosDir('OPEN')}
          className={`py-2.5 text-[13px] font-bold tracking-wide transition-colors ${
            posDir === 'OPEN' ? 'text-[#EAECEF] border-b-2 border-[#F0B90B]' : 'text-[#848E9C] hover:text-[#EAECEF]'
          }`}>
          {t('order.open')}
        </button>
        <button onClick={() => setPosDir('CLOSE')}
          className={`py-2.5 text-[13px] font-bold tracking-wide transition-colors ${
            posDir === 'CLOSE' ? 'text-[#EAECEF] border-b-2 border-[#F0B90B]' : 'text-[#848E9C] hover:text-[#EAECEF]'
          }`}>
          {t('order.close')}
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
              {m === 'CROSS' ? t('order.margin.cross') : t('order.margin.isolated')}
            </button>
          ))}
        </div>
        {/* Leverage */}
        <div className="ml-auto flex items-center gap-1.5">
          <span className="text-[10px] text-[#848E9C]">{t('order.leverageShort')}</span>
          <select value={leverage} onChange={e => { void handleLeverageChange(Number(e.target.value)) }} disabled={leverageUpdating}
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
            {t === 'LIMIT' ? useTranslation(locale).t('order.limit') : t === 'MARKET' ? useTranslation(locale).t('order.market') : useTranslation(locale).t('type.stop')}
          </button>
        ))}
      </div>

      {/* ── Form ── */}
      <form onSubmit={e => e.preventDefault()} className="flex-1 overflow-y-auto px-3 pt-3 space-y-2.5 min-h-0">

        {/* Stop trigger price */}
        {orderType === 'STOP' && (
          <div>
            <label className="block text-[10px] text-[#848E9C] uppercase tracking-wider mb-1">{t('order.triggerPrice')}</label>
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
                {orderType === 'STOP' ? t('order.limitPrice') : t('order.price')}
              </label>
              <button type="button" onClick={fillMarkPrice}
                className="text-[9px] text-[#F0B90B] hover:text-[#D9A429] transition-colors">
                {t('order.fillMark')} ↓
              </button>
            </div>
            <div className="relative">
              <input type="number" value={price} onChange={e => setPrice(e.target.value)}
                placeholder="0.00" min="0" step="any"
                className="w-full bg-[#1E2026] border border-[#2B2F36] focus:border-[#F0B90B] text-[13px] text-[#EAECEF] rounded px-2.5 py-1.5 outline-none selectable pr-14" />
              <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-[#848E9C]">{quoteAsset}</span>
            </div>
          </div>
        )}

        {/* Market price info */}
        {orderType === 'MARKET' && (() => {
          const displayPrice = currentPrice ?? markPrice ?? accountSummary?.rest_mark_price ?? null
          return displayPrice != null ? (
            <div className="flex items-center justify-between px-2 py-1.5 bg-[#1E2026] rounded text-[11px]">
              <span className="text-[#848E9C]">{t('order.marketPrice')}</span>
              <span className="text-[#EAECEF] font-mono tabular-nums">{fmt(displayPrice, 2)}</span>
            </div>
          ) : null
        })()}

        {/* Size */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <label className="text-[10px] text-[#848E9C] uppercase tracking-wider">{t('order.size')}</label>
          </div>
          <div className="flex">
            <input type="number" value={qty} onChange={e => setQty(e.target.value)}
              placeholder="0.0000" min="0" step="any"
              className="flex-1 min-w-0 bg-[#1E2026] border border-[#2B2F36] border-r-0 focus:border-[#F0B90B] text-[13px] text-[#EAECEF] rounded-l px-2.5 py-1.5 outline-none selectable" />
            <select
              value={sizeUnit}
              onChange={e => { onSizeUnitChange(e.target.value as 'QUOTE' | 'BASE'); setQty('') }}
              className="bg-[#2B2F36] border border-[#2B2F36] text-[#EAECEF] text-[11px] rounded-r px-2 py-1.5 outline-none cursor-pointer shrink-0">
              <option value="QUOTE">{quoteAsset}</option>
              <option value="BASE">{baseTicker}</option>
            </select>
          </div>
        </div>

        {/* Percentage quick fill */}
        <div className="grid grid-cols-4 gap-1">
          {[0.25, 0.5, 0.75, 1.0].map(pct => (
            <button key={pct} type="button" onClick={() => fillPct(pct)}
              className="py-1 text-[10px] rounded bg-[#2B2F36] text-[#C7CCD3] hover:bg-[#363C45] hover:text-[#F0B90B] hover:border-[#F0B90B] active:scale-95 transition-all border border-[#474D57] cursor-pointer select-none">
              {pct * 100}%
            </button>
          ))}
        </div>

        {/* Estimated margin cost */}
        {estimatedCost != null && (
          <div className="flex items-center justify-between px-2.5 py-1.5 bg-[#161A1E] border border-[#2B2F36] rounded">
            <span className="text-[10px] text-[#848E9C]">{t('order.estimatedMargin')} ({leverage}×)</span>
            <span className="text-[11px] text-[#EAECEF] font-mono tabular-nums">{fmt(estimatedCost, 2)} {quoteAsset}</span>
          </div>
        )}

        {/* TP / SL */}
        <div>
          <button type="button" onClick={() => setShowTpSl(v => !v)}
            className={`flex items-center gap-1.5 text-[10px] transition-colors ${showTpSl ? 'text-[#F0B90B]' : 'text-[#848E9C] hover:text-[#EAECEF]'}`}>
            <span className={`flex items-center justify-center w-3 h-3 rounded-sm border text-[8px] leading-none ${showTpSl ? 'bg-[#F0B90B] border-[#F0B90B] text-black' : 'border-[#848E9C]'}`}>
              {showTpSl ? '✓' : ''}
            </span>
            {t('order.tpSl')}
          </button>
        </div>

        {showTpSl && (
          <div className="space-y-2">
            <div>
              <label className="block text-[10px] text-[#848E9C] uppercase tracking-wider mb-1">{t('order.takeProfit')}</label>
              <div className="relative">
                <input type="number" value={tp} onChange={e => setTp(e.target.value)}
                  placeholder="0.00" min="0" step="any"
                  className="w-full bg-[#1E2026] border border-[#2B2F36] focus:border-[#0ECB81] text-[13px] text-[#EAECEF] rounded px-2.5 py-1.5 outline-none selectable pr-10" />
                <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-[#0ECB81]">TP</span>
              </div>
            </div>
            <div>
              <label className="block text-[10px] text-[#848E9C] uppercase tracking-wider mb-1">{t('order.stopLoss')}</label>
              <div className="relative">
                <input type="number" value={sl} onChange={e => setSl(e.target.value)}
                  placeholder="0.00" min="0" step="any"
                  className="w-full bg-[#1E2026] border border-[#2B2F36] focus:border-[#F6465D] text-[13px] text-[#EAECEF] rounded px-2.5 py-1.5 outline-none selectable pr-10" />
                <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-[#F6465D]">SL</span>
              </div>
            </div>
          </div>
        )}
        {/* Submit */}
        {!user ? (
          <div className="grid grid-cols-2 gap-2">
            <button type="button" disabled
              className="py-2.5 text-[13px] font-bold rounded bg-[#0ecb81]/20 text-[#0ecb81]/40 cursor-not-allowed border border-[#0ecb81]/20">
              {t('order.openLong')}
            </button>
            <button type="button" disabled
              className="py-2.5 text-[13px] font-bold rounded bg-[#f6465d]/20 text-[#f6465d]/40 cursor-not-allowed border border-[#f6465d]/20">
              {t('order.openShort')}
            </button>
            <div className="col-span-2 flex items-center justify-center gap-1.5 py-1.5 rounded bg-[#F0B90B]/10 border border-[#F0B90B]/40">
              <span className="text-[#F0B90B] text-[11px]">⚠</span>
              <span className="text-[12px] font-medium text-[#F0B90B]">{t('order.loginRequired')}</span>
            </div>
          </div>
        ) : (
        <div className="grid grid-cols-2 gap-2">
          {posDir === 'OPEN' ? (<>
            <button type="button" onClick={() => handleSubmit('BUY')} disabled={isSubmitting || !qty}
              className="py-2.5 text-[13px] font-bold rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed bg-[#0ecb81] hover:bg-[#0ab36d] text-white">
              {isSubmitting ? t('order.submitting') : t('order.openLong')}
            </button>
            <button type="button" onClick={() => handleSubmit('SELL')} disabled={isSubmitting || !qty}
              className="py-2.5 text-[13px] font-bold rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed bg-[#f6465d] hover:bg-[#d93a4e] text-white">
              {isSubmitting ? t('order.submitting') : t('order.openShort')}
            </button>
          </>) : (<>
            <button type="button" onClick={() => handleSubmit('BUY')} disabled={isSubmitting || !qty}
              className="py-2.5 text-[13px] font-bold rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed bg-[#0ecb81] hover:bg-[#0ab36d] text-white">
              {isSubmitting ? t('order.submitting') : t('order.closeShort')}
            </button>
            <button type="button" onClick={() => handleSubmit('SELL')} disabled={isSubmitting || !qty}
              className="py-2.5 text-[13px] font-bold rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed bg-[#f6465d] hover:bg-[#d93a4e] text-white">
              {isSubmitting ? t('order.submitting') : t('order.closeLong')}
            </button>
          </>)}
        </div>
        )}

        {/* Position info */}
        <div className="grid grid-cols-2 gap-x-2 pt-1">
          {/* Long side */}
          <div className="space-y-1">
            {posDir === 'OPEN' && (
              <>
                <div className="flex justify-between text-[9px]">
                  <span className="text-[#848E9C]">{t('order.liqPrice')}</span>
                  <span className="text-[#0ECB81]">— {quoteAsset}</span>
                </div>
                <div className="flex justify-between text-[9px]">
                  <span className="text-[#848E9C]">{t('pos.margin')}</span>
                  <span className="text-[#EAECEF]">— {quoteAsset}</span>
                </div>
              </>
            )}
            <div className="flex justify-between text-[9px]">
              <span className="text-[#848E9C]">{posDir === 'OPEN' ? t('order.availOpen') : t('order.availClose')}</span>
              <span className="text-[#EAECEF]">{posDir === 'OPEN' ? openAvailableDisplay : shortCloseDisplay}</span>
            </div>
          </div>
          {/* Short side */}
          <div className="space-y-1">
            {posDir === 'OPEN' && (
              <>
                <div className="flex justify-between text-[9px]">
                  <span className="text-[#848E9C]">{t('order.liqPrice')}</span>
                  <span className="text-[#F6465D]">— {quoteAsset}</span>
                </div>
                <div className="flex justify-between text-[9px]">
                  <span className="text-[#848E9C]">{t('pos.margin')}</span>
                  <span className="text-[#EAECEF]">— {quoteAsset}</span>
                </div>
              </>
            )}
            <div className="flex justify-between text-[9px]">
              <span className="text-[#848E9C]">{posDir === 'OPEN' ? t('order.availOpen') : t('order.availClose')}</span>
              <span className="text-[#EAECEF]">{posDir === 'OPEN' ? openAvailableDisplay : longCloseDisplay}</span>
            </div>
          </div>
        </div>

        {/* ── Account section ── */}
        <div className="border-t border-[#2B2F36] pt-2 -mx-3 px-3">
          <div className="py-1 mb-1">
            <span className="text-[10px] font-semibold text-[#848E9C] uppercase tracking-wider">{t('order.account')}</span>
          </div>
          <div className="space-y-1 pb-2">
            {[
              [t('order.account.marginRatio'),    fmtRate(accountSummary?.margin_ratio ?? null)],
              [t('order.account.riskRate'),       fmtRate(accountSummary?.risk_rate ?? null)],
              [t('order.account.maintMargin'),    withAsset(accountSummary?.maint_margin, quoteAsset)],
              [t('order.account.totalEquity'),    withAsset(accountSummary?.total_equity, quoteAsset)],
              [t('order.account.positionValue'),  withAsset(accountSummary?.position_value, quoteAsset)],
              [t('order.account.actualLeverage'), withTimes(accountSummary?.actual_leverage)],
              [t('order.account.unrealizedPnl'),  withAsset(liveUnrealizedPnl, quoteAsset)],
              [t('order.account.walletBalance'),  withAsset(accountSummary?.wallet_balance, quoteAsset)],
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

function withAsset(value: number | null | undefined, asset: string) {
  if (value == null || Number.isNaN(value)) return '—'
  return `${fmt(value, 2)} ${asset}`
}

function withTimes(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return '—'
  return `${fmt(value, 2)}x`
}
