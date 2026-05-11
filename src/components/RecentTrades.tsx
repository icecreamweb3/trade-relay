/**
 * RecentTrades — shows recent platform fills (all users, from backend API)
 */
import { useEffect, useState, useCallback } from 'react'
import { api } from '../api/client'

interface Fill {
  username: string
  symbol: string
  side: string
  quantity: number
  avg_price: number | null
  created_at: string
}

function fmtTime(ts: string): string {
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString('en-US', { hour12: false })
  } catch {
    return ts
  }
}

function fmtNum(n: number | null, dp = 2): string {
  if (n == null) return '—'
  return n.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })
}

export function RecentTrades() {
  const [fills, setFills] = useState<Fill[]>([])

  const load = useCallback(async () => {
    try {
      const data = await api.getRecentFills()
      setFills(data.slice(0, 30))
    } catch {}
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [load])

  return (
    <div className="h-full flex flex-col bg-[#161616] select-none">
      {/* Header */}
      <div className="px-3 py-1.5 border-b border-[#3e3e42] shrink-0">
        <span className="text-[11px] font-semibold text-[#cccccc]">Recent Trades</span>
      </div>

      {/* Column labels */}
      <div className="grid px-2 py-1 text-[9px] text-[#555] uppercase tracking-wider shrink-0 border-b border-[#2a2a2a]"
        style={{ gridTemplateColumns: '1fr 1fr 52px 1fr 1fr 1fr' }}>
        <span>User</span>
        <span>Symbol</span>
        <span>Side</span>
        <span className="text-right">Qty</span>
        <span className="text-right">Value</span>
        <span className="text-right">Time</span>
      </div>

      {/* Rows */}
      <div className="flex-1 overflow-y-auto">
        {fills.length === 0 ? (
          <div className="px-3 py-4 text-[10px] text-[#444]">No recent trades</div>
        ) : (
          fills.map((f, i) => {
            const isBuy = f.side === 'BUY'
            const value = f.avg_price != null ? f.quantity * f.avg_price : null
            return (
              <div
                key={i}
                className="grid px-2 py-[2px] text-[10px] hover:bg-[#1e1e1e] font-mono tabular-nums"
                style={{ gridTemplateColumns: '1fr 1fr 52px 1fr 1fr 1fr' }}
              >
                <span className="text-[#aaa] truncate pr-1">{f.username}</span>
                <span className="text-[#888] truncate pr-1">{f.symbol}</span>
                <span className={isBuy ? 'text-[#0ecb81]' : 'text-[#f6465d]'}>{f.side}</span>
                <span className="text-right text-[#aaa]">{fmtNum(f.quantity, 4)}</span>
                <span className="text-right text-[#aaa]">{value != null ? fmtNum(value, 2) : '—'}</span>
                <span className="text-right text-[#555]">{fmtTime(f.created_at)}</span>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
