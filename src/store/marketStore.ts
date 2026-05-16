import { create } from 'zustand'

function normalizeSymbol(symbol: string): string {
  return symbol.trim().toUpperCase()
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

export type MarketEvent = KlineData | MarkPriceData | TradeData

interface MarketStore {
  symbol: string
  currentPrice: number | null
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

export const useMarketStore = create<MarketStore>((set) => ({
  symbol: 'BTCUSDC',
  currentPrice: null,
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
    if (!nextSymbol || nextSymbol === state.symbol) return {}
    return {
      symbol: nextSymbol,
      currentPrice: null,
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
    if (!Number.isFinite(price) || !nextSymbol || nextSymbol !== state.symbol) return {}
    return { currentPrice: price, isConnected: true }
  }),
  setChartInterval: (interval) => set({ chartInterval: interval }),
  setChartExpanded: (v) => set({ isChartExpanded: v }),

  processMarketEvent: (event) => {
    const eventSymbol = normalizeSymbol(event.symbol)
    if (event.type === 'kline') {
      const kline = event as KlineData
      set((state) => {
        if (eventSymbol !== state.symbol) return {}
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
          isConnected: true,
        }
      })
    } else if (event.type === 'markPrice') {
      const mp = event as MarkPriceData
      set((state) => {
        if (eventSymbol !== state.symbol) return {}
        return { markPrice: mp.markPrice, fundingRate: mp.fundingRate, nextFundingTime: mp.nextFundingTime, isConnected: true }
      })
    } else if (event.type === 'trade') {
      const trade = event as TradeData
      set((state) => ({
        ...(eventSymbol !== state.symbol ? {} : {
        recentTrades: [trade, ...state.recentTrades].slice(0, 50),
        currentPrice: trade.price,
        isConnected: true,
        }),
      }))
    }
  },
}))
