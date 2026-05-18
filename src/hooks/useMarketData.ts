import { useEffect, useRef } from 'react'
import { useMarketStore, MarketEvent } from '../store/marketStore'
import { api, type ApiOrderMarker } from '../api/client'
import { useAuthStore } from '../store/authStore'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'

// ── Direct Binance Futures WebSocket subscription ─────────────────────────────
// One direct connection is kept here for kline + trade data.
// Mark price / funding REST polling was removed to reduce extra Binance requests.

const FSTREAM_BASE = 'wss://fstream.binance.com'

interface ChartOverlaySignal {
  direction: 'LONG' | 'SHORT'
  trade_action: 'OPEN' | 'CLOSE'
  show_label: boolean
  timestamp: string
  entry_price: number
  quantity: number
  bar_low: number
  bar_high: number
}

function mapOrderMarkersToOverlaySignals(markers: ApiOrderMarker[], showLabels: boolean): ChartOverlaySignal[] {
  return markers
    .filter((marker) => Number.isFinite(marker.avg_price) && marker.avg_price > 0 && Boolean(marker.created_at))
    .sort((left, right) => Date.parse(left.created_at) - Date.parse(right.created_at))
    .map((marker) => ({
      direction: String(marker.side).toUpperCase() === 'SELL' ? 'SHORT' : 'LONG',
      trade_action: String(marker.trade_direction).toUpperCase() === 'CLOSE' ? 'CLOSE' : 'OPEN',
      show_label: showLabels,
      timestamp: marker.created_at,
      entry_price: Number(marker.avg_price),
      quantity: Number(marker.filled_qty),
      bar_low: Number(marker.avg_price),
      bar_high: Number(marker.avg_price),
    }))
}

function makeWs(
  url: string,
  onMsg: (data: unknown) => void,
  label: string,
): () => void {
  let alive = true
  let ws: WebSocket
  let retryTimer: ReturnType<typeof setTimeout> | null = null

  function connect() {
    if (!alive) return
    ws = new WebSocket(url)
    let msgCount = 0
    let noMsgTimer: ReturnType<typeof setTimeout> | null = null

    ws.onopen = () => {
      console.log(`[useMarketData] ${label} connected`)
      window.electronAPI?.logToMain?.('info', `${label} connected: ${url}`)
      // Warn if no messages arrive within 5 seconds of connecting
      noMsgTimer = setTimeout(() => {
        if (alive && msgCount === 0) {
          const warn = `${label} connected but NO messages received after 5s`
          console.warn('[useMarketData]', warn)
          window.electronAPI?.logToMain?.('warn', warn)
        }
      }, 5000)
    }

    ws.onmessage = (e) => {
      if (!alive) return
      msgCount++
      if (noMsgTimer) { clearTimeout(noMsgTimer); noMsgTimer = null }
      try {
        const msg = JSON.parse(e.data as string)
        onMsg(msg)
      } catch { /* ignore */ }
    }

    ws.onerror = () => {
      if (noMsgTimer) { clearTimeout(noMsgTimer); noMsgTimer = null }
      console.warn(`[useMarketData] ${label} error`)
      window.electronAPI?.logToMain?.('warn', `${label} error: ${url}`)
      ws.close()
    }

    ws.onclose = (ev) => {
      if (noMsgTimer) { clearTimeout(noMsgTimer); noMsgTimer = null }
      if (!alive) return
      const msg = `${label} closed (code=${ev.code}), reconnecting in 3s`
      console.warn('[useMarketData]', msg)
      window.electronAPI?.logToMain?.('warn', msg)
      retryTimer = setTimeout(connect, 3000)
    }
  }

  connect()

  return () => {
    alive = false
    if (retryTimer) clearTimeout(retryTimer)
    try { ws?.close() } catch { /* ignore */ }
  }
}

