/**
 * 交易分析：把订单记录配对成「完整交易」，并汇总总体交易情况。纯函数，不依赖 React。
 *
 * 双向持仓（hedge）模式下同一交易对的多、空是独立仓位，配对按
 * （用户, 交易对, 持仓方向）分组进行：组内净持仓从 0 升起即开启一笔，
 * 期间所有开仓（含分批建仓、加仓）与平仓（含分批止盈、减仓）都累计进
 * 同一笔，净持仓回到 0 该笔完成 —— 分批止盈无论分几批都会合并成一笔。
 * 平仓量超出当前持仓的部分（窗口起点前遗留仓位或数据缺口，如交易所手动
 * 平仓未同步）直接忽略，不影响后续分笔；窗口结束时仍未平仓完成的交易
 * 不计入统计。无开平标记的记录（旧数据）退回旧的净额配对。
 */
import { parseUtcTimestamp } from './datetime'

export interface OrderLike {
  id: number
  username?: string
  symbol: string
  side: string
  order_type?: string | null
  trade_direction?: string | null
  quantity?: number
  filled_qty?: number
  price?: number | null
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
  /** 该笔交易的开仓数量（组内各开仓单成交量合计） */
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

/**
 * 由 开平方向 + 买卖方向 推导持仓方向（与后端 _derive_conditional_position_side 规则一致）：
 * 开仓 BUY→LONG / SELL→SHORT；平仓 BUY→SHORT / SELL→LONG。
 * 无开平标记时返回 ''，该记录退回旧的净额配对分组。
 */
function positionSideKey(fill: OrderLike): string {
  const dir = String(fill.trade_direction || '').toUpperCase()
  const side = String(fill.side || '').toUpperCase()
  if (dir === 'OPEN') return side === 'BUY' ? 'LONG' : 'SHORT'
  if (dir === 'CLOSE') return side === 'BUY' ? 'SHORT' : 'LONG'
  return ''
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
 * 单个 (用户, 交易对, 持仓方向) 分组内（双向持仓模式），按时间升序配对：
 * 净持仓从 0 升起即开启一笔交易，期间所有开仓/平仓（含分批建仓、分批止盈、
 * 加仓、减仓）都累计进同一笔；净持仓回到 0 该笔完成。
 * 平仓量超出当前持仓的部分（窗口起点前遗留仓位或数据缺口）忽略；
 * 窗口结束时仍未平仓完成的交易不计入统计（draft 直接丢弃）。
 */
function pairGroupBySide(fills: OrderLike[]): AnalyzedTrade[] {
  const trips: AnalyzedTrade[] = []
  let position = 0
  let draft: TripDraft | null = null

  const finishTrip = () => {
    if (draft) {
      trips.push({
        ...draft,
        entryTimes: [...new Set(draft.entryTimes)].sort(),
        exitTimes: draft.exitTimes.sort(),
      })
    }
    draft = null
    position = 0
  }

  for (const fill of fills) {
    const qty = Number(fill.filled_qty ?? 0)
    if (qty <= 0) continue
    const time = effectiveTimestamp(fill)
    const dir = String(fill.trade_direction || '').toUpperCase()

    if (dir === 'OPEN') {
      if (!draft) {
        draft = { username: fill.username ?? '', symbol: fill.symbol, entryTimes: [], exitTimes: [], quantity: 0, pnl: 0, commissions: {} }
      }
      if (time) draft.entryTimes.push(time)
      draft.quantity += qty
      addCommission(draft.commissions, fill.commission_asset, fill.commission)
      position += qty
      continue
    }

    // 平仓单：无在持仓位（窗口起点前遗留仓位的平仓）无法配对，跳过
    if (!draft) continue
    if (time) draft.exitTimes.push(time)
    draft.pnl += Number(fill.realized_pnl ?? 0)
    addCommission(draft.commissions, fill.commission_asset, fill.commission)
    position -= qty
    if (position <= EPS) finishTrip()
  }
  return trips
}

/**
 * 无开平标记的分组（旧数据）：按时间升序遍历成交单，
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

  const groups = new Map<string, { legacy: boolean; list: OrderLike[] }>()
  for (const fill of fills) {
    const sideKey = positionSideKey(fill)
    const key = `${fill.username ?? ''}${fill.symbol}${sideKey}`
    const group = groups.get(key)
    if (group) group.list.push(fill)
    else groups.set(key, { legacy: sideKey === '', list: [fill] })
  }

  const trips: AnalyzedTrade[] = []
  for (const group of groups.values()) {
    trips.push(...(group.legacy ? pairGroup(group.list) : pairGroupBySide(group.list)))
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
