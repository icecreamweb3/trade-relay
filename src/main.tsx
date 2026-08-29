import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { ErrorBoundary } from './components/ErrorBoundary'
import { OrderKlineModal } from './components/OrderKlineModal'
import type { PositionWindow } from './utils/orderChart'

function OrderKlineWindowApp() {
  const [position, setPosition] = React.useState<PositionWindow | null>(null)

  React.useEffect(() => {
    let active = true
    const unsubscribe = window.electronAPI?.onOrderKlinePayload?.((payload) => {
      if (active) setPosition(payload)
    })
    window.electronAPI?.getOrderKlinePayload?.().then((payload) => {
      if (active && payload) setPosition(payload)
    })
    return () => {
      active = false
      unsubscribe?.()
    }
  }, [])

  if (!position) {
    return <div className="flex h-screen items-center justify-center bg-[#101318] text-sm text-[#9aa3b2]">Loading position candles...</div>
  }

  return <OrderKlineModal position={position} standalone onClose={() => void window.electronAPI?.closeOrderKlineWindow?.()} />
}

const isOrderKlineWindow = new URLSearchParams(window.location.search).get('window') === 'order-kline'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <ErrorBoundary>
    {isOrderKlineWindow ? <OrderKlineWindowApp /> : <App />}
  </ErrorBoundary>
)
