import { parseUtcTimestamp } from './datetime'
import type { OrderLike } from './tradeAnalysis'

export interface PositionFillMarker {
  id: number
  action: 'ENTRY' | 'EXIT'
  side: string
  timestamp: number
  price: number
  quantity: number
}

export interface PositionWindow {
  symbol: string
  username: string
  positionSide: 'LONG' | 'SHORT' | 'UNKNOWN'
  startTime: number
  endTime: number
  isOpen: boolean
  markers: PositionFillMarker[]
}

const EPS = 1e-9

function fillTimestamp(order: OrderLike): number | null {
  const parsed = parseUtcTimestamp(order.filled_at || order.updated_at || order.created_at)
  return parsed?.getTime() ?? null
}

function positionSide(order: OrderLike): 'LONG' | 'SHORT' | 'UNKNOWN' {
  const direction = String(order.trade_direction || '').toUpperCase()
  const side = String(order.side || '').toUpperCase()
  if (direction === 'OPEN') return side === 'BUY' ? 'LONG' : 'SHORT'
  if (direction === 'CLOSE') return side === 'BUY' ? 'SHORT' : 'LONG'
  return 'UNKNOWN'
}

function toMarker(order: OrderLike, action: 'ENTRY' | 'EXIT'): PositionFillMarker | null {
  const timestamp = fillTimestamp(order)
  const price = Number(order.avg_price)
  const quantity = Number(order.filled_qty)
  if (timestamp == null || !Number.isFinite(price) || price <= 0 || !Number.isFinite(quantity) || quantity <= 0) return null
  return { id: order.id, action, side: order.side, timestamp, price, quantity }
}

/** Find the complete (or currently open) position cycle containing one order. */
export function findPositionWindow(orders: OrderLike[], selectedOrderId: number, now = Date.now()): PositionWindow | null {
  const selected = orders.find((order) => order.id === selectedOrderId)
  if (!selected || Number(selected.filled_qty ?? 0) <= 0 || !(Number(selected.avg_price) > 0)) return null

  const selectedSide = positionSide(selected)
  const fills = orders
    .filter((order) => (
      order.username === selected.username
      && order.symbol === selected.symbol
      && Number(order.filled_qty ?? 0) > 0
      && Number(order.avg_price) > 0
      && positionSide(order) === selectedSide
      && fillTimestamp(order) != null
    ))
    .sort((left, right) => (fillTimestamp(left) ?? 0) - (fillTimestamp(right) ?? 0) || left.id - right.id)

  let position = 0
  let cycle: PositionFillMarker[] = []

  const resolveCycle = (isOpen: boolean): PositionWindow | null => {
    if (!cycle.some((marker) => marker.id === selectedOrderId)) return null
    const entries = cycle.filter((marker) => marker.action === 'ENTRY')
    const exits = cycle.filter((marker) => marker.action === 'EXIT')
    if (entries.length === 0) return null
    return {
      symbol: selected.symbol,
      username: selected.username ?? '',
      positionSide: selectedSide,
      startTime: Math.min(...entries.map((marker) => marker.timestamp)),
      endTime: isOpen ? now : Math.max(...exits.map((marker) => marker.timestamp)),
      isOpen,
      markers: [...cycle],
    }
  }

  for (const fill of fills) {
    const qty = Number(fill.filled_qty)
    const direction = String(fill.trade_direction || '').toUpperCase()

    if (selectedSide === 'UNKNOWN') {
      const delta = String(fill.side).toUpperCase() === 'BUY' ? qty : -qty
      if (cycle.length === 0 || Math.sign(delta) === Math.sign(position)) {
        const marker = toMarker(fill, 'ENTRY')
        if (marker) cycle.push(marker)
        position += delta
        continue
      }
      const marker = toMarker(fill, 'EXIT')
      if (marker) cycle.push(marker)
      const next = position + delta
      if (Math.abs(next) <= EPS || Math.sign(next) !== Math.sign(position)) {
        const found = resolveCycle(false)
        if (found) return found
        cycle = []
        position = 0
        if (Math.abs(next) > EPS) {
          const entry = toMarker(fill, 'ENTRY')
          if (entry) cycle = [entry]
          position = next
        }
      } else {
        position = next
      }
      continue
    }

    if (direction === 'OPEN') {
      const marker = toMarker(fill, 'ENTRY')
      if (marker) cycle.push(marker)
      position += qty
      continue
    }
    // A close from a position opened before the available history cannot be paired.
    if (cycle.length === 0) continue
    const marker = toMarker(fill, 'EXIT')
    if (marker) cycle.push(marker)
    position -= qty
    if (position <= EPS) {
      const found = resolveCycle(false)
      if (found) return found
      cycle = []
      position = 0
    }
  }

  return resolveCycle(true)
}

export function chooseKlineInterval(startTime: number, endTime: number): string {
  const durationMinutes = Math.max(1, (endTime - startTime) / 60_000)
  if (durationMinutes <= 180) return '1m'
  if (durationMinutes <= 900) return '5m'
  if (durationMinutes <= 2_700) return '15m'
  if (durationMinutes <= 10_800) return '1h'
  if (durationMinutes <= 43_200) return '4h'
  return '1d'
}
