import { useState, useEffect, useMemo, useRef } from 'react'
import { api, getBackendWebSocketUrl } from '../api/client'
import { useMarketStore } from '../store/marketStore'
import { useAuthStore } from '../store/authStore'
import { useToastStore } from '../store/toastStore'
import { Locale, useTranslation } from '../i18n/translations'
import { perfSignalDone } from '../utils/perf'
import { getPreferredLocale, useUiPreferencesStore } from '../store/uiPreferencesStore'

type Side = 'BUY' | 'SELL'
type OrderType = 'LIMIT' | 'MARKET' | 'CONDITIONAL' | 'POST_ONLY'
type AdvancedOrderType = 'CONDITIONAL' | 'POST_ONLY'
type CondSubType = 'LIMIT' | 'MARKET'
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

interface PositionSnapshot {
  symbol: string
  side: string
  quantity: number
  entry_price: number | null
  unrealized_pnl: number | null
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

function fmtSigned(n: number | null | undefined, decimals = 2): string {
  if (n == null || isNaN(n)) return '—'
  const sign = n > 0 ? '+' : ''
  return `${sign}${fmt(n, decimals)}`
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

function getConditionalSubTypeStorageKey(username: string, symbol: string) {
  return `trade-relay:conditional-sub-type:${username}:${symbol.toUpperCase()}`
}

function getAdvancedOrderTypeStorageKey(username: string, symbol: string) {
  return `trade-relay:advanced-order-type:${username}:${symbol.toUpperCase()}`
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

function readStoredConditionalSubType(username: string, symbol: string): CondSubType | null {
  try {
    const raw = window.localStorage.getItem(getConditionalSubTypeStorageKey(username, symbol))
    return raw === 'LIMIT' || raw === 'MARKET' ? raw : null
  } catch {
    return null
  }
}

function writeStoredConditionalSubType(username: string, symbol: string, subType: CondSubType) {
  try {
    window.localStorage.setItem(getConditionalSubTypeStorageKey(username, symbol), subType)
  } catch {
    // Ignore storage errors so order-type changes still work without persistence.
  }
}

function readStoredAdvancedOrderType(username: string, symbol: string): AdvancedOrderType | null {
  try {
    const raw = window.localStorage.getItem(getAdvancedOrderTypeStorageKey(username, symbol))
    return raw === 'CONDITIONAL' || raw === 'POST_ONLY' ? raw : null
  } catch {
    return null
  }
}

function writeStoredAdvancedOrderType(username: string, symbol: string, type: AdvancedOrderType) {
  try {
    window.localStorage.setItem(getAdvancedOrderTypeStorageKey(username, symbol), type)
  } catch {
    // Ignore storage errors so order-type changes still work without persistence.
  }
}

// ── Ticker strip ─────────────────────────────────────────────────────────────

function TickerStrip({ onFillMark }: { onFillMark: () => void }) {
  const locale = getPreferredLocale()
  const { t } = useTranslation(locale)
  const { symbol, currentPrice, dayPriceChange, dayPriceChangePercent } = useMarketStore()

  const hasDayTicker = dayPriceChange !== null && dayPriceChangePercent !== null
  const isUp = hasDayTicker ? (dayPriceChange ?? 0) >= 0 : null
  const priceColor = isUp == null ? 'text-[#EAECEF]' : isUp ? 'text-[#0ECB81]' : 'text-[#F6465D]'
  const changeAmountText = hasDayTicker ? fmtSigned(dayPriceChange, currentPrice != null && currentPrice > 1000 ? 2 : 4) : '--'
  const changePercentText = hasDayTicker ? `${isUp ? '+' : ''}${(dayPriceChangePercent ?? 0).toFixed(2)}%` : '--'

  return (
    <div className="px-3 py-2.5 border-b border-[#2B2F36] bg-[#0B0E11] shrink-0 select-none">
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-bold text-[#EAECEF] tracking-wide">{symbol}</span>
          <span className="text-[9px] text-[#848E9C] bg-[#1E2026] px-1.5 py-0.5 rounded uppercase tracking-wider">{t('order.perpetual')}</span>
        </div>
        <span className="text-[10px] uppercase tracking-[0.12em] text-[#848E9C]">24H</span>
      </div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <div className={`text-[22px] font-bold leading-none tabular-nums ${priceColor}`}>
              {currentPrice != null ? fmt(currentPrice, currentPrice > 1000 ? 2 : 4) : '—'}
            </div>
            <button
              type="button"
              onClick={onFillMark}
              className="shrink-0 rounded bg-[#1E2026] px-2 py-1 text-[10px] font-medium text-[#F0B90B] transition-colors hover:text-[#D9A429]"
            >
              Mark
            </button>
          </div>
        </div>
        <div className="flex min-w-[132px] flex-col items-end text-right">
          <span className={`text-[16px] font-semibold tabular-nums ${priceColor}`}>{changePercentText}</span>
          <span className={`text-[14px] font-medium tabular-nums ${priceColor}`}>{changeAmountText}</span>
        </div>
      </div>
    </div>
  )
}

export function OrderFormWidget({
  onOrderPlaced,
  onLoginClick,
  selectedOrderBookPrice,
  isActive = true,
  sizeUnit,
  onSizeUnitChange,
  refreshTrigger,
}: {
  onOrderPlaced?: () => void
  onLoginClick?: () => void
  selectedOrderBookPrice?: { value: number; token: number } | null
  isActive?: boolean
  sizeUnit: 'QUOTE' | 'BASE'
  onSizeUnitChange: (nextUnit: 'QUOTE' | 'BASE') => void
  refreshTrigger?: number
}) {
  const locale = useUiPreferencesStore((state) => state.locale)
  const { t } = useTranslation(locale)
  const { symbol, currentPrice, markPrice } = useMarketStore()
  const { user } = useAuthStore()
  const { baseAsset, quoteAsset } = useMemo(() => splitTradingSymbol(symbol), [symbol])
  const isAdminAccount = user?.role === 'admin'

  const [side, setSide] = useState<Side>('BUY')
  const [marginType, setMarginType] = useState<MarginType>('CROSS')
  const [posDir, setPosDir] = useState<PositionDir>('OPEN')
  const [orderType, setOrderType] = useState<OrderType>('LIMIT')
  const [lastAdvancedOrderType, setLastAdvancedOrderType] = useState<AdvancedOrderType>('CONDITIONAL')
  const [condSubType, setCondSubType] = useState<CondSubType>('MARKET')
  const [advancedOrderTypeOpen, setAdvancedOrderTypeOpen] = useState(false)
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
  const [positionLongQty, setPositionLongQty] = useState<number | null>(null)
  const [positionShortQty, setPositionShortQty] = useState<number | null>(null)
  const [liveSymbolPositions, setLiveSymbolPositions] = useState<PositionSnapshot[] | null>(null)
  const showToast = useToastStore((state) => state.showToast)
  const lastAccountErrorRef = useRef<string | null>(null)
  const loadAccountSummaryRef = useRef<(() => Promise<void>) | null>(null)
  const forceLoadAccountSummaryRef = useRef<(() => Promise<void>) | null>(null)
  const loadSymbolPositionsRef = useRef<(() => Promise<void>) | null>(null)
  const _accountFirstLoadDone = useRef(false)

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

    if (!user?.username || isAdminAccount) {
      setAccountSummary(emptyAccountSummary)
      setAccountLoading(false)
      return () => { alive = false }
    }

    const loadAccountSummary = async (force = false) => {
      setAccountLoading(true)
      try {
        const data = await api.getAccountSummary(symbol, force)
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
        if (alive) {
          setAccountLoading(false)
          if (!_accountFirstLoadDone.current) {
            _accountFirstLoadDone.current = true
            perfSignalDone('account-summary: first load done')
          }
        }
      }
    }

    _accountFirstLoadDone.current = false
    loadAccountSummary()
    loadAccountSummaryRef.current = loadAccountSummary
    forceLoadAccountSummaryRef.current = () => loadAccountSummary(true)
    const timer = setInterval(loadAccountSummary, 30000)
    return () => { alive = false; clearInterval(timer) }
  }, [isActive, user?.username, isAdminAccount, symbol, baseAsset, quoteAsset])

  // Refresh account summary immediately when external trigger fires (e.g. position closed)
  useEffect(() => {
    if (!refreshTrigger || !isActive || !user?.username || isAdminAccount) return
    void forceLoadAccountSummaryRef.current?.()
  }, [refreshTrigger, isActive, user?.username, isAdminAccount])

  // Load positions for current symbol to compute Avail. Close instantly (without waiting for account summary)
  useEffect(() => {
    if (!isActive || !user?.username || !symbol || isAdminAccount) {
      setPositionLongQty(null)
      setPositionShortQty(null)
      setLiveSymbolPositions(null)
      return
    }
    let alive = true
    const load = async () => {
      try {
        const all = await api.getPositions()
        if (!alive) return
        const sym = symbol.toUpperCase()
        const nextPositions = all.filter((p) => p.symbol.toUpperCase() === sym)
        let longQty = 0
        let shortQty = 0
        for (const p of nextPositions) {
          if (p.side === 'LONG') longQty += p.quantity
          else if (p.side === 'SHORT') shortQty += p.quantity
        }
        setLiveSymbolPositions(nextPositions)
        setPositionLongQty(longQty)
        setPositionShortQty(shortQty)
      } catch {
        // silently ignore — account summary is fallback
      }
    }
    loadSymbolPositionsRef.current = load
    void load()
    return () => {
      alive = false
      loadSymbolPositionsRef.current = null
    }
  }, [isActive, user?.username, symbol, refreshTrigger, isAdminAccount])

  useEffect(() => {
    if (!isActive || !user?.username || isAdminAccount) return

    let alive = true
    let socket: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let reloadTimer: ReturnType<typeof setTimeout> | null = null
    let lastReloadAt = 0
    const RELOAD_COOLDOWN_MS = 1500

    const scheduleReload = () => {
      if (!alive) return
      if (reloadTimer) clearTimeout(reloadTimer)
      const now = Date.now()
      const delay = Math.max(200, lastReloadAt + RELOAD_COOLDOWN_MS - now)
      reloadTimer = setTimeout(() => {
        lastReloadAt = Date.now()
        void loadSymbolPositionsRef.current?.()
      }, delay)
    }

    const connect = async () => {
      const token = await window.electronAPI?.getToken?.()
      if (!alive || !token) return

      const wsUrl = new URL(getBackendWebSocketUrl('/api/positions/ws'))
      wsUrl.searchParams.set('token', token)
      socket = new WebSocket(wsUrl.toString())

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data as string) as { type?: string; event?: string }
          if (data.type === 'account_update') {
            scheduleReload()
          } else if (data.type === 'order_update' && (data.event === 'POLL' || data.event === 'SYNC' || data.event === 'REST_SYNC')) {
            scheduleReload()
          }
        } catch {
          // ignore malformed messages
        }
      }

      socket.onerror = () => {
        socket?.close()
      }

      socket.onclose = () => {
        if (!alive) return
        reconnectTimer = setTimeout(() => {
          void connect()
        }, 3000)
      }
    }

    void connect()

    return () => {
      alive = false
      if (reloadTimer) clearTimeout(reloadTimer)
      if (reconnectTimer) clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [isActive, user?.username, isAdminAccount])

  const baseTicker = baseAsset

  const orderTypeLabel = (type: OrderType) => {
    if (type === 'LIMIT') return t('order.limit')
    if (type === 'MARKET') return t('order.market')
    if (type === 'POST_ONLY') return t('order.postOnly')
    return t('type.conditional')
  }

  const selectedAdvancedOrderType: AdvancedOrderType =
    orderType === 'CONDITIONAL' || orderType === 'POST_ONLY'
      ? orderType
      : lastAdvancedOrderType

  const selectOrderType = (type: OrderType) => {
    setOrderType(type)
    if (type === 'CONDITIONAL' || type === 'POST_ONLY') {
      setLastAdvancedOrderType(type)
    }
    setAdvancedOrderTypeOpen(false)
  }

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
    if (!user?.username) {
      setLastAdvancedOrderType('CONDITIONAL')
      setCondSubType('MARKET')
      return
    }

    const storedAdvancedOrderType = readStoredAdvancedOrderType(user.username, symbol)
    setLastAdvancedOrderType(storedAdvancedOrderType ?? 'CONDITIONAL')

    const storedCondSubType = readStoredConditionalSubType(user.username, symbol)
    setCondSubType(storedCondSubType ?? 'MARKET')
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
    if (!user?.username) return
    writeStoredConditionalSubType(user.username, symbol, condSubType)
  }, [condSubType, symbol, user?.username])

  useEffect(() => {
    if (!user?.username) return
    writeStoredAdvancedOrderType(user.username, symbol, lastAdvancedOrderType)
  }, [lastAdvancedOrderType, symbol, user?.username])

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
    const nextValue = String(selectedOrderBookPrice.value)
    if (orderType === 'CONDITIONAL') {
      setStopPrice(nextValue)
      return
    }
    setPrice(nextValue)
  }, [orderType, selectedOrderBookPrice])

  const estimatedCost = useMemo(() => {
    const qtyNum = parseFloat(qty)
    const priceNum = orderType === 'MARKET' ? (currentPrice ?? 0) : parseFloat(price)
    if (!qtyNum || !priceNum) return null
    // qty is in USDT → convert to base first
    const baseQty = sizeUnit === 'QUOTE' ? qtyNum / priceNum : qtyNum
    return (baseQty * priceNum) / leverage
  }, [qty, price, orderType, currentPrice, leverage, sizeUnit])

  const tpSlEntryPrice = useMemo(() => {
    const entryPrice = orderType === 'MARKET'
      ? (currentPrice ?? markPrice ?? accountSummary?.rest_mark_price ?? null)
      : (parseFloat(price) || (currentPrice ?? markPrice ?? accountSummary?.rest_mark_price ?? null))
    return entryPrice && entryPrice > 0 ? entryPrice : null
  }, [orderType, price, currentPrice, markPrice, accountSummary?.rest_mark_price])

  const tpSlBaseQty = useMemo(() => {
    const qtyNum = parseFloat(qty)
    if (!qtyNum || qtyNum <= 0 || !tpSlEntryPrice) return null
    return sizeUnit === 'QUOTE' ? qtyNum / tpSlEntryPrice : qtyNum
  }, [qty, sizeUnit, tpSlEntryPrice])

  const getTpSlEstimate = useMemo(() => {
    return (targetPriceText: string) => {
      const targetPrice = parseFloat(targetPriceText)
      if (!tpSlEntryPrice || !tpSlBaseQty || !targetPrice || targetPrice <= 0) return null

      const buyPnl = tpSlBaseQty * (targetPrice - tpSlEntryPrice)
      const sellPnl = tpSlBaseQty * (tpSlEntryPrice - targetPrice)
      const roiBase = estimatedCost && estimatedCost > 0 ? estimatedCost : null

      return {
        buyPnl,
        sellPnl,
        buyPct: roiBase ? (buyPnl / roiBase) * 100 : null,
        sellPct: roiBase ? (sellPnl / roiBase) * 100 : null,
      }
    }
  }, [estimatedCost, tpSlBaseQty, tpSlEntryPrice])

  const tpEstimate = useMemo(() => getTpSlEstimate(tp), [getTpSlEstimate, tp])
  const slEstimate = useMemo(() => getTpSlEstimate(sl), [getTpSlEstimate, sl])

  const liveLongQty = positionLongQty ?? accountSummary?.long_position_qty ?? null
  const liveShortQty = positionShortQty ?? accountSummary?.short_position_qty ?? null

  const liveLongEntryPrice = useMemo(() => {
    if (!liveSymbolPositions || liveSymbolPositions.length === 0) return null
    let totalQty = 0
    let totalCost = 0
    for (const position of liveSymbolPositions) {
      if (position.side !== 'LONG' || position.entry_price == null || position.quantity <= 0) continue
      totalQty += position.quantity
      totalCost += position.quantity * position.entry_price
    }
    if (totalQty <= 0) return null
    return totalCost / totalQty
  }, [liveSymbolPositions])

  const liveShortEntryPrice = useMemo(() => {
    if (!liveSymbolPositions || liveSymbolPositions.length === 0) return null
    let totalQty = 0
    let totalCost = 0
    for (const position of liveSymbolPositions) {
      if (position.side !== 'SHORT' || position.entry_price == null || position.quantity <= 0) continue
      totalQty += position.quantity
      totalCost += position.quantity * position.entry_price
    }
    if (totalQty <= 0) return null
    return totalCost / totalQty
  }, [liveSymbolPositions])

  const closeEstimatePrice = useMemo(() => {
    if (posDir !== 'CLOSE') return null
    if (orderType === 'MARKET') return currentPrice ?? markPrice ?? accountSummary?.rest_mark_price ?? null
    const typedPrice = parseFloat(price)
    return Number.isFinite(typedPrice) && typedPrice > 0 ? typedPrice : null
  }, [posDir, orderType, currentPrice, markPrice, accountSummary?.rest_mark_price, price])

  const closeEstimateBaseQty = useMemo(() => {
    if (posDir !== 'CLOSE' || !closeEstimatePrice) return null
    const qtyNum = parseFloat(qty)
    if (!qtyNum || qtyNum <= 0) return null
    return sizeUnit === 'QUOTE' ? qtyNum / closeEstimatePrice : qtyNum
  }, [posDir, closeEstimatePrice, qty, sizeUnit])

  const closeEstimate = useMemo(() => {
    if (posDir !== 'CLOSE' || !closeEstimatePrice || !closeEstimateBaseQty) return null

    const longClosableQty = Math.min(closeEstimateBaseQty, Math.max(liveLongQty ?? 0, 0))
    const shortClosableQty = Math.min(closeEstimateBaseQty, Math.max(liveShortQty ?? 0, 0))

    const longPnl = longClosableQty > 0 && liveLongEntryPrice != null
      ? longClosableQty * (closeEstimatePrice - liveLongEntryPrice)
      : null
    const shortPnl = shortClosableQty > 0 && liveShortEntryPrice != null
      ? shortClosableQty * (liveShortEntryPrice - closeEstimatePrice)
      : null

    return {
      price: closeEstimatePrice,
      longPnl,
      shortPnl,
    }
  }, [
    posDir,
    closeEstimatePrice,
    closeEstimateBaseQty,
    liveLongQty,
    liveShortQty,
    liveLongEntryPrice,
    liveShortEntryPrice,
  ])

  const fillPct = (pct: number) => {
    if (posDir === 'CLOSE') {
      const longQty = liveLongQty ?? 0
      const shortQty = liveShortQty ?? 0
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
    const qty = liveLongQty
    if (qty == null) return '—'
    if (sizeUnit === 'BASE') return `${fmt(qty, 3)} ${baseTicker}`
    if (qty === 0) return withAsset(0, quoteAsset)
    // Use live WS price first for immediate response
    const livePrice = markPrice ?? currentPrice
    if (livePrice != null) return withAsset(qty * livePrice, quoteAsset)
    // Fallback: server-side notional from account summary
    const notional = accountSummary?.long_position_value ?? null
    if (notional != null) return withAsset(notional, quoteAsset)
    const restPrice = accountSummary?.rest_mark_price ?? null
    if (restPrice != null) return withAsset(qty * restPrice, quoteAsset)
    return `${fmt(qty, 3)} ${baseTicker}`
  }, [liveLongQty, accountSummary?.long_position_value, accountSummary?.rest_mark_price, sizeUnit, markPrice, currentPrice, quoteAsset, baseTicker])

  const shortCloseDisplay = useMemo(() => {
    const qty = liveShortQty
    if (qty == null) return '—'
    if (sizeUnit === 'BASE') return `${fmt(qty, 3)} ${baseTicker}`
    if (qty === 0) return withAsset(0, quoteAsset)
    const livePrice = markPrice ?? currentPrice
    if (livePrice != null) return withAsset(qty * livePrice, quoteAsset)
    const notional = accountSummary?.short_position_value ?? null
    if (notional != null) return withAsset(notional, quoteAsset)
    const restPrice = accountSummary?.rest_mark_price ?? null
    if (restPrice != null) return withAsset(qty * restPrice, quoteAsset)
    return `${fmt(qty, 3)} ${baseTicker}`
  }, [liveShortQty, accountSummary?.short_position_value, accountSummary?.rest_mark_price, sizeUnit, markPrice, currentPrice, quoteAsset, baseTicker])

  // Unrealized PnL adjusted in real-time using the latest available price.
  // base_pnl comes from the REST poll; we add the delta caused by price movement
  // since that poll: delta = net_qty × (live_price − rest_mark_price).
  const livePositionUnrealizedPnl = useMemo(() => {
    if (liveSymbolPositions == null) return null
    if (liveSymbolPositions.length === 0) return 0
    const livePrice = markPrice ?? currentPrice
    return liveSymbolPositions.reduce((total, position) => {
      if (livePrice != null && position.entry_price != null) {
        if (position.side === 'LONG') return total + position.quantity * (livePrice - position.entry_price)
        if (position.side === 'SHORT') return total + position.quantity * (position.entry_price - livePrice)
      }
      return total + (position.unrealized_pnl ?? 0)
    }, 0)
  }, [liveSymbolPositions, markPrice, currentPrice])

  const liveUnrealizedPnl = useMemo(() => {
    if (livePositionUnrealizedPnl != null) return livePositionUnrealizedPnl
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
    livePositionUnrealizedPnl,
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

    // Map frontend order type to backend order_type
    let backendOrderType: string = orderType
    if (orderType === 'CONDITIONAL') {
      backendOrderType = condSubType === 'LIMIT' ? 'STOP' : 'STOP_MARKET'
    } else if (orderType === 'POST_ONLY') {
      backendOrderType = 'LIMIT'
    }

    const isPostOnly = orderType === 'POST_ONLY'

    const body: Parameters<typeof api.submitOrder>[0] = {
      symbol, side: submitSide,
      order_type: backendOrderType,
      quantity: baseQty,
      leverage,
      margin_type: marginType,
      position_direction: posDir,
      post_only: isPostOnly,
    }
    if ((orderType === 'LIMIT' || orderType === 'POST_ONLY' || (orderType === 'CONDITIONAL' && condSubType === 'LIMIT')) && price)
      body.price = parseFloat(price)
    if (orderType === 'CONDITIONAL' && stopPrice) body.stop_price = parseFloat(stopPrice)
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
      <TickerStrip onFillMark={fillMarkPrice} />

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
      <div className="flex gap-0 px-3 pt-2 shrink-0 items-center">
        {(['LIMIT', 'MARKET'] as OrderType[]).map(tp => (
          <button key={tp} onClick={() => selectOrderType(tp)}
            className={`py-1 px-2.5 text-[11px] rounded transition-colors mr-1 ${
              orderType === tp
                ? 'text-[#EAECEF] border-b border-[#F0B90B]'
                : 'text-[#848E9C] hover:text-[#EAECEF] border border-transparent'
            }`}>
            {orderTypeLabel(tp)}
          </button>
        ))}
        <div
          className="relative mr-1"
          onMouseEnter={() => setAdvancedOrderTypeOpen(true)}
          onMouseLeave={() => setAdvancedOrderTypeOpen(false)}
        >
          <div className="flex items-center">
            <button
              type="button"
              onClick={() => selectOrderType(lastAdvancedOrderType)}
              className={`py-1 pl-2.5 pr-1.5 text-[11px] rounded-l transition-colors ${
                orderType === 'CONDITIONAL' || orderType === 'POST_ONLY'
                  ? 'text-[#EAECEF] border-b border-[#F0B90B]'
                  : 'text-[#848E9C] hover:text-[#EAECEF] border border-transparent'
              }`}
            >
              {orderTypeLabel(lastAdvancedOrderType)}
            </button>
            <button
              type="button"
              aria-label="Open advanced order types"
              className={`py-1 pl-0.5 pr-2 text-[11px] rounded-r transition-colors ${
                orderType === 'CONDITIONAL' || orderType === 'POST_ONLY'
                  ? 'text-[#EAECEF] border border-transparent'
                  : 'text-[#848E9C] hover:text-[#EAECEF] border border-transparent'
              }`}
            >
              <span className="block text-[9px]">{advancedOrderTypeOpen ? '▲' : '▼'}</span>
            </button>
          </div>
          {advancedOrderTypeOpen && (
            <div className="absolute left-0 top-full z-20 mt-1 min-w-[132px] overflow-hidden rounded border border-[#2B2F36] bg-[#1E2026] shadow-lg">
              {(['CONDITIONAL', 'POST_ONLY'] as OrderType[]).map((tp) => (
                <button
                  key={tp}
                  type="button"
                  onClick={() => selectOrderType(tp)}
                  className={`flex w-full items-center px-3 py-2 text-left text-[11px] transition-colors ${
                    selectedAdvancedOrderType === tp
                      ? 'bg-[#2B3139] text-[#EAECEF]'
                      : 'text-[#C7CCD3] hover:bg-[#2B3139] hover:text-[#EAECEF]'
                  }`}
                >
                  <span>{orderTypeLabel(tp)}</span>
                  <span className={`ml-auto w-4 flex-none text-right ${selectedAdvancedOrderType === tp ? 'text-[#EAECEF]' : 'text-transparent'}`}>✓</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── Form ── */}
      <form onSubmit={e => e.preventDefault()} className="flex-1 overflow-y-auto px-3 pt-3 space-y-2.5 min-h-0">

        {/* Stop trigger price */}
        {orderType === 'CONDITIONAL' && (
          <div>
            <label className="block text-[10px] text-[#848E9C] uppercase tracking-wider mb-1">{t('order.triggerPrice')}</label>
            <div className="relative">
              <input type="number" value={stopPrice} onChange={e => setStopPrice(e.target.value)}
                placeholder="0.00" min="0" step="any"
                className="w-full bg-[#1E2026] border border-[#2B2F36] focus:border-[#F0B90B] text-[13px] text-[#EAECEF] rounded px-2.5 py-1.5 outline-none selectable pr-14" />
              <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-[#848E9C]">{t('order.triggerPriceType.last')}</span>
            </div>
          </div>
        )}

        {/* Price */}
        {(orderType === 'LIMIT' || orderType === 'POST_ONLY' || orderType === 'CONDITIONAL') && (
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-[10px] text-[#848E9C] uppercase tracking-wider">{t('order.price')}</label>
            </div>
            <div className="flex gap-2">
              <div className="relative flex-1 min-w-0">
                <input
                  type={orderType === 'CONDITIONAL' && condSubType === 'MARKET' ? 'text' : 'number'}
                  value={orderType === 'CONDITIONAL' && condSubType === 'MARKET' ? '' : price}
                  onChange={e => setPrice(e.target.value)}
                  placeholder={orderType === 'CONDITIONAL' && condSubType === 'MARKET' ? t('order.marketPrice') : '0.00'}
                  min="0"
                  step="any"
                  disabled={orderType === 'CONDITIONAL' && condSubType === 'MARKET'}
                  className={`w-full rounded px-2.5 py-1.5 text-[13px] outline-none pr-14 ${
                    orderType === 'CONDITIONAL' && condSubType === 'MARKET'
                      ? 'bg-[#2B2F36] border border-[#2B2F36] text-[#848E9C] cursor-not-allowed'
                      : 'bg-[#1E2026] border border-[#2B2F36] focus:border-[#F0B90B] text-[#EAECEF] selectable'
                  }`}
                />
                <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-[#848E9C]">{quoteAsset}</span>
              </div>
              {orderType === 'CONDITIONAL' && (
                <select
                  value={condSubType}
                  onChange={e => setCondSubType(e.target.value as CondSubType)}
                  className="w-[92px] shrink-0 bg-[#0B0E11] border border-[#2B2F36] text-[#EAECEF] text-[11px] rounded px-2 py-1.5 outline-none cursor-pointer"
                >
                  <option value="LIMIT">{t('order.limit')}</option>
                  <option value="MARKET">{t('order.market')}</option>
                </select>
              )}
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

        {posDir === 'CLOSE' && closeEstimate && (
          <div className="rounded border border-[#2B2F36] bg-[#161A1E] px-2.5 py-2 text-[10px]">
            <div className="mb-1 flex items-center justify-between gap-2">
              <span className="text-[#848E9C]">{t('order.estimatedClosePnl')}</span>
              <span className="font-mono tabular-nums text-[#EAECEF]">{fmt(closeEstimate.price, 2)} {quoteAsset}</span>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-[#848E9C]">{t('order.closeLong')}</span>
              <span className={`font-mono tabular-nums ${
                closeEstimate.longPnl == null ? 'text-[#848E9C]' : closeEstimate.longPnl >= 0 ? 'text-[#0ECB81]' : 'text-[#F6465D]'
              }`}>
                {closeEstimate.longPnl == null
                  ? '—'
                  : `${closeEstimate.longPnl >= 0 ? t('order.takeProfit') : t('order.stopLoss')} ${fmtSigned(closeEstimate.longPnl, 2)} ${quoteAsset}`}
              </span>
            </div>
            <div className="mt-0.5 flex items-center justify-between gap-2">
              <span className="text-[#848E9C]">{t('order.closeShort')}</span>
              <span className={`font-mono tabular-nums ${
                closeEstimate.shortPnl == null ? 'text-[#848E9C]' : closeEstimate.shortPnl >= 0 ? 'text-[#0ECB81]' : 'text-[#F6465D]'
              }`}>
                {closeEstimate.shortPnl == null
                  ? '—'
                  : `${closeEstimate.shortPnl >= 0 ? t('order.takeProfit') : t('order.stopLoss')} ${fmtSigned(closeEstimate.shortPnl, 2)} ${quoteAsset}`}
              </span>
            </div>
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
              {tpEstimate && (
                <div className="mt-1 rounded border border-[#2B2F36] bg-[#161A1E] px-2 py-1.5 text-[10px]">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[#848E9C]">{t('order.buyPnl')}</span>
                    <span className={`font-mono tabular-nums ${tpEstimate.buyPnl >= 0 ? 'text-[#0ECB81]' : 'text-[#F6465D]'}`}>
                      {fmtSigned(tpEstimate.buyPnl, 2)} {quoteAsset}
                      {tpEstimate.buyPct != null ? ` (${fmtSigned(tpEstimate.buyPct, 2)}%)` : ''}
                    </span>
                  </div>
                  <div className="mt-0.5 flex items-center justify-between gap-2">
                    <span className="text-[#848E9C]">{t('order.sellPnl')}</span>
                    <span className={`font-mono tabular-nums ${tpEstimate.sellPnl >= 0 ? 'text-[#0ECB81]' : 'text-[#F6465D]'}`}>
                      {fmtSigned(tpEstimate.sellPnl, 2)} {quoteAsset}
                      {tpEstimate.sellPct != null ? ` (${fmtSigned(tpEstimate.sellPct, 2)}%)` : ''}
                    </span>
                  </div>
                </div>
              )}
            </div>
            <div>
              <label className="block text-[10px] text-[#848E9C] uppercase tracking-wider mb-1">{t('order.stopLoss')}</label>
              <div className="relative">
                <input type="number" value={sl} onChange={e => setSl(e.target.value)}
                  placeholder="0.00" min="0" step="any"
                  className="w-full bg-[#1E2026] border border-[#2B2F36] focus:border-[#F6465D] text-[13px] text-[#EAECEF] rounded px-2.5 py-1.5 outline-none selectable pr-10" />
                <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-[#F6465D]">SL</span>
              </div>
              {slEstimate && (
                <div className="mt-1 rounded border border-[#2B2F36] bg-[#161A1E] px-2 py-1.5 text-[10px]">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[#848E9C]">{t('order.buyPnl')}</span>
                    <span className={`font-mono tabular-nums ${slEstimate.buyPnl >= 0 ? 'text-[#0ECB81]' : 'text-[#F6465D]'}`}>
                      {fmtSigned(slEstimate.buyPnl, 2)} {quoteAsset}
                      {slEstimate.buyPct != null ? ` (${fmtSigned(slEstimate.buyPct, 2)}%)` : ''}
                    </span>
                  </div>
                  <div className="mt-0.5 flex items-center justify-between gap-2">
                    <span className="text-[#848E9C]">{t('order.sellPnl')}</span>
                    <span className={`font-mono tabular-nums ${slEstimate.sellPnl >= 0 ? 'text-[#0ECB81]' : 'text-[#F6465D]'}`}>
                      {fmtSigned(slEstimate.sellPnl, 2)} {quoteAsset}
                      {slEstimate.sellPct != null ? ` (${fmtSigned(slEstimate.sellPct, 2)}%)` : ''}
                    </span>
                  </div>
                </div>
              )}
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
            <button
              type="button"
              onClick={onLoginClick}
              className="col-span-2 flex items-center justify-center gap-1.5 py-1.5 rounded bg-[#F0B90B]/10 border border-[#F0B90B]/40 hover:bg-[#F0B90B]/15 hover:border-[#F0B90B]/60 transition-colors"
            >
              <span className="text-[#F0B90B] text-[11px]">⚠</span>
              <span className="text-[12px] font-medium text-[#F0B90B]">{t('order.loginRequired')}</span>
            </button>
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
