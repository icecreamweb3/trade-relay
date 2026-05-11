import { useState, useEffect } from 'react'
import { api } from '../api/client'
import { useAuthStore } from '../store/authStore'

interface Order {
  id: number; symbol: string; side: string; order_type: string
  quantity: number; price: number; status: string; username: string
  exchange_order_id?: string; created_at?: string; error_message?: string
}

export function OrderLogScreen() {
  const [orders, setOrders] = useState<Order[]>([])
  const [loading, setLoading] = useState(true)
  const { user } = useAuthStore()

  const load = async () => {
    setLoading(true)
    try {
      const data = await api.getOrders({ limit: 200 })
      setOrders(data)
    } catch { /* ignore */ }
    setLoading(false)
  }

  useEffect(() => { load() }, [])
  useEffect(() => {
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e]">
      <div className="px-4 py-2 border-b border-[#3e3e42] flex items-center gap-3 shrink-0">
        <span className="text-sm font-semibold text-[#cccccc]">订单记录</span>
        {user?.role === 'admin' && <span className="text-xs text-[#858585]">（全部用户）</span>}
        <button onClick={load} className="ml-auto text-xs text-[#858585] hover:text-[#cccccc]">
          {loading ? '加载中...' : '↻ 刷新'}
        </button>
      </div>
      <div className="flex-1 overflow-auto">
        <table className="trade-table w-full">
          <thead><tr>
            <th>#</th><th>时间</th><th>用户</th><th>品种</th>
            <th>方向</th><th>类型</th><th>数量</th><th>价格</th><th>状态</th><th>订单号</th>
          </tr></thead>
          <tbody>
            {orders.length === 0 ? (
              <tr><td colSpan={10} className="text-center text-[#858585] py-6">{loading ? '...' : '暂无订单'}</td></tr>
            ) : orders.map((o, i) => (
              <tr key={o.id}>
                <td className="text-[#858585]">{i + 1}</td>
                <td className="text-[#858585]">{o.created_at ? new Date(o.created_at).toLocaleString() : '-'}</td>
                <td className="text-[#cccccc]">{o.username}</td>
                <td className="font-semibold">{o.symbol}</td>
                <td className={o.side === 'BUY' ? 'text-buy font-semibold' : 'text-sell font-semibold'}>{o.side}</td>
                <td className="text-[#858585]">{o.order_type}</td>
                <td className="font-mono">{o.quantity}</td>
                <td className="font-mono">{o.price ? o.price.toFixed(2) : '-'}</td>
                <td><StatusBadge status={o.status} /></td>
                <td className="text-[#858585] font-mono truncate max-w-32" title={o.exchange_order_id}>
                  {o.exchange_order_id ? o.exchange_order_id.slice(0, 16) + '...' : o.error_message || '-'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const cls = status === 'FILLED' ? 'badge-filled'
    : status === 'MOCK' ? 'badge-mock'
    : status === 'FAILED' ? 'badge-failed'
    : 'badge-pending'
  return <span className={`badge ${cls}`}>{status}</span>
}
