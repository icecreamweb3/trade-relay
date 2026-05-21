import { useMarketStore } from '../store/marketStore'
import { Locale, useTranslation } from '../i18n/translations'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'
const appVersion = __APP_VERSION__

function formatBuildTime(value: string, locale: Locale) {
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
  const locale = useUiPreferencesStore((state) => state.locale)
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
  const buildTime = formatBuildTime(__BUILD_TIME__, locale)
  const hasDayTicker = dayPriceChange !== null && dayPriceChangePercent !== null
  const priceChange = dayPriceChange ?? 0
  const priceChangePct = dayPriceChangePercent ?? 0
  const isUp = priceChange >= 0
  const priceTone = !hasDayTicker ? 'text-[#EAECEF]' : isUp ? 'text-buy' : 'text-sell'
  const fundingTone = fundingRate === null ? 'text-[#EAECEF]' : fundingRate >= 0 ? 'text-buy' : 'text-sell'
  const signedFundingRate = fundingRate === null ? '--' : `${fundingRate >= 0 ? '+' : ''}${(fundingRate * 100).toFixed(4)}%`

  return (
    <div className="h-6 border-t border-[#2B2F36] bg-[#11161c] flex items-center px-3 gap-4 text-xs text-[#EAECEF] select-none shrink-0 shadow-[inset_0_1px_0_rgba(255,255,255,0.02)]">
      <span className={`flex items-center gap-1 ${isConnected ? '' : 'opacity-60'}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-green-300' : 'bg-yellow-300'}`} />
        {isConnected ? t('statusbar.live') : t('statusbar.disconnected')}
      </span>
      <span className="text-[#4a5563]">|</span>
      <span className="font-semibold">{symbol}</span>
      {currentPrice !== null && (
        <>
          <span className={`font-mono font-bold ${priceTone}`}>{currentPrice.toFixed(2)}</span>
          <span className={`font-mono ${priceTone}`}>
            {hasDayTicker ? `${isUp ? '+' : ''}${priceChange.toFixed(2)} (${priceChangePct.toFixed(2)}%)` : '--'}
          </span>
        </>
      )}
      {markPrice !== null && (
        <>
          <span className="text-[#4a5563]">|</span>
          <span className="text-[#8b94a5]">{t('statusbar.markPrice')}: <span className="font-mono text-[#EAECEF]">{markPrice.toFixed(2)}</span></span>
        </>
      )}
      {fundingRate !== null && (
        <span className="text-[#8b94a5]">
          {t('statusbar.fundingRate')}: <span className={`font-mono ${fundingTone}`}>{signedFundingRate}</span>
        </span>
      )}
      <div className="ml-auto flex items-center gap-3 text-[11px] text-[#8b94a5] whitespace-nowrap">
        <span>{t('statusbar.version')}: <span className="font-mono text-[#EAECEF]">{appVersion}</span></span>
        <span>{t('statusbar.buildTime')}: <span className="font-mono text-[#EAECEF]">{buildTime}</span></span>
      </div>
    </div>
  )
}
