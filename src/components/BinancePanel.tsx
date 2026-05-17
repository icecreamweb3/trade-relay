// Left panel placeholder — actual Binance chart is rendered by Electron BrowserView
import { useMarketStore } from '../store/marketStore'
import { Locale, useTranslation } from '../i18n/translations'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'

export function BinancePanel() {
  const locale = useUiPreferencesStore((state) => state.locale)
  const { t } = useTranslation(locale)
  const { symbol, currentPrice, isConnected } = useMarketStore()

  return (
    <div className="h-full bg-[#1a1a2e] flex items-center justify-center text-center">
      <div className="text-[#3e3e6a]">
        <div className="text-2xl font-bold mb-2">{symbol}</div>
        {currentPrice !== null && (
          <div className="text-lg font-mono text-[#4a4a7a]">{currentPrice.toFixed(2)}</div>
        )}
        <div className={`text-xs mt-1 ${isConnected ? 'text-green-900' : 'text-[#3e3e6a]'}`}>
          {isConnected ? `● ${t('binance.connected')}` : `● ${t('binance.waitingView')}`}
        </div>
      </div>
    </div>
  )
}
