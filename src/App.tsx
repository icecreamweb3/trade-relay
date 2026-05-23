import { useEffect, useRef, useState } from 'react'
import { PanelGroup, Panel, PanelResizeHandle } from 'react-resizable-panels'
import { useAuthStore } from './store/authStore'
import { Locale, useTranslation } from './i18n/translations'
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
import { GlobalToast } from './components/GlobalToast'
import { useUiPreferencesStore } from './store/uiPreferencesStore'

type Screen = 'trade' | 'orders' | 'users' | 'profile' | 'settings'
type WorkspaceTab = { id: Screen; screen: Screen; title: string; closable: boolean }

function MainApp() {
  useMarketData()

  const appLocale = useUiPreferencesStore((state) => state.locale)

  const { t } = useTranslation(appLocale)
  const { isAuthenticated } = useAuthStore()
  const wasAuthenticatedRef = useRef(isAuthenticated)
  const getScreenTitle = (screen: Screen) => {
    switch (screen) {
      case 'trade': return t('nav.trade')
      case 'orders': return t('nav.orders')
      case 'users': return t('nav.users')
      case 'profile': return t('nav.profile')
      case 'settings': return t('nav.settings')
    }
  }

  const [tabs, setTabs] = useState<WorkspaceTab[]>([
    { id: 'trade', screen: 'trade', title: getScreenTitle('trade'), closable: false },
  ])
  const [activeTabId, setActiveTabId] = useState<Screen>('trade')
  const [orderRefresh, setOrderRefresh] = useState(0)
  const [positionRefresh, setPositionRefresh] = useState(0)
  const [showLogin, setShowLogin] = useState(false)
  const [selectedOrderBookPrice, setSelectedOrderBookPrice] = useState<{ value: number; token: number } | null>(null)
  const [tradeSizeUnit, setTradeSizeUnit] = useState<'QUOTE' | 'BASE'>('QUOTE')

  const activeTab = tabs.find((tab) => tab.id === activeTabId) ?? { id: 'trade', screen: 'trade', title: getScreenTitle('trade'), closable: false }
  const activeScreen = activeTab.screen
  const isTradeScreenActive = activeScreen === 'trade'

  const openScreen = (screen: Screen) => {
    if (screen === 'trade') {
      setActiveTabId('trade')
      return
    }

    if (!isAuthenticated) {
      openLogin()
      return
    }

    setTabs((current) => {
      if (current.some((tab) => tab.id === screen)) return current
      return [...current, { id: screen, screen, title: getScreenTitle(screen), closable: true }]
    })
    setActiveTabId(screen)
  }

  const closeTab = (tabId: Screen) => {
    if (tabId === 'trade') return
    setTabs((current) => current.filter((tab) => tab.id !== tabId))
    setActiveTabId((current) => (current === tabId ? 'trade' : current))
  }

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

  useEffect(() => {
    if (wasAuthenticatedRef.current && !isAuthenticated) {
      openLogin()
    }
    wasAuthenticatedRef.current = isAuthenticated
  }, [isAuthenticated])

  useEffect(() => {
    setTabs((current) => current.map((tab) => ({
      ...tab,
      title: getScreenTitle(tab.screen),
    })))
  }, [appLocale])

  useEffect(() => {
    window.electronAPI?.setBinanceViewVisible?.(isTradeScreenActive)
    if (isTradeScreenActive) {
      notifyElectron()
    }
  }, [isTradeScreenActive])

  // Track both ratios to send both to Electron on any resize
  const leftRatio = useRef(0.60)
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
      <TitleBar activeScreen={activeScreen} onNavigate={openScreen} onLoginClick={openLogin} />
      <GlobalToast />

      <div className="h-9 shrink-0 bg-[#1a1d23] border-b border-[#2b2f36] flex items-end px-2 overflow-x-auto">
        {tabs.map((tab) => {
          const active = tab.id === activeTabId
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTabId(tab.id)}
              className={`group flex items-center gap-2 h-8 px-3 mr-1 rounded-t-md border border-b-0 text-xs transition-colors ${
                active
                  ? 'bg-[#0f131a] border-[#3a404c] text-white'
                  : 'bg-[#232831] border-[#2f3440] text-[#9aa3b2] hover:text-white hover:bg-[#2a303a]'
              }`}
            >
              <span>{tab.title}</span>
              {tab.closable && (
                <span
                  onClick={(event) => {
                    event.stopPropagation()
                    closeTab(tab.id)
                  }}
                  className="text-[11px] leading-none rounded px-1 text-[#7f8896] hover:text-white hover:bg-[#3a404c]"
                >
                  ×
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Login modal — shown when not authenticated */}
      {showLogin && <LoginModal onClose={closeLogin} />}

      {/* Non-trade screens */}
      {activeScreen !== 'trade' && (
        <>
          {activeScreen === 'orders'   && <div className="flex-1 overflow-hidden"><OrderLogScreen /></div>}
          {activeScreen === 'users'    && <div className="flex-1 overflow-hidden"><AdminScreen /></div>}
          {activeScreen === 'profile'  && <div className="flex-1 overflow-hidden"><ProfileScreen /></div>}
          {activeScreen === 'settings' && <div className="flex-1 overflow-hidden"><ConfigScreen /></div>}
          <StatusBar />
        </>
      )}

      {/* Trading screen — always mounted to keep BrowserView in sync */}
      <div className={`flex-1 overflow-hidden flex flex-col ${activeScreen !== 'trade' ? 'hidden' : ''}`}>
        <PanelGroup
          direction="horizontal"
          className="flex-1 overflow-hidden"
          onLayout={handleMainLayout}
        >
          {/* ── LEFT: chart (top) + positions (bottom) ── */}
          <Panel defaultSize={67} minSize={40} id="left">
            <PanelGroup direction="vertical" className="h-full" onLayout={handleLeftLayout}>
              <Panel defaultSize={65} minSize={30} id="chart">
                <BinancePanel />
              </Panel>
              <PanelResizeHandle className="h-px bg-[#3e3e42] hover:bg-[#007acc] cursor-row-resize" />
              <Panel defaultSize={35} minSize={15} id="positions">
                <PositionsPanel
                  refreshTrigger={orderRefresh + positionRefresh}
                  isActive={isTradeScreenActive}
                  sizeUnit={tradeSizeUnit}
                  onOrdersChanged={() => setOrderRefresh(n => n + 1)}
                />
              </Panel>
            </PanelGroup>
          </Panel>

          <PanelResizeHandle className="w-1 bg-[#3e3e42] hover:bg-[#007acc] cursor-col-resize" />

          {/* ── RIGHT: [order book | trade form] (top) + recent trades (bottom) ── */}
          <Panel defaultSize={33} minSize={22} maxSize={48} id="right">
            <PanelGroup direction="vertical" className="h-full">
              {/* Top: order book (fixed width) + trade form (flex) */}
              <Panel defaultSize={72} minSize={50} id="right-top">
                <div className="h-full flex overflow-hidden">
                  {/* Order book — fixed width */}
                  <div className="w-[280px] shrink-0 border-r border-[#2a2a2a] overflow-hidden">
                    <OrderBook onPriceSelect={(value) => {
                      setSelectedOrderBookPrice((current) => ({ value, token: (current?.token ?? 0) + 1 }))
                    }} />
                  </div>
                  {/* Trade form — fills remaining width */}
                  <div className="flex-1 min-w-0 overflow-hidden">
                    <OrderFormWidget
                      isActive={isTradeScreenActive}
                      onOrderPlaced={() => setOrderRefresh(n => n + 1)}
                      onLoginClick={openLogin}
                      selectedOrderBookPrice={selectedOrderBookPrice}
                      sizeUnit={tradeSizeUnit}
                      onSizeUnitChange={setTradeSizeUnit}
                      refreshTrigger={orderRefresh + positionRefresh}
                    />
                  </div>
                </div>
              </Panel>

              <PanelResizeHandle className="h-px bg-[#3e3e42] hover:bg-[#007acc] cursor-row-resize" />

              {/* Bottom-right: recent platform trades */}
              <Panel defaultSize={28} minSize={15} id="right-bottom">
                <RecentTrades isActive={isTradeScreenActive} refreshTrigger={orderRefresh} />
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
  const { checkAuth } = useAuthStore()
  const locale = useUiPreferencesStore((state) => state.locale)
  const [initializing, setInitializing] = useState(true)
  const { t } = useTranslation(locale)

  useEffect(() => {
    checkAuth().finally(() => setInitializing(false))
  }, [checkAuth])

  if (initializing) {
    return (
      <div className="h-screen bg-[#1e1e1e] flex items-center justify-center text-[#858585] text-sm">
        {t('app.initializing')}
      </div>
    )
  }

  // Always render MainApp; login modal is shown inline when needed
  return <MainApp />
}