export function useMarketData() {
  const user = useAuthStore((state) => state.user)
  const chartOrderMarkersVisible = useUiPreferencesStore((state) => state.chartOrderMarkersVisible)
  const chartOrderMarkerLabelsVisible = useUiPreferencesStore((state) => state.chartOrderMarkerLabelsVisible)
  const {
    processMarketEvent,
    setSymbol,
    setChartInterval,
    setChartExpanded,
    symbol,
    chartInterval,
    currentPrice,
    dayOpenPrice,
    dayPriceChange,
    dayPriceChangePercent,
  } = useMarketStore()

  // Stable ref so WS callbacks always call the latest action without re-running effects
  const processRef = useRef(processMarketEvent)
  useEffect(() => { processRef.current = processMarketEvent })
  const baselineLoggedSymbolRef = useRef<string | null>(null)
  const derivedLoggedSymbolRef = useRef<string | null>(null)

  const syncSymbolFromEvent = (event: { symbol?: string } | null | undefined) => {
    const nextSymbol = event?.symbol?.toUpperCase()
    if (!nextSymbol) return
    const currentSymbol = useMarketStore.getState().symbol
    if (nextSymbol !== currentSymbol) {
      setSymbol(nextSymbol)
    }
  }

  // ── Electron IPC bridge (BrowserView secondary source) ────────────────────
  useEffect(() => {
    const cleanups: (() => void)[] = []
    if (!window.electronAPI) return

    const unsubMarket = window.electronAPI.onMarketData?.((data: MarketEvent) => {
      syncSymbolFromEvent(data)
      processRef.current(data)
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
  }, [setSymbol, setChartInterval, setChartExpanded])

  // ── kline + aggTrade: combined stream with depth20 keep-alive ────────────
  // Including @depth20@100ms forces the proxy to flush TCP buffers every 100ms,
  // ensuring kline and aggTrade frames are not stuck in proxy buffers.
  useEffect(() => {
    const s = symbol.toLowerCase()
    const streams = [
      `${s}@kline_${chartInterval}`,
      `${s}@aggTrade`,
      `${s}@depth20@100ms`,   // keep-alive: 10 msgs/sec forces proxy flush
    ].join('/')
    const url = `${FSTREAM_BASE}/stream?streams=${streams}`

    const cleanup = makeWs(url, (raw) => {
      const envelope = raw as Record<string, unknown>
      // Combined stream wraps: { stream: "...", data: { e: "...", ... } }
      const data = (envelope.data ?? envelope) as Record<string, unknown>
      if (!data?.e) return

      if (data.e === 'kline' && data.k) {
        const k = data.k as Record<string, unknown>
        processRef.current({
          type: 'kline',
          symbol: data.s as string,
          interval: k.i as string,
          openTime: k.t as number,
          open: parseFloat(k.o as string),
          high: parseFloat(k.h as string),
          low: parseFloat(k.l as string),
          close: parseFloat(k.c as string),
          volume: parseFloat(k.v as string),
          closeTime: k.T as number,
          isClosed: k.x as boolean,
        })
      } else if (data.e === 'aggTrade') {
        processRef.current({
          type: 'trade',
          symbol: data.s as string,
          price: parseFloat(data.p as string),
          quantity: parseFloat(data.q as string),
          isBuyerMaker: data.m as boolean,
          timestamp: data.T as number,
        })
      }
      // depth20 frames are intentionally discarded (OrderBook has its own WS)
    }, `kline+trade(${symbol})`)

    return () => {
      cleanup()
    }
  }, [symbol, chartInterval])

  // ── 24h ticker REST hydration ────────────────────────────────────────────
  // Use REST to get the 24h open price once on symbol change. Real-time 24h
  // change values are then derived from live currentPrice updates locally.
  useEffect(() => {
    let alive = true
    baselineLoggedSymbolRef.current = null
    derivedLoggedSymbolRef.current = null

    const loadTicker24h = async () => {
      try {
        const response = await fetch(`https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=${symbol.toUpperCase()}`)
        if (!response.ok) return
        const data = await response.json() as Record<string, unknown>
        if (!alive || String(data.symbol || '').toUpperCase() !== symbol.toUpperCase()) return

        const openPrice = parseFloat(String(data.openPrice ?? '0'))
        const lastPrice = parseFloat(String(data.lastPrice ?? '0'))
        if (baselineLoggedSymbolRef.current !== symbol.toUpperCase()) {
          baselineLoggedSymbolRef.current = symbol.toUpperCase()
          window.electronAPI?.logToMain?.('info', '24h auto-calc baseline loaded', {
            symbol: symbol.toUpperCase(),
            source: 'rest-24hr',
            openPrice,
            lastPrice,
          })
        }

        processRef.current({
          type: 'ticker24h',
          symbol: String(data.symbol || symbol),
          lastPrice,
          priceChange: parseFloat(String(data.priceChange ?? '0')),
          priceChangePercent: parseFloat(String(data.priceChangePercent ?? '0')),
          openPrice,
          highPrice: parseFloat(String(data.highPrice ?? '0')),
          lowPrice: parseFloat(String(data.lowPrice ?? '0')),
          volume: parseFloat(String(data.volume ?? '0')),
          quoteVolume: parseFloat(String(data.quoteVolume ?? '0')),
          openTime: Number(data.openTime ?? 0),
          closeTime: Number(data.closeTime ?? 0),
          eventTime: Date.now(),
        })
      } catch {
        // Ignore fallback fetch failures; live price updates may still arrive.
      }
    }

    void loadTicker24h()

    return () => {
      alive = false
    }
  }, [symbol])

  useEffect(() => {
    if (
      !symbol ||
      dayOpenPrice == null ||
      currentPrice == null ||
      dayPriceChange == null ||
      dayPriceChangePercent == null
    ) {
      return
    }

    const normalizedSymbol = symbol.toUpperCase()
    if (derivedLoggedSymbolRef.current === normalizedSymbol) {
      return
    }

    derivedLoggedSymbolRef.current = normalizedSymbol
    window.electronAPI?.logToMain?.('info', '24h auto-calc derived from live price', {
      symbol: normalizedSymbol,
      source: 'local-derived',
      openPrice: dayOpenPrice,
      currentPrice,
      dayPriceChange,
      dayPriceChangePercent,
    })
  }, [symbol, dayOpenPrice, currentPrice, dayPriceChange, dayPriceChangePercent])

  useEffect(() => {
    const clearOverlay = async () => {
      try {
        await window.electronAPI?.clearChartOverlaySignals?.()
      } catch {
        // Ignore overlay clear errors; chart may still be loading.
      }
    }

    if (!user || !symbol || !chartOrderMarkersVisible) {
      void clearOverlay()
      return
    }

    let alive = true
    let requestSequence = 0

    const syncMarkers = async () => {
      requestSequence += 1
      const currentRequest = requestSequence

      try {
        const markers = await api.getOrderMarkers({ symbol, limit: 200 })
        if (!alive || currentRequest !== requestSequence) return

        const signals = mapOrderMarkersToOverlaySignals(markers, chartOrderMarkerLabelsVisible)
        if (signals.length === 0) {
          await clearOverlay()
          return
        }

        await window.electronAPI?.setChartOverlaySignals?.(signals)
      } catch (error) {
        if (!alive || currentRequest !== requestSequence) return
        window.electronAPI?.logToMain?.('warn', 'load chart order markers failed', {
          symbol,
          chartInterval,
          userId: user.id,
          labelsVisible: chartOrderMarkerLabelsVisible,
          error: error instanceof Error ? error.message : String(error),
        })
      }
    }

    void syncMarkers()
    const timer = setInterval(() => {
      void syncMarkers()
    }, 15000)

    return () => {
      alive = false
      requestSequence += 1
      clearInterval(timer)
    }
  }, [user, symbol, chartInterval, chartOrderMarkersVisible, chartOrderMarkerLabelsVisible])
}
