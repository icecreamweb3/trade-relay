import React from 'react'
import { ChevronLeft, ChevronRight, RotateCcw, Expand, Shrink, LogOut, LogIn, Settings, Users, BarChart2, ClipboardList, Activity, Eraser } from 'lucide-react'
import { useMarketStore } from '../store/marketStore'
import { useAuthStore } from '../store/authStore'
import { Locale, useTranslation } from '../i18n/translations'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'

type Screen = 'trade' | 'orders' | 'users' | 'profile' | 'settings'

interface TitleBarProps {
  activeScreen: Screen
  onNavigate: (screen: Screen) => void
  onLoginClick?: () => void
}

export function TitleBar({ activeScreen, onNavigate, onLoginClick }: TitleBarProps) {
  const locale = useUiPreferencesStore((state) => state.locale)
  const setChartOrderMarkersVisible = useUiPreferencesStore((state) => state.setChartOrderMarkersVisible)
  const { t } = useTranslation(locale)
  const { symbol, isConnected, isChartExpanded, setChartExpanded } = useMarketStore()
  const { user, logout } = useAuthStore()
  const [isDebugClearing, setIsDebugClearing] = React.useState(false)
  const [debugDialog, setDebugDialog] = React.useState<null | { title: string; lines: string[] }>(null)

  const minimize = () => window.electronAPI?.minimizeWindow()
  const maximize = () => window.electronAPI?.maximizeWindow()
  const close = () => window.electronAPI?.closeWindow()

  const debugClearLabel = locale === 'zh-CN' ? '清箭头' : 'Clear Arrows'
  const showDebugClearButton = false
  const showDebugDialog = false

  React.useEffect(() => {
    const shouldShowBinanceView = activeScreen === 'trade' && (!showDebugDialog || !debugDialog)
    window.electronAPI?.setBinanceViewVisible?.(shouldShowBinanceView)
  }, [activeScreen, debugDialog, showDebugDialog])

  const handleDebugClearArrows = async () => {
    if (isDebugClearing) return

    setIsDebugClearing(true)
    setChartOrderMarkersVisible(false)
    try {
      const probeResult = await window.electronAPI?.debugProbeChartOverlay?.()
      const clearResult = await window.electronAPI?.debugClearChartOverlaySignals?.()

      const lines = [
        `[probe] ${JSON.stringify(probeResult ?? {})}`,
        `[clear-ipc] ${JSON.stringify(clearResult ?? {})}`,
      ]

      setDebugDialog({
        title: locale === 'zh-CN' ? '清除箭头调试结果' : 'Clear Arrow Debug Result',
        lines,
      })
    } catch (error) {
      setDebugDialog({
        title: locale === 'zh-CN' ? '清除箭头调试失败' : 'Clear Arrow Debug Failed',
        lines: [error instanceof Error ? error.message : String(error)],
      })
    } finally {
      setIsDebugClearing(false)
    }
  }

  return (
    <>
      <div
        className="h-8 bg-[#323233] flex items-center justify-between px-2 select-none"
        style={{ WebkitAppRegion: 'drag' } as React.CSSProperties}
      >
        {/* Left: logo + nav */}
        <div className="flex items-center gap-1" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
          <Activity size={14} className="text-[#007acc] shrink-0" />
          <span className="text-xs font-semibold text-[#cccccc] mr-2">{t('title.app')}</span>
          <span className="text-xs text-[#858585] mr-1">{symbol}</span>
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${isConnected ? 'bg-green-400' : 'bg-yellow-400'}`} />

          <div className="flex items-center gap-0.5 ml-2">
            <NavBtn onClick={() => window.electronAPI?.binanceGoBack()} title={t('nav.back')}><ChevronLeft size={13} /></NavBtn>
            <NavBtn onClick={() => window.electronAPI?.binanceGoForward()} title={t('nav.forward')}><ChevronRight size={13} /></NavBtn>
            <NavBtn onClick={() => window.electronAPI?.binanceReload()} title={t('nav.reload')}><RotateCcw size={11} /></NavBtn>
            <NavBtn
              onClick={() => { const next = !isChartExpanded; setChartExpanded(next); window.electronAPI?.chartToggleFullscreen?.() }}
              title={t('nav.chartExpand')}
              active={isChartExpanded}
            >
              {isChartExpanded ? <Shrink size={11} /> : <Expand size={11} />}
            </NavBtn>
            {showDebugClearButton && (
              <NavBtn
                onClick={handleDebugClearArrows}
                title={debugClearLabel}
                active={isDebugClearing}
              >
                {isDebugClearing ? '...' : <Eraser size={11} />}
              </NavBtn>
            )}
          </div>

          <div className="flex items-center gap-0.5 ml-3">
            <ScreenTab active={activeScreen === 'trade'} onClick={() => onNavigate('trade')} icon={<Activity size={11} />}>{t('nav.trade')}</ScreenTab>
            <ScreenTab active={activeScreen === 'orders'} onClick={() => onNavigate('orders')} icon={<ClipboardList size={11} />}>{t('nav.orders')}</ScreenTab>
            {user?.role === 'admin' && (
              <ScreenTab active={activeScreen === 'users'} onClick={() => onNavigate('users')} icon={<Users size={11} />}>{t('nav.users')}</ScreenTab>
            )}
            <ScreenTab active={activeScreen === 'profile'} onClick={() => onNavigate('profile')} icon={<BarChart2 size={11} />}>{t('nav.profile')}</ScreenTab>
            <ScreenTab active={activeScreen === 'settings'} onClick={() => onNavigate('settings')} icon={<Settings size={11} />}>{t('nav.settings')}</ScreenTab>
          </div>
        </div>

        <div className="flex items-center gap-1.5" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
          {user ? (
            <>
              <span className="text-xs text-[#858585]">{user.username} ({user.role})</span>
              <NavBtn onClick={async () => { await logout(); window.location.reload() }} title={t('nav.logout')}>
                <LogOut size={11} />
              </NavBtn>
            </>
          ) : (
            <button
              onClick={onLoginClick}
              className="flex items-center gap-1 px-2.5 h-6 text-xs font-medium rounded bg-[#007acc] hover:bg-[#0069b3] text-white transition-colors"
            >
              <LogIn size={11} />
              {t('nav.login')}
            </button>
          )}
          <div className="w-px h-4 bg-[#3e3e42] mx-1" />
          <WinBtn onClick={minimize} cls="hover:bg-[#3e3e42]">─</WinBtn>
          <WinBtn onClick={maximize} cls="hover:bg-[#3e3e42]">□</WinBtn>
          <WinBtn onClick={close} cls="hover:bg-red-600">✕</WinBtn>
        </div>
      </div>

      {/* Debug dialog is kept here for future troubleshooting, but hidden in normal use.
      {showDebugDialog && debugDialog && (
        <div className="fixed inset-0 z-[10020] flex items-center justify-center bg-black/55 px-4" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
          <div className="w-full max-w-[720px] rounded-xl border border-[#3a4048] bg-[#1c2027] shadow-[0_24px_60px_rgba(0,0,0,0.45)]">
            <div className="flex items-center justify-between border-b border-[#313741] px-4 py-3">
              <h2 className="text-sm font-semibold text-[#f3f5f7]">{debugDialog.title}</h2>
              <button
                onClick={() => setDebugDialog(null)}
                className="rounded px-2 py-1 text-xs text-[#aeb6c2] hover:bg-[#2a313b] hover:text-white"
              >
                {debugDialogCloseLabel}
              </button>
            </div>
            <div className="max-h-[60vh] overflow-auto px-4 py-3">
              <pre className="whitespace-pre-wrap break-words text-[12px] leading-5 text-[#d8dee7]">
                {debugDialog.lines.join('\n\n')}
              </pre>
            </div>
          </div>
        </div>
      )}
      */}
    </>
  )
}

function NavBtn({ onClick, title, children, active }: { onClick: () => void; title?: string; children: React.ReactNode; active?: boolean }) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`w-6 h-6 flex items-center justify-center rounded transition-colors ${
        active ? 'text-[#007acc]' : 'text-[#858585] hover:text-white hover:bg-[#3e3e42]'
      }`}
    >
      {children}
    </button>
  )
}

function ScreenTab({ active, onClick, icon, children }: { active: boolean; onClick: () => void; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1 px-2.5 h-8 text-xs font-medium border-b-2 transition-colors ${
        active
          ? 'border-[#007acc] text-[#cccccc]'
          : 'border-transparent text-[#858585] hover:text-[#cccccc]'
      }`}
    >
      {icon}
      {children}
    </button>
  )
}

function WinBtn({ onClick, cls, children }: { onClick: () => void; cls: string; children: React.ReactNode }) {
  return (
    <button onClick={onClick} className={`w-6 h-6 flex items-center justify-center rounded text-[#858585] hover:text-white text-xs transition-colors ${cls}`}>
      {children}
    </button>
  )
}
