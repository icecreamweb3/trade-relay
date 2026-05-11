import { useEffect } from 'react'
import { useMarketStore, MarketEvent } from '../store/marketStore'

export function useMarketData() {
  const { processMarketEvent, setSymbol, setChartInterval, setChartExpanded } = useMarketStore()

  useEffect(() => {
    const cleanups: (() => void)[] = []

    if (!window.electronAPI) return

    const unsubMarket = window.electronAPI.onMarketData?.((data: MarketEvent) => {
      processMarketEvent(data)
    })
    if (unsubMarket) cleanups.push(unsubMarket)

    const unsubSymbol = window.electronAPI.onSymbolChange?.((symbol: string) => {
      setSymbol(symbol)
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
}
