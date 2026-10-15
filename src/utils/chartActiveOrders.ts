export interface ChartActiveOrderInput {
  id: number
  symbol: string
  exchange_order_id?: string | null
  side: string
  order_type: string
  trade_direction?: string | null
  reduce_only?: boolean
  status: string
  price?: number | null
  quantity: number
  filled_qty?: number | null
}

export interface ChartConditionalOrderInput {
  algo_id: number
  symbol: string
  side: string
  order_type: string
  quantity: number
  trigger_price: number
  status: string
  trade_direction?: string | null
}

export interface ChartActiveOrder {
  id: number
  symbol: string
  exchange_order_id?: string | null
  side: string
  order_type: string
  trade_direction?: string | null
  reduce_only: boolean
  price: number
  quantity: number
  remaining_quantity: number
  cancel_kind?: 'basic' | 'conditional'
  algo_id?: number
}

/** Map active basic limits and conditional Stop orders to chart lines. */
export function selectChartActiveOrders(
  orders: ChartActiveOrderInput[],
  symbol: string,
  conditionalOrders: ChartConditionalOrderInput[] = [],
): ChartActiveOrder[] {
  const normalizedSymbol = symbol.trim().toUpperCase()

  const basicLines = orders
    .filter((order) => {
      const orderType = String(order.order_type || '').toUpperCase()
      const status = String(order.status || '').toUpperCase()
      return order.symbol.toUpperCase() === normalizedSymbol
        && ['LIMIT', 'TAKE_PROFIT'].includes(orderType)
        && ['NEW', 'PARTIALLY_FILLED'].includes(status)
        && Number(order.price) > 0
        && Number(order.quantity) > Number(order.filled_qty || 0)
    })
    .map((order) => ({
      id: order.id,
      symbol: order.symbol,
      exchange_order_id: order.exchange_order_id,
      side: order.side,
      order_type: order.order_type,
      trade_direction: order.trade_direction,
      reduce_only: Boolean(order.reduce_only),
      price: Number(order.price),
      quantity: Number(order.quantity),
      remaining_quantity: Math.max(0, Number(order.quantity) - Number(order.filled_qty || 0)),
      cancel_kind: 'basic' as const,
    }))

  const stopLines = conditionalOrders
    .filter((order) => {
      const orderType = String(order.order_type || '').toUpperCase()
      const status = String(order.status || '').toUpperCase()
      return order.symbol.toUpperCase() === normalizedSymbol
        && ['STOP', 'STOP_MARKET'].includes(orderType)
        && ['NEW', 'WORKING', 'TRIGGERING', 'TRIGGERED', 'PARTIALLY_FILLED'].includes(status)
        && Number(order.trigger_price) > 0
        && Number(order.quantity) > 0
    })
    .map((order) => ({
      id: Number(order.algo_id),
      algo_id: Number(order.algo_id),
      symbol: order.symbol,
      side: order.side,
      order_type: order.order_type,
      trade_direction: order.trade_direction,
      reduce_only: false,
      price: Number(order.trigger_price),
      quantity: Number(order.quantity),
      remaining_quantity: Number(order.quantity),
      cancel_kind: 'conditional' as const,
    }))

  return [...basicLines, ...stopLines]
}
