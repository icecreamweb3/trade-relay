import { useEffect, useRef, useState } from 'react'
import { PanelGroup, Panel, PanelResizeHandle } from 'react-resizable-panels'
import { useAuthStore } from './store/authStore'
import { useMarketData } from './hooks/useMarketData'
import { LoginModal } from './components/LoginModal'
import { TitleBar } from './components/TitleBar'
import { StatusBar } from './components/StatusBar'
import { BinancePanel } from './components/BinancePanel'
import { OrderFormWidget } from './components/OrderFormWidget'
import { PositionsPanel } from './components/PositionsPanel'
import { OrderBook } from './components/OrderBook'
import { RecentTrades } from './components/RecentTrades'
import { OrderLogScreen } from './components/OrderLogScreen'
import { AdminScreen } from './components/AdminScreen'
import { ProfileScreen } from './components/ProfileScreen'
import { ConfigScreen } from './components/ConfigScreen'

type Screen = 'trade' | 'orders' | 'users' | 'profile' | 'settings'

function MainApp() {
  useMarketData()

  const { isAuthenticated } = useAuthStore()
  const [screen, setScreen] = useState<Screen>('trade')
  const [orderRefresh, setOrderRefresh] = useState(0)
  const [showLogin, setShowLogin] = useState(false)

  const openLogin = () => {
    // Collapse BrowserView to 0 so it doesn't cover the modal
    window.electronAPI?.resizeBinancePanel(0, 0)
    setShowLogin(true)
  }
  const closeLogin = () => {
    setShowLogin(false)
    // Restore BrowserView to its previous position
    window.electronAPI?.resizeBinancePanel(leftRatio.current, chartRatio.current)
  }

  // Close modal automatically after successful login
  useEffect(() => {
    if (isAuthenticated) closeLogin()
  }, [isAuthenticated])

  // Track both ratios to send both to Electron on any resize
  const leftRatio = useRef(0.62)
  const chartRatio = useRef(0.65)

  const notifyElectron = () => {
    window.electronAPI?.resizeBinancePanel(leftRatio.current, chartRatio.current)
  }

  const handleMainLayout = (sizes: number[]) => {
    leftRatio.current = sizes[0] / 100
    notifyElectron()
  }

  const handleLeftLayout = (sizes: number[]) => {
    chartRatio.current = sizes[0] / 100
    notifyElectron()
  }

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <TitleBar activeScreen={screen} onNavigate={setScreen} onLoginClick={openLogin} />

      {/* Login modal — shown when not authenticated */}
      {showLogin && <LoginModal onClose={closeLogin} />}

      {/* Non-trade screens */}
      {screen !== 'trade' && (
        <>
          {screen === 'orders'   && <div className="flex-1 overflow-hidden"><OrderLogScreen /></div>}
          {screen === 'users'    && <div className="flex-1 overflow-hidden"><AdminScreen /></div>}
          {screen === 'profile'  && <div className="flex-1 overflow-hidden"><ProfileScreen /></div>}
          {screen === 'settings' && <div className="flex-1 overflow-hidden"><ConfigScreen /></div>}
          <StatusBar />
        </>
      )}

      {/* Trading screen — always mounted to keep BrowserView in sync */}
      <div className={`flex-1 overflow-hidden flex flex-col ${screen !== 'trade' ? 'hidden' : ''}`}>
        <PanelGroup
          direction="horizontal"
          className="flex-1 overflow-hidden"
          onLayout={handleMainLayout}
        >
          {/* ── LEFT: chart (top) + positions (bottom) ── */}
          <Panel defaultSize={62} minSize={38} id="left">
            <PanelGroup direction="vertical" className="h-full" onLayout={handleLeftLayout}>
              <Panel defaultSize={65} minSize={30} id="chart">
                <BinancePanel />
              </Panel>
              <PanelResizeHandle className="h-px bg-[#3e3e42] hover:bg-[#007acc] cursor-row-resize" />
              <Panel defaultSize={35} minSize={15} id="positions">
                <PositionsPanel refreshTrigger={orderRefresh} />
              </Panel>
            </PanelGroup>
          </Panel>

          <PanelResizeHandle className="w-1 bg-[#3e3e42] hover:bg-[#007acc] cursor-col-resize" />

          {/* ── RIGHT: [order book | trade form] (top) + recent trades (bottom) ── */}
          <Panel defaultSize={38} minSize={26} maxSize={55} id="right">
            <PanelGroup direction="vertical" className="h-full">
              {/* Top: order book (fixed width) + trade form (flex) */}
              <Panel defaultSize={72} minSize={50} id="right-top">
                <div className="h-full flex overflow-hidden">
                  {/* Order book — fixed width */}
                  <div className="w-[350px] shrink-0 border-r border-[#2a2a2a] overflow-hidden">
                    <OrderBook />
                  </div>
                  {/* Trade form — fills remaining width */}
                  <div className="flex-1 min-w-0 overflow-hidden">
                    <OrderFormWidget onOrderPlaced={() => setOrderRefresh(n => n + 1)} />
                  </div>
                </div>
              </Panel>

              <PanelResizeHandle className="h-px bg-[#3e3e42] hover:bg-[#007acc] cursor-row-resize" />

              {/* Bottom-right: recent platform trades */}
              <Panel defaultSize={28} minSize={15} id="right-bottom">
                <RecentTrades />
              </Panel>
            </PanelGroup>
          </Panel>
        </PanelGroup>

        <StatusBar />
      </div>
    </div>
  )
}

export default function App() {
  const { isLoading, checkAuth } = useAuthStore()

  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  if (isLoading) {
    return (
      <div className="h-screen bg-[#1e1e1e] flex items-center justify-center text-[#858585] text-sm">
        正在验证身份...
      </div>
    )
  }

  // Always render MainApp; login modal is shown inline when needed
  return <MainApp />
}
