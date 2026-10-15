import { create } from 'zustand'

function normalizeSymbol(symbol: string): string {
  return symbol.trim().toUpperCase()
}

const TRADE_UI_PUBLISH_INTERVAL_MS = 100
const latestTradePriceBySymbol = new Map<string, number>()
let pendingTrade: TradeData | null = null
let pendingTradeTimer: ReturnType<typeof setTimeout> | null = null

export function getLatestTradePrice(symbol: string): number | null {
  const price = latestTradePriceBySymbol.get(normalizeSymbol(symbol))
  return price != null && Number.isFinite(price) && price > 0 ? price : null
}

function clearPendingTrade() {
  pendingTrade = null
  if (pendingTradeTimer != null) {
    clearTimeout(pendingTradeTimer)
    pendingTradeTimer = null
  }
}

export interface OHLCVBar {
  time: number; open: number; high: number; low: number; close: number; volume: number
}

export interface KlineData {
  type: 'kline'
  symbol: string; interval: string; openTime: number
  open: number; high: number; low: number; close: number; volume: number
  closeTime: number; isClosed: boolean
}

export interface MarkPriceData {
  type: 'markPrice'
  symbol: string; markPrice: number; indexPrice: number
  fundingRate: number; nextFundingTime: number; timestamp: number
}

export interface TradeData {
  type: 'trade'
  symbol: string; price: number; quantity: number; isBuyerMaker: boolean; timestamp: number
}

export interface Ticker24hData {
  type: 'ticker24h'
  symbol: string
  lastPrice: number
  priceChange: number
  priceChangePercent: number
  openPrice: number
  highPrice: number
  lowPrice: number
  volume: number
  quoteVolume: number
  openTime: number
  closeTime: number
  eventTime: number
}

export type MarketEvent = KlineData | MarkPriceData | TradeData | Ticker24hData

function deriveDayChange(currentPrice: number | null, dayOpenPrice: number | null) {
  if (currentPrice == null || dayOpenPrice == null || !Number.isFinite(currentPrice) || !Number.isFinite(dayOpenPrice) || dayOpenPrice === 0) {
    return { dayPriceChange: null, dayPriceChangePercent: null }
  }

  const dayPriceChange = currentPrice - dayOpenPrice
  const dayPriceChangePercent = dayPriceChange / dayOpenPrice * 100
  return { dayPriceChange, dayPriceChangePercent }
}

interface MarketStore {
  symbol: string
  currentPrice: number | null
  dayOpenPrice: number | null
  dayPriceChange: number | null
  dayPriceChangePercent: number | null
  markPrice: number | null
  fundingRate: number | null
  nextFundingTime: number | null
  klines: OHLCVBar[]
  latestKline: KlineData | null
  recentTrades: TradeData[]
  isConnected: boolean
  chartInterval: string
  isChartExpanded: boolean

  setSymbol: (symbol: string) => void
  setConnected: (connected: boolean) => void
  setCurrentPrice: (symbol: string, price: number) => void
  setChartInterval: (interval: string) => void
  setChartExpanded: (v: boolean) => void
  processMarketEvent: (event: MarketEvent) => void
}

export const useMarketStore = create<MarketStore>((set, get) => ({
  symbol: 'BTCUSDC',
  currentPrice: null,
  dayOpenPrice: null,
  dayPriceChange: null,
  dayPriceChangePercent: null,
  markPrice: null,
  fundingRate: null,
  nextFundingTime: null,
  klines: [],
  latestKline: null,
  recentTrades: [],
  isConnected: false,
  chartInterval: '15m',
  isChartExpanded: false,

  setSymbol: (symbol) => set((state) => {
    const nextSymbol = normalizeSymbol(symbol)
    if (!nextSymbol || nextSymbol === state.symbol) return state
    clearPendingTrade()
    return {
      symbol: nextSymbol,
      currentPrice: null,
      dayOpenPrice: null,
      dayPriceChange: null,
      dayPriceChangePercent: null,
      markPrice: null,
      fundingRate: null,
      nextFundingTime: null,
      klines: [],
      latestKline: null,
      recentTrades: [],
      isConnected: false,
    }
  }),
  setConnected: (connected) => set({ isConnected: connected }),
  setCurrentPrice: (symbol, price) => set((state) => {
    const nextSymbol = normalizeSymbol(symbol)
    if (!Number.isFinite(price) || !nextSymbol || nextSymbol !== state.symbol) return state
    latestTradePriceBySymbol.set(nextSymbol, price)
    return {
      currentPrice: price,
      ...deriveDayChange(price, state.dayOpenPrice),
      isConnected: true,
    }
  }),
  setChartInterval: (interval) => set({ chartInterval: interval }),
  setChartExpanded: (v) => set({ isChartExpanded: v }),

  processMarketEvent: (event) => {
    const eventSymbol = normalizeSymbol(event.symbol)
    if (event.type === 'kline') {
      const kline = event as KlineData
      set((state) => {
        if (eventSymbol !== state.symbol) return state
        const newBar: OHLCVBar = {
          time: kline.openTime, open: kline.open, high: kline.high,
          low: kline.low, close: kline.close, volume: kline.volume,
        }
        let klines = [...state.klines]
        if (kline.isClosed) {
          klines = [...klines, newBar].slice(-200)
        } else {
          const last = klines[klines.length - 1]
          if (last && last.time === newBar.time) {
            klines = [...klines.slice(0, -1), newBar]
          } else {
            klines = [...klines, newBar].slice(-200)
          }
        }
        return {
          klines,
          latestKline: kline,
          currentPrice: kline.close,
          ...deriveDayChange(kline.close, state.dayOpenPrice),
          isConnected: true,
        }
      })
    } else if (event.type === 'markPrice') {
      const mp = event as MarkPriceData
      set((state) => {
        if (eventSymbol !== state.symbol) return state
        return { markPrice: mp.markPrice, fundingRate: mp.fundingRate, nextFundingTime: mp.nextFundingTime, isConnected: true }
      })
    } else if (event.type === 'trade') {
      const trade = event as TradeData
      if (!Number.isFinite(trade.price) || trade.price <= 0) return
      latestTradePriceBySymbol.set(eventSymbol, trade.price)
      if (eventSymbol !== get().symbol) return
      pendingTrade = trade
      if (pendingTradeTimer != null) return

      pendingTradeTimer = setTimeout(() => {
        const nextTrade = pendingTrade
        pendingTrade = null
        pendingTradeTimer = null
        if (!nextTrade) return

        const nextSymbol = normalizeSymbol(nextTrade.symbol)
        set((state) => {
          if (nextSymbol !== state.symbol) return state
          return {
            recentTrades: [nextTrade, ...state.recentTrades].slice(0, 50),
            currentPrice: nextTrade.price,
            ...deriveDayChange(nextTrade.price, state.dayOpenPrice),
            isConnected: true,
          }
        })
      }, TRADE_UI_PUBLISH_INTERVAL_MS)
    } else if (event.type === 'ticker24h') {
      const ticker = event as Ticker24hData
      set((state) => {
        if (eventSymbol !== state.symbol) return state
        const nextCurrentPrice = state.currentPrice ?? ticker.lastPrice
        return {
          currentPrice: nextCurrentPrice,
          dayOpenPrice: ticker.openPrice,
          ...deriveDayChange(nextCurrentPrice, ticker.openPrice),
          isConnected: true,
        }
      })
    }
  },
}))
