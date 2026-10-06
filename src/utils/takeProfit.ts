const AUTO_TAKE_PROFIT_R_MULTIPLE = 2

export interface TakeProfitPosition {
  side: string
  entry_price: number | null
}

export function calculateTwoRTakeProfit(
  position: TakeProfitPosition,
  stopPrice: number | null,
  minimumProfitPoints: number,
): number | null {
  const entryPrice = position.entry_price
  if (entryPrice == null || !Number.isFinite(entryPrice) || entryPrice <= 0) return null
  if (stopPrice == null || !Number.isFinite(stopPrice) || stopPrice <= 0) return null
  if (!Number.isFinite(minimumProfitPoints) || minimumProfitPoints < 0) return null

  const riskDistance = Math.abs(entryPrice - stopPrice)
  if (riskDistance <= 0) return null
  const profitDistance = Math.max(AUTO_TAKE_PROFIT_R_MULTIPLE * riskDistance, minimumProfitPoints)

  if (position.side === 'LONG') return entryPrice + profitDistance
  if (position.side === 'SHORT') {
    const takeProfit = entryPrice - profitDistance
    return takeProfit > 0 ? takeProfit : null
  }
  return null
}
