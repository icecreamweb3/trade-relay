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
  focusTime: number
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

  if (selectedSide !== 'UNKNOWN') {
    return findExplicitPositionWindow(fills, selected, selectedSide, now)
  }

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
      focusTime: fillTimestamp(selected) ?? Math.min(...entries.map((marker) => marker.timestamp)),
      isOpen,
      markers: [...cycle],
    }
  }

  for (const fill of fills) {
    const qty = Number(fill.filled_qty)
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
  }

  return resolveCycle(true)
}

interface OpenLot {
  order: OrderLike
  remaining: number
}

/** LIFO-match one explicit OPEN/CLOSE fill instead of treating a continuously
 * non-zero account position as one multi-month trade. */
function findExplicitPositionWindow(
  fills: OrderLike[],
  selected: OrderLike,
  selectedSide: 'LONG' | 'SHORT',
  now: number,
): PositionWindow | null {
  const lots: OpenLot[] = []
  const selectedEntry = toMarker(selected, 'ENTRY')
  const selectedExits: PositionFillMarker[] = []
  let selectedOpenRemaining = Number(selected.filled_qty ?? 0)

  for (const fill of fills) {
    const direction = String(fill.trade_direction || '').toUpperCase()
    const fillQty = Number(fill.filled_qty ?? 0)
    if (direction === 'OPEN') {
      lots.push({ order: fill, remaining: fillQty })
      continue
    }

    let closeRemaining = fillQty
    const entriesClosedBySelected: PositionFillMarker[] = []
    let selectedCloseMatchedQty = 0

    while (closeRemaining > EPS && lots.length > 0) {
      // Match the most recently opened lots first. A 0.02 close following two
      // recent 0.01 opens should point to those two entries, not residual lots
      // from much earlier in the account history.
      const lot = lots[lots.length - 1]
      const consumed = Math.min(closeRemaining, lot.remaining)

      if (fill.id === selected.id) {
        const entry = toMarker(lot.order, 'ENTRY')
        if (entry) entriesClosedBySelected.push({ ...entry, quantity: consumed })
        selectedCloseMatchedQty += consumed
      }
      if (lot.order.id === selected.id) {
        const exit = toMarker(fill, 'EXIT')
        if (exit) selectedExits.push({ ...exit, quantity: consumed })
        selectedOpenRemaining -= consumed
      }

      lot.remaining -= consumed
      closeRemaining -= consumed
      if (lot.remaining <= EPS) lots.pop()
    }

    if (fill.id === selected.id) {
      const exit = toMarker(fill, 'EXIT')
      if (!exit || entriesClosedBySelected.length === 0 || selectedCloseMatchedQty <= EPS) return null
      const startTime = Math.min(...entriesClosedBySelected.map((marker) => marker.timestamp))
      return {
        symbol: selected.symbol,
        username: selected.username ?? '',
        positionSide: selectedSide,
        startTime,
        endTime: exit.timestamp,
        focusTime: exit.timestamp,
        isOpen: false,
        markers: [...entriesClosedBySelected, { ...exit, quantity: selectedCloseMatchedQty }],
      }
    }

    if (selectedEntry && selectedOpenRemaining <= EPS) {
      return {
        symbol: selected.symbol,
        username: selected.username ?? '',
        positionSide: selectedSide,
        startTime: selectedEntry.timestamp,
        endTime: Math.max(...selectedExits.map((marker) => marker.timestamp)),
        focusTime: selectedEntry.timestamp,
        isOpen: false,
        markers: [selectedEntry, ...selectedExits],
      }
    }
  }

  if (!selectedEntry || String(selected.trade_direction || '').toUpperCase() !== 'OPEN') return null
  return {
    symbol: selected.symbol,
    username: selected.username ?? '',
    positionSide: selectedSide,
    startTime: selectedEntry.timestamp,
    endTime: now,
    focusTime: selectedEntry.timestamp,
    isOpen: true,
    markers: [selectedEntry, ...selectedExits],
  }
}
