/**
 * 交易分析：把订单记录配对成「完整交易」（开仓 → 加仓/分批减仓 → 仓位归零），
 * 并汇总总体交易情况。纯函数，不依赖 React。
 */
import { parseUtcTimestamp } from './datetime'

export interface OrderLike {
  id: number
  username?: string
  symbol: string
  side: string
  trade_direction?: string | null
  filled_qty?: number
  avg_price?: number | null
  realized_pnl?: number | null
  commission?: number | null
  commission_asset?: string | null
  created_at?: string
  updated_at?: string | null
  filled_at?: string | null
}

export interface AnalyzedTrade {
  username: string
  symbol: string
  entryTimes: string[]
  exitTimes: string[]
  /** 峰值持仓数量（该笔交易累计开仓成交量） */
  quantity: number
  /** 该笔完整交易的已实现盈亏合计 */
  pnl: number
  /** 该笔交易的手续费（按币种聚合） */
  commissions: Record<string, number>
}

export interface TradeAnalysis {
  /** 已成交订单数（含未配成完整交易的） */
  fillCount: number
  /** 完整交易笔数 */
  tradeCount: number
  /** 交易金额：所有成交单 filled_qty × avg_price 之和 */
  totalNotional: number
  /** 总盈利：各完整交易盈亏之和 */
  totalPnl: number
  winCount: number
  lossCount: number
  /** 盈利单数 / 交易次数；无完整交易时为 null */
  winRate: number | null
  maxProfitTrip: AnalyzedTrade | null
  maxLossTrip: AnalyzedTrade | null
  /** 所有成交单的手续费（按币种聚合） */
  commissions: Record<string, number>
}

const EPS = 1e-9

function fillTimeKey(order: OrderLike): number {
  const ts = order.filled_at || order.updated_at || order.created_at
  const parsed = parseUtcTimestamp(ts)
  return parsed ? parsed.getTime() : 0
}

function effectiveTimestamp(order: OrderLike): string {
  return order.filled_at || order.updated_at || order.created_at || ''
}

function addCommission(target: Record<string, number>, asset: string | null | undefined, amount: number | null | undefined) {
  if (amount == null || amount === 0) return
  const key = (asset || '').trim() || '—'
  target[key] = (target[key] ?? 0) + amount
}

interface TripDraft {
  username: string
  symbol: string
  entryTimes: string[]
  exitTimes: string[]
  quantity: number
  pnl: number
  commissions: Record<string, number>
}

/**
 * 单个 (用户, 交易对) 分组内，按时间升序遍历成交单，配对完整交易。
 * 带符号持仓（BUY=+、SELL=−），持仓归零即完成一笔。
 */
function pairGroup(fills: OrderLike[]): AnalyzedTrade[] {
  const trips: AnalyzedTrade[] = []
  let position = 0
  let draft: TripDraft | null = null

  const finishTrip = () => {
    if (draft) trips.push({ ...draft })
    draft = null
    position = 0
  }

  for (const fill of fills) {
    const qty = Number(fill.filled_qty ?? 0)
    if (qty <= 0) continue
    const delta = fill.side === 'BUY' ? qty : -qty
    const time = effectiveTimestamp(fill)

    if (!draft) {
      // 窗口起点在持仓中途出现的孤立平仓，无法配对，跳过
      if (String(fill.trade_direction || '').toUpperCase() === 'CLOSE') continue
      draft = {
        username: fill.username ?? '',
        symbol: fill.symbol,
        entryTimes: time ? [time] : [],
        exitTimes: [],
        quantity: qty,
        pnl: 0,
        commissions: {},
      }
      addCommission(draft.commissions, fill.commission_asset, fill.commission)
      position = delta
      continue
    }

    const sameDirection = Math.sign(delta) === Math.sign(position)
    if (sameDirection) {
      // 加仓
      draft.entryTimes.push(time)
      draft.quantity += qty
      addCommission(draft.commissions, fill.commission_asset, fill.commission)
      position += delta
      continue
    }

    // 减仓 / 平仓 / 反手
    draft.exitTimes.push(time)
    draft.pnl += Number(fill.realized_pnl ?? 0)
    addCommission(draft.commissions, fill.commission_asset, fill.commission)
    const next = position + delta

    if (Math.abs(next) <= EPS) {
      finishTrip()
    } else if (Math.sign(next) === Math.sign(position)) {
      position = next
    } else {
      // 反手：当前交易在 0 点完成，剩余数量开启新一笔
      finishTrip()
      draft = {
        username: fill.username ?? '',
        symbol: fill.symbol,
        entryTimes: time ? [time] : [],
        exitTimes: [],
        quantity: Math.abs(next),
        pnl: 0,
        commissions: {},
      }
      position = next
    }
  }
  // 末尾未归零的持仓属于未完成交易，不计入统计（draft 直接丢弃）
  return trips
}

export function computeTradeAnalysis(orders: OrderLike[]): TradeAnalysis {
  const fills = orders
    .filter((o) => Number(o.filled_qty ?? 0) > 0 && o.avg_price != null)
    .sort((a, b) => fillTimeKey(a) - fillTimeKey(b) || a.id - b.id)

  let totalNotional = 0
  const commissions: Record<string, number> = {}
  for (const fill of fills) {
    totalNotional += Number(fill.filled_qty) * Number(fill.avg_price)
    addCommission(commissions, fill.commission_asset, fill.commission)
  }

  const groups = new Map<string, OrderLike[]>()
  for (const fill of fills) {
    const key = `${fill.username ?? ''}${fill.symbol}`
    const list = groups.get(key)
    if (list) list.push(fill)
    else groups.set(key, [fill])
  }

  const trips: AnalyzedTrade[] = []
  for (const group of groups.values()) {
    trips.push(...pairGroup(group))
  }

  let totalPnl = 0
  let winCount = 0
  let lossCount = 0
  let maxProfitTrip: AnalyzedTrade | null = null
  let maxLossTrip: AnalyzedTrade | null = null
  for (const trip of trips) {
    totalPnl += trip.pnl
    if (trip.pnl > 0) {
      winCount += 1
      if (!maxProfitTrip || trip.pnl > maxProfitTrip.pnl) maxProfitTrip = trip
    } else if (trip.pnl < 0) {
      lossCount += 1
      if (!maxLossTrip || trip.pnl < maxLossTrip.pnl) maxLossTrip = trip
    }
  }

  return {
    fillCount: fills.length,
    tradeCount: trips.length,
    totalNotional,
    totalPnl,
    winCount,
    lossCount,
    winRate: trips.length > 0 ? winCount / trips.length : null,
    maxProfitTrip,
    maxLossTrip,
    commissions,
  }
}
