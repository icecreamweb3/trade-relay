import { create } from 'zustand'

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
  klines: OHLCVBar[]
  latestKline: KlineData | null
  recentTrades: TradeData[]
  isConnected: boolean
  chartInterval: string
  isChartExpanded: boolean

  setSymbol: (symbol: string) => void
  setConnected: (connected: boolean) => void
  setChartInterval: (interval: string) => void
  setChartExpanded: (v: boolean) => void
  processMarketEvent: (event: MarketEvent) => void
}

export const useMarketStore = create<MarketStore>((set) => ({
  symbol: 'BTCUSDT',
  currentPrice: null,
  markPrice: null,
  fundingRate: null,
  klines: [],
  latestKline: null,
  recentTrades: [],
  isConnected: false,
  chartInterval: '15m',
  isChartExpanded: false,

  setSymbol: (symbol) => set({ symbol }),
  setConnected: (connected) => set({ isConnected: connected }),
  setChartInterval: (interval) => set({ chartInterval: interval }),
  setChartExpanded: (v) => set({ isChartExpanded: v }),

  processMarketEvent: (event) => {
    if (event.type === 'kline') {
      const kline = event as KlineData
      set((state) => {
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
      set({ markPrice: mp.markPrice, fundingRate: mp.fundingRate, isConnected: true })
    } else if (event.type === 'trade') {
      const trade = event as TradeData
      set((state) => ({
        recentTrades: [trade, ...state.recentTrades].slice(0, 50),
        currentPrice: trade.price,
        isConnected: true,
      }))
    }
  },
}))
