import { useMarketStore } from '../store/marketStore'
import { Locale, useTranslation } from '../i18n/translations'

const UI_LANG = (window as unknown as { electronAPI?: { uiLang?: string } }).electronAPI?.uiLang
const locale: Locale = (UI_LANG === 'en' ? 'en' : 'zh-CN')
const appVersion = __APP_VERSION__

function formatBuildTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(locale === 'en' ? 'en-US' : 'zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

export function StatusBar() {
  const { t } = useTranslation(locale)
  const {
    symbol,
    currentPrice,
    dayPriceChange,
    dayPriceChangePercent,
    markPrice,
    fundingRate,
    isConnected,
  } = useMarketStore()
  const buildTime = formatBuildTime(__BUILD_TIME__)
  const hasDayTicker = dayPriceChange !== null && dayPriceChangePercent !== null
  const priceChange = dayPriceChange ?? 0
  const priceChangePct = dayPriceChangePercent ?? 0
  const isUp = priceChange >= 0
  const priceTone = !hasDayTicker ? 'text-white' : isUp ? 'text-green-300' : 'text-red-300'

  return (
    <div className="h-6 bg-[#007acc] flex items-center px-3 gap-4 text-xs text-white select-none shrink-0">
      <span className={`flex items-center gap-1 ${isConnected ? '' : 'opacity-60'}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-green-300' : 'bg-yellow-300'}`} />
        {isConnected ? t('statusbar.live') : t('statusbar.disconnected')}
      </span>
      <span className="text-blue-200">|</span>
      <span className="font-semibold">{symbol}</span>
      {currentPrice !== null && (
        <>
          <span className={`font-mono font-bold ${priceTone}`}>{currentPrice.toFixed(2)}</span>
          <span className={priceTone}>
            {hasDayTicker ? `${isUp ? '+' : ''}${priceChange.toFixed(2)} (${priceChangePct.toFixed(2)}%)` : '--'}
          </span>
        </>
      )}
      {markPrice !== null && (
        <><span className="text-blue-200">|</span><span className="text-blue-100">{t('statusbar.markPrice')}: <span className="font-mono">{markPrice.toFixed(2)}</span></span></>
      )}
      {fundingRate !== null && (
        <span className={fundingRate >= 0 ? 'text-green-300' : 'text-red-300'}>
          {t('statusbar.fundingRate')}: {(fundingRate * 100).toFixed(4)}%
        </span>
      )}
      <div className="ml-auto flex items-center gap-3 text-[11px] text-blue-100 whitespace-nowrap">
        <span>{t('statusbar.version')}: <span className="font-mono text-white">{appVersion}</span></span>
        <span>{t('statusbar.buildTime')}: <span className="font-mono text-white">{buildTime}</span></span>
      </div>
    </div>
  )
}
