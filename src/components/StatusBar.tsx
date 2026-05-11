import { useMarketStore } from '../store/marketStore'

export function StatusBar() {
  const { symbol, currentPrice, markPrice, fundingRate, isConnected, klines } = useMarketStore()

  const lastKline = klines[klines.length - 1]
  const prevKline = klines[klines.length - 2]
  const priceChange = lastKline && prevKline ? lastKline.close - prevKline.close : 0
  const priceChangePct = prevKline ? (priceChange / prevKline.close) * 100 : 0
  const isUp = priceChange >= 0

  return (
    <div className="h-6 bg-[#007acc] flex items-center px-3 gap-4 text-xs text-white select-none shrink-0">
      <span className={`flex items-center gap-1 ${isConnected ? '' : 'opacity-60'}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-green-300' : 'bg-yellow-300'}`} />
        {isConnected ? '实时' : '未连接'}
      </span>
      <span className="text-blue-200">|</span>
      <span className="font-semibold">{symbol}</span>
      {currentPrice !== null && (
        <>
          <span className="font-mono font-bold">{currentPrice.toFixed(2)}</span>
          <span className={isUp ? 'text-green-300' : 'text-red-300'}>
            {isUp ? '+' : ''}{priceChange.toFixed(2)} ({priceChangePct.toFixed(2)}%)
          </span>
        </>
      )}
      {markPrice !== null && (
        <><span className="text-blue-200">|</span><span className="text-blue-100">标记: <span className="font-mono">{markPrice.toFixed(2)}</span></span></>
      )}
      {fundingRate !== null && (
        <span className={fundingRate >= 0 ? 'text-green-300' : 'text-red-300'}>
          资金费: {(fundingRate * 100).toFixed(4)}%
        </span>
      )}
    </div>
  )
}
