import { useEffect, useRef } from 'react'
import { useMarketStore, MarketEvent } from '../store/marketStore'

// ── Direct Binance Futures WebSocket subscription ─────────────────────────────
// Connects to wss://fstream.binance.com and subscribes to markPrice + kline
// streams for the current symbol. This provides real-time data independently
// of the BrowserView IPC path, so the right panel always shows live prices.

const FSTREAM = 'wss://fstream.binance.com/stream'

function buildStreamUrl(symbol: string, interval: string): string {
  const s = symbol.toLowerCase()
  // Combined stream: markPrice (1s) + kline + aggTrade
  const streams = [
    `${s}@markPrice@1s`,
    `${s}@kline_${interval}`,
    `${s}@aggTrade`,
  ].join('/')
  return `${FSTREAM}?streams=${streams}`
}

export function useMarketData() {
  const {
    processMarketEvent,
    setSymbol,
    setChartInterval,
    setChartExpanded,
    symbol,
    chartInterval,
  } = useMarketStore()

  // ── Electron IPC bridge (BrowserView data) ────────────────────────────────
  useEffect(() => {
    const cleanups: (() => void)[] = []
    if (!window.electronAPI) return

    const unsubMarket = window.electronAPI.onMarketData?.((data: MarketEvent) => {
      processMarketEvent(data)
    })
    if (unsubMarket) cleanups.push(unsubMarket)

    const unsubSymbol = window.electronAPI.onSymbolChange?.((sym: string) => {
      setSymbol(sym)
    })
    if (unsubSymbol) cleanups.push(unsubSymbol)

    const unsubInterval = window.electronAPI.onIntervalChange?.((interval: string) => {
      setChartInterval(interval)
    })
    if (unsubInterval) cleanups.push(unsubInterval)

    const unsubExpand = window.electronAPI.onChartExpandChange?.((expanded: boolean) => {
      setChartExpanded(expanded)
    })
    if (unsubExpand) cleanups.push(unsubExpand)

    return () => cleanups.forEach(fn => fn())
  }, [processMarketEvent, setSymbol, setChartInterval, setChartExpanded])

  // ── Direct Binance WS (always-on fallback / primary source) ──────────────
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const unmounted = useRef(false)

  useEffect(() => {
    unmounted.current = false

    function connect() {
      if (unmounted.current) return
      const url = buildStreamUrl(symbol, chartInterval)
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data as string)
          const data = msg.data ?? msg
          if (!data?.e) return

          if (data.e === 'markPriceUpdate') {
            const payload = {
              type: 'markPrice' as const,
              symbol: data.s,
              markPrice: parseFloat(data.p),
              indexPrice: parseFloat(data.i),
              fundingRate: parseFloat(data.r),
              nextFundingTime: data.T,
              timestamp: data.E,
            }
            console.log(
              `[WS markPriceUpdate] symbol=${payload.symbol}`,
              `markPrice=${payload.markPrice}`,
              `indexPrice=${payload.indexPrice}`,
              `fundingRate=${(payload.fundingRate * 100).toFixed(4)}%`,
              `nextFundingTime=${new Date(payload.nextFundingTime).toISOString()}`,
            )
            processMarketEvent(payload)
          } else if (data.e === 'kline' && data.k) {
            const k = data.k
            processMarketEvent({
              type: 'kline',
              symbol: data.s,
              interval: k.i,
              openTime: k.t,
              open: parseFloat(k.o),
              high: parseFloat(k.h),
              low: parseFloat(k.l),
              close: parseFloat(k.c),
              volume: parseFloat(k.v),
              closeTime: k.T,
              isClosed: k.x,
            })
          } else if (data.e === 'aggTrade') {
            processMarketEvent({
              type: 'trade',
              symbol: data.s,
              price: parseFloat(data.p),
              quantity: parseFloat(data.q),
              isBuyerMaker: data.m,
              timestamp: data.T,
            })
          }
        } catch { /* ignore parse errors */ }
      }

      ws.onclose = () => {
        if (unmounted.current) return
        // Reconnect after 3s on unexpected close
        reconnectTimer.current = setTimeout(connect, 3000)
      }

      ws.onerror = () => {
        ws.close()
      }
    }

    connect()

    return () => {
      unmounted.current = true
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [symbol, chartInterval, processMarketEvent])
}
