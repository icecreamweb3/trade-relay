import { useEffect, useRef } from 'react'
import { useMarketStore, MarketEvent } from '../store/marketStore'

// ── Direct Binance Futures WebSocket subscription ─────────────────────────────
// Two separate connections:
//   1. markPrice WS  — depends only on symbol, never disrupted by interval change
//   2. kline+trade WS — depends on symbol+interval, reconnects on interval change

const FAPI_BASE = 'https://fapi.binance.com/fapi/v1'
const FSTREAM_BASE = 'wss://fstream.binance.com'

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
  const {
    processMarketEvent,
    setSymbol,
    setChartInterval,
    setChartExpanded,
    symbol,
    chartInterval,
  } = useMarketStore()

  // Stable ref so WS callbacks always call the latest action without re-running effects
  const processRef = useRef(processMarketEvent)
  const lastRateLimitStatusRef = useRef<number | null>(null)
  useEffect(() => { processRef.current = processMarketEvent })

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

  // ── markPrice: REST polling for mark price + funding data ─────────────────
  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | null = null

    async function poll() {
      if (!alive) return
      let delayMs = 15000
      try {
        const resp = await fetch(`${FAPI_BASE}/premiumIndex?symbol=${symbol}`)
        if (resp.ok) {
          lastRateLimitStatusRef.current = null
          const d = await resp.json()
          const markPrice = parseFloat(d.markPrice)
          const indexPrice = parseFloat(d.indexPrice)
          const fundingRate = parseFloat(d.lastFundingRate)
          const nextFundingTime = d.nextFundingTime as number
          processRef.current({ type: 'markPrice', symbol: d.symbol, markPrice, indexPrice, fundingRate, nextFundingTime, timestamp: Date.now() })
        } else {
          if (resp.status === 429 || resp.status === 418) {
            delayMs = resp.status === 418 ? 300000 : 60000
            if (lastRateLimitStatusRef.current !== resp.status) {
              window.electronAPI?.logToMain?.('warn', `markPrice REST HTTP ${resp.status} for ${symbol}; backing off to ${Math.round(delayMs / 1000)}s`)
              lastRateLimitStatusRef.current = resp.status
            }
          } else {
            window.electronAPI?.logToMain?.('warn', `markPrice REST HTTP ${resp.status} for ${symbol}`)
          }
        }
      } catch (e) {
        window.electronAPI?.logToMain?.('warn', `markPrice REST error: ${e}`)
      }
      if (alive) timer = setTimeout(poll, delayMs)
    }

    poll()
    return () => { alive = false; if (timer) clearTimeout(timer) }
  }, [symbol])

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

    return cleanup
  }, [symbol, chartInterval])
}
