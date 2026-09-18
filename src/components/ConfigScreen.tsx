import { useState, useEffect } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { api } from '../api/client'
import { useAuthStore } from '../store/authStore'
import {
  AUTO_BREAKEVEN_PARAMETER_LIMITS,
  DEFAULT_AUTO_BREAKEVEN_PARAMETERS,
  useAutoBreakevenSettingsStore,
} from '../store/autoBreakevenSettingsStore'
import { useToastStore } from '../store/toastStore'
import { Locale, translations, useTranslation } from '../i18n/translations'
import { OrderBookDepthMode, useUiPreferencesStore } from '../store/uiPreferencesStore'
import {
  DEFAULT_RISK_WARNING_PARAMETERS,
  RISK_WARNING_PARAMETER_LIMITS,
  useRiskWarningSettingsStore,
} from '../store/riskWarningSettingsStore'

type SettingsCategory = 'language' | 'password' | 'riskProtection' | 'orderbook' | 'chart' | 'apikey'

export function ConfigScreen() {
  const currentUser = useAuthStore((state) => state.user)
  const username = currentUser?.username ?? ''
  const locale = useUiPreferencesStore((state) => state.locale)
  const setLocale = useUiPreferencesStore((state) => state.setLocale)
  const orderBookDepthMode = useUiPreferencesStore((state) => state.orderBookDepthMode)
  const setOrderBookDepthMode = useUiPreferencesStore((state) => state.setOrderBookDepthMode)
  const chartOrderMarkersVisible = useUiPreferencesStore((state) => state.chartOrderMarkersVisible)
  const setChartOrderMarkersVisible = useUiPreferencesStore((state) => state.setChartOrderMarkersVisible)
  const chartOrderMarkerLabelsVisible = useUiPreferencesStore((state) => state.chartOrderMarkerLabelsVisible)
  const setChartOrderMarkerLabelsVisible = useUiPreferencesStore((state) => state.setChartOrderMarkerLabelsVisible)
  const riskWarningsEnabled = useUiPreferencesStore((state) => state.riskWarningsEnabled)
  const setRiskWarningsEnabled = useUiPreferencesStore((state) => state.setRiskWarningsEnabled)
  const { t } = useTranslation(locale)
  const [activeCategory, setActiveCategory] = useState<SettingsCategory>('language')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [saving, setSaving] = useState(false)
  const autoBreakevenParameters = useAutoBreakevenSettingsStore((state) => (
    username
      ? state.byUsername[username] ?? DEFAULT_AUTO_BREAKEVEN_PARAMETERS
      : DEFAULT_AUTO_BREAKEVEN_PARAMETERS
  ))
  const loadAutoBreakevenParameters = useAutoBreakevenSettingsStore((state) => state.loadParameters)
  const setAutoBreakevenParameters = useAutoBreakevenSettingsStore((state) => state.setParameters)
  const [triggerRMultiple, setTriggerRMultiple] = useState(String(DEFAULT_AUTO_BREAKEVEN_PARAMETERS.triggerRMultiple))
  const [minimumProfitPercent, setMinimumProfitPercent] = useState(String(DEFAULT_AUTO_BREAKEVEN_PARAMETERS.minimumProfitPercent))
  const riskWarningParameters = useRiskWarningSettingsStore((state) => (
    username
      ? state.byUsername[username] ?? DEFAULT_RISK_WARNING_PARAMETERS
      : DEFAULT_RISK_WARNING_PARAMETERS
  ))
  const loadRiskWarningParameters = useRiskWarningSettingsStore((state) => state.loadParameters)
  const setRiskWarningParameters = useRiskWarningSettingsStore((state) => state.setParameters)
  const [tradeWindowMinutes, setTradeWindowMinutes] = useState(String(DEFAULT_RISK_WARNING_PARAMETERS.tradeWindowMinutes))
  const [tradeLimit, setTradeLimit] = useState(String(DEFAULT_RISK_WARNING_PARAMETERS.tradeLimit))
  const [cooldownMinutes, setCooldownMinutes] = useState(String(DEFAULT_RISK_WARNING_PARAMETERS.cooldownMinutes))
  const [consecutiveLossLimit, setConsecutiveLossLimit] = useState(String(DEFAULT_RISK_WARNING_PARAMETERS.consecutiveLossLimit))
  const [addPositionLimit, setAddPositionLimit] = useState(String(DEFAULT_RISK_WARNING_PARAMETERS.addPositionLimit))

  // API Key settings
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [apiSecretMasked, setApiSecretMasked] = useState(false)
  const [apiTestnet, setApiTestnet] = useState(false)
  const [apiKeyLoading, setApiKeyLoading] = useState(false)
  const [apiKeySaving, setApiKeySaving] = useState(false)
  const [showApiSecret, setShowApiSecret] = useState(false)

  useEffect(() => {
    if (!username) return
    loadAutoBreakevenParameters(username)
    loadRiskWarningParameters(username)
  }, [loadAutoBreakevenParameters, loadRiskWarningParameters, username])

  useEffect(() => {
    if (activeCategory !== 'riskProtection') return
    setTriggerRMultiple(String(autoBreakevenParameters.triggerRMultiple))
    setMinimumProfitPercent(String(autoBreakevenParameters.minimumProfitPercent))
    setTradeWindowMinutes(String(riskWarningParameters.tradeWindowMinutes))
    setTradeLimit(String(riskWarningParameters.tradeLimit))
    setCooldownMinutes(String(riskWarningParameters.cooldownMinutes))
    setConsecutiveLossLimit(String(riskWarningParameters.consecutiveLossLimit))
    setAddPositionLimit(String(riskWarningParameters.addPositionLimit))
  }, [activeCategory, autoBreakevenParameters, riskWarningParameters])

  useEffect(() => {
    if (activeCategory !== 'apikey') return
    setApiKeyLoading(true)
    api.getMyConfig()
      .then(data => {
        setApiKey(data.api_key || '')
        setApiSecret(data.api_secret || '')
        setApiSecretMasked(data.api_secret?.includes('*') ?? false)
        setApiTestnet(data.testnet || false)
      })
      .catch(() => {/* silently ignore */})
      .finally(() => setApiKeyLoading(false))
  }, [activeCategory])
  const showToast = useToastStore((state) => state.showToast)

  const translateForLocale = (targetLocale: Locale, key: string) => {
    return translations[targetLocale]?.[key] ?? translations.en[key] ?? key
  }

  const handleLocaleChange = (nextLocale: Locale) => {
    if (nextLocale === locale) return
    setLocale(nextLocale)
    showToast('success', translateForLocale(nextLocale, 'config.languageUpdated'))
  }

  const handleDepthModeChange = (mode: OrderBookDepthMode) => {
    if (mode === orderBookDepthMode) return
    setOrderBookDepthMode(mode)
    showToast('success', translateForLocale(locale, 'config.orderBookDepthUpdated'))
  }

  const handleChartOrderMarkersVisibleChange = (visible: boolean) => {
    if (visible === chartOrderMarkersVisible) return
    setChartOrderMarkersVisible(visible)
    showToast('success', translateForLocale(locale, 'config.chartOrderMarkersUpdated'))
  }

  const handleChartOrderMarkerLabelsVisibleChange = (visible: boolean) => {
    if (visible === chartOrderMarkerLabelsVisible) return
    setChartOrderMarkerLabelsVisible(visible)
    showToast('success', translateForLocale(locale, 'config.chartOrderMarkerLabelsUpdated'))
  }

  const handleRiskWarningsEnabledChange = (enabled: boolean) => {
    if (enabled === riskWarningsEnabled) return
    setRiskWarningsEnabled(enabled)
    showToast('success', translateForLocale(locale, 'config.riskWarningsUpdated'))
  }

  const handleSaveRiskWarnings = (e: React.FormEvent) => {
    e.preventDefault()
    const next = {
      tradeWindowMinutes: Number(tradeWindowMinutes),
      tradeLimit: Number(tradeLimit),
      cooldownMinutes: Number(cooldownMinutes),
      consecutiveLossLimit: Number(consecutiveLossLimit),
      addPositionLimit: Number(addPositionLimit),
    }
    const valid = username && Object.entries(next).every(([key, value]) => {
      const limits = RISK_WARNING_PARAMETER_LIMITS[key as keyof typeof RISK_WARNING_PARAMETER_LIMITS]
      return Number.isInteger(value) && value >= limits.min && value <= limits.max
    })
    if (!valid) {
      showToast('error', t('config.riskWarnings.invalid'))
      return
    }
    setRiskWarningParameters(username, next)
    showToast('success', t('config.riskWarnings.saved'))
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    const current = currentPassword.trim()
    const next = newPassword.trim()
    const confirm = confirmPassword.trim()

    if (!current || !next || !confirm) {
      showToast('error', t('config.error.required'))
      return
    }

    if (next !== confirm) {
      showToast('error', t('config.error.mismatch'))
      return
    }

    setSaving(true)
    try {
      await api.changeMyPassword({
        current_password: current,
        new_password: next,
      })
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      showToast('success', t('config.success'))
    } catch (err: unknown) {
      showToast('error', (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || t('config.error.required'))
    }
    setSaving(false)
  }

  const handleSaveApiKey = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!apiKey.trim()) {
      showToast('error', t('config.error.apiKeyRequired'))
      return
    }
    if (!apiSecretMasked && !apiSecret.trim()) {
      showToast('error', t('config.error.apiSecretRequired'))
      return
    }
    setApiKeySaving(true)
    try {
      await api.saveMyConfig({ api_key: apiKey.trim(), api_secret: apiSecretMasked ? '***keep***' : apiSecret.trim(), testnet: apiTestnet })
      showToast('success', t('config.apiKeySaved'))
    } catch (err: unknown) {
      showToast('error', (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || t('config.error.saveApiKeyFailed'))
    }
    setApiKeySaving(false)
  }

  const handleSaveRiskProtection = (e: React.FormEvent) => {
    e.preventDefault()
    const nextTriggerRMultiple = Number(triggerRMultiple)
    const nextMinimumProfitPercent = Number(minimumProfitPercent)
    const triggerLimits = AUTO_BREAKEVEN_PARAMETER_LIMITS.triggerRMultiple
    const profitLimits = AUTO_BREAKEVEN_PARAMETER_LIMITS.minimumProfitPercent
    if (
      !username
      || !Number.isFinite(nextTriggerRMultiple)
      || nextTriggerRMultiple < triggerLimits.min
      || nextTriggerRMultiple > triggerLimits.max
      || !Number.isFinite(nextMinimumProfitPercent)
      || nextMinimumProfitPercent < profitLimits.min
      || nextMinimumProfitPercent > profitLimits.max
    ) {
      showToast('error', t('config.riskProtection.invalid'))
      return
    }
    setAutoBreakevenParameters(username, {
      triggerRMultiple: nextTriggerRMultiple,
      minimumProfitPercent: nextMinimumProfitPercent,
    })
    showToast('success', t('config.riskProtection.saved'))
  }

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e]">
      <div className="px-4 py-2 border-b border-[#3e3e42] shrink-0">
        <span className="text-sm font-semibold text-[#cccccc]">{t('config.title')}</span>
      </div>
      <div className="flex flex-1 overflow-hidden">
        <div className="w-[220px] shrink-0 border-r border-[#2b2f36] bg-[#181b20] p-3">
          <div className="space-y-1">
            {([
              ['language', t('config.category.language')],
              ['password', t('config.category.password')],
              ['apikey', t('config.category.apikey')],
              ['riskProtection', t('config.category.riskProtection')],
              ['orderbook', t('config.category.orderbook')],
              ['chart', t('config.category.chart')],
            ] as Array<[SettingsCategory, string]>).map(([category, label]) => (
              <button
                key={category}
                type="button"
                onClick={() => setActiveCategory(category)}
                className={`flex w-full items-center rounded px-3 py-2 text-left text-sm transition-colors ${
                  activeCategory === category
                    ? 'bg-[#232831] text-[#EAECEF]'
                    : 'text-[#9aa3b2] hover:bg-[#20242b] hover:text-[#EAECEF]'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-auto p-4">
          {activeCategory === 'language' && (
            <section className="max-w-2xl space-y-4">
              <div>
                <h2 className="text-base font-semibold text-[#e6ebf2]">{t('config.category.language')}</h2>
                <p className="mt-1 text-sm text-[#8b94a5]">{t('config.languageDescription')}</p>
              </div>
              <div className="grid max-w-md gap-2">
                {([
                  ['zh-CN', t('config.language.zhCN')],
                  ['en', t('config.language.en')],
                ] as Array<[Locale, string]>).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => handleLocaleChange(value)}
                    className={`flex items-center justify-between rounded border px-3 py-2 text-sm transition-colors ${
                      locale === value
                        ? 'border-[#007acc] bg-[#14263a] text-[#EAECEF]'
                        : 'border-[#2d3542] bg-[#0d131a] text-[#c5ccd8] hover:border-[#3a4454] hover:text-[#EAECEF]'
                    }`}
                  >
                    <span>{label}</span>
                    <span className={locale === value ? 'text-[#4da3ff]' : 'text-transparent'}>✓</span>
                  </button>
                ))}
              </div>
            </section>
          )}

          {activeCategory === 'password' && (
            <section className="max-w-lg space-y-4">
              <div>
                <h2 className="text-base font-semibold text-[#e6ebf2]">{t('config.category.password')}</h2>
                <p className="mt-1 text-sm text-[#8b94a5]">{t('config.passwordDescription')}</p>
              </div>
              <form onSubmit={handleSave} className="space-y-4">
                <Field label={t('config.currentPassword')}>
                  <input type="password" value={currentPassword} onChange={e => setCurrentPassword(e.target.value)}
                    placeholder={t('config.placeholder.currentPassword')} className={INPUT_CLS} />
                </Field>
                <Field label={t('config.newPassword')}>
                  <input type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)}
                    placeholder={t('config.placeholder.newPassword')} className={INPUT_CLS} />
                </Field>

                <Field label={t('config.confirmPassword')}>
                  <input type="password" value={confirmPassword} onChange={e => setConfirmPassword(e.target.value)}
                    placeholder={t('config.placeholder.confirmPassword')} className={INPUT_CLS} />
                </Field>

                <button type="submit" disabled={saving}
                  className="px-6 py-2 bg-[#007acc] hover:bg-blue-600 disabled:opacity-50 text-white text-sm rounded">
                  {saving ? t('config.saving') : t('config.save')}
                </button>
              </form>
            </section>
          )}

          {activeCategory === 'apikey' && (
            <section className="max-w-lg space-y-4">
              <div>
                <h2 className="text-base font-semibold text-[#e6ebf2]">{t('config.category.apikey')}</h2>
                <p className="mt-1 text-sm text-[#8b94a5]">{t('config.apiKeyDescription')}</p>
              </div>
              {apiKeyLoading ? (
                <p className="text-sm text-[#8b94a5]">{t('config.apiKeyLoading')}</p>
              ) : (
                <form onSubmit={handleSaveApiKey} className="space-y-4">
                  <Field label={t('config.apiKey')}>
                    <input
                      type="text"
                      value={apiKey}
                      onChange={e => setApiKey(e.target.value)}
                      placeholder={t('config.placeholder.apiKey')}
                      autoComplete="off"
                      spellCheck={false}
                      className={INPUT_CLS}
                    />
                  </Field>
                  <Field label={t('config.apiSecret')}>
                    <div className="relative">
                      <input
                        type={showApiSecret ? 'text' : 'password'}
                        value={apiSecret}
                        onChange={e => { setApiSecret(e.target.value); setApiSecretMasked(false) }}
                        onFocus={() => { if (apiSecretMasked) { setApiSecret(''); setApiSecretMasked(false) } }}
                        placeholder={t('config.placeholder.apiSecret')}
                        autoComplete="off"
                        spellCheck={false}
                        className={`${INPUT_CLS} pr-10`}
                      />
                      <button
                        type="button"
                        onClick={() => setShowApiSecret(v => !v)}
                        className="absolute right-2 top-1/2 -translate-y-1/2 text-[#858585] hover:text-[#cccccc] transition-colors"
                        tabIndex={-1}
                      >
                        {showApiSecret ? <EyeOff size={15} /> : <Eye size={15} />}
                      </button>
                    </div>
                  </Field>
                  <label className="flex items-center gap-2 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={apiTestnet}
                      onChange={e => setApiTestnet(e.target.checked)}
                      className="w-4 h-4 accent-[#007acc] cursor-pointer"
                    />
                    <span className="text-sm text-[#c5ccd8]">{t('config.apiKeyTestnet')}</span>
                  </label>
                  <button
                    type="submit"
                    disabled={apiKeySaving}
                    className="px-6 py-2 bg-[#007acc] hover:bg-blue-600 disabled:opacity-50 text-white text-sm rounded"
                  >
                    {apiKeySaving ? t('config.saving') : t('config.saveApiKey')}
                  </button>
                </form>
              )}
            </section>
          )}

          {activeCategory === 'riskProtection' && (
            <section className="max-w-2xl space-y-4">
              <div>
                <h2 className="text-base font-semibold text-[#e6ebf2]">{t('config.category.riskProtection')}</h2>
                <p className="mt-1 text-sm text-[#8b94a5]">{t('config.riskProtectionDescription')}</p>
              </div>
              <div>
                <h3 className="text-sm font-medium text-[#d6dbe4]">{t('config.riskWarnings.title')}</h3>
                <p className="mt-1 text-sm text-[#8b94a5]">{t('config.riskWarnings.description')}</p>
              </div>
              <div className="grid max-w-md gap-2">
                {([
                  [true, t('config.riskWarnings.enable')],
                  [false, t('config.riskWarnings.disable')],
                ] as Array<[boolean, string]>).map(([enabled, label]) => (
                  <button
                    key={`risk-warning-${String(enabled)}`}
                    type="button"
                    onClick={() => handleRiskWarningsEnabledChange(enabled)}
                    className={`flex items-center justify-between rounded border px-3 py-2 text-sm transition-colors ${
                      riskWarningsEnabled === enabled
                        ? 'border-[#007acc] bg-[#14263a] text-[#EAECEF]'
                        : 'border-[#2d3542] bg-[#0d131a] text-[#c5ccd8] hover:border-[#3a4454] hover:text-[#EAECEF]'
                    }`}
                  >
                    <span>{label}</span>
                    <span className={riskWarningsEnabled === enabled ? 'text-[#4da3ff]' : 'text-transparent'}>✓</span>
                  </button>
                ))}
              </div>
              <form onSubmit={handleSaveRiskWarnings} className="max-w-lg space-y-4 rounded border border-[#2d3542] bg-[#15191f] p-4">
                <div className="grid grid-cols-2 gap-4">
                  <RiskParameterField
                    label={t('config.riskWarnings.tradeWindowMinutes')}
                    value={tradeWindowMinutes}
                    onChange={setTradeWindowMinutes}
                    min={RISK_WARNING_PARAMETER_LIMITS.tradeWindowMinutes.min}
                    max={RISK_WARNING_PARAMETER_LIMITS.tradeWindowMinutes.max}
                    unit={t('config.riskWarnings.unit.minutes')}
                  />
                  <RiskParameterField
                    label={t('config.riskWarnings.tradeLimit')}
                    value={tradeLimit}
                    onChange={setTradeLimit}
                    min={RISK_WARNING_PARAMETER_LIMITS.tradeLimit.min}
                    max={RISK_WARNING_PARAMETER_LIMITS.tradeLimit.max}
                    unit={t('config.riskWarnings.unit.trades')}
                  />
                  <RiskParameterField
                    label={t('config.riskWarnings.cooldownMinutes')}
                    value={cooldownMinutes}
                    onChange={setCooldownMinutes}
                    min={RISK_WARNING_PARAMETER_LIMITS.cooldownMinutes.min}
                    max={RISK_WARNING_PARAMETER_LIMITS.cooldownMinutes.max}
                    unit={t('config.riskWarnings.unit.minutes')}
                  />
                  <RiskParameterField
                    label={t('config.riskWarnings.consecutiveLossLimit')}
                    value={consecutiveLossLimit}
                    onChange={setConsecutiveLossLimit}
                    min={RISK_WARNING_PARAMETER_LIMITS.consecutiveLossLimit.min}
                    max={RISK_WARNING_PARAMETER_LIMITS.consecutiveLossLimit.max}
                    unit={t('config.riskWarnings.unit.trades')}
                  />
                  <RiskParameterField
                    label={t('config.riskWarnings.addPositionLimit')}
                    value={addPositionLimit}
                    onChange={setAddPositionLimit}
                    min={RISK_WARNING_PARAMETER_LIMITS.addPositionLimit.min}
                    max={RISK_WARNING_PARAMETER_LIMITS.addPositionLimit.max}
                    unit={t('config.riskWarnings.unit.times')}
                  />
                </div>
                <p className="text-xs leading-5 text-[#8b94a5]">{t('config.riskWarnings.parameterHint')}</p>
                <button type="submit" className="rounded bg-[#007acc] px-6 py-2 text-sm text-white hover:bg-blue-600">
                  {t('config.riskWarnings.save')}
                </button>
              </form>
              <div className="border-t border-[#2d3542] pt-4">
                <h3 className="text-sm font-medium text-[#d6dbe4]">{t('config.riskProtection.autoBreakevenTitle')}</h3>
              </div>
              <form onSubmit={handleSaveRiskProtection} className="max-w-lg space-y-4">
                <Field label={t('config.riskProtection.triggerRMultiple')}>
                  <div className="relative">
                    <input
                      type="number"
                      min={AUTO_BREAKEVEN_PARAMETER_LIMITS.triggerRMultiple.min}
                      max={AUTO_BREAKEVEN_PARAMETER_LIMITS.triggerRMultiple.max}
                      step="0.1"
                      value={triggerRMultiple}
                      onChange={e => setTriggerRMultiple(e.target.value)}
                      className={`${INPUT_CLS} pr-9`}
                    />
                    <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-[#858585]">R</span>
                  </div>
                  <p className="mt-1 text-xs text-[#697386]">{t('config.riskProtection.triggerRMultipleDescription')}</p>
                </Field>
                <Field label={t('config.riskProtection.minimumProfitPercent')}>
                  <div className="relative">
                    <input
                      type="number"
                      min={AUTO_BREAKEVEN_PARAMETER_LIMITS.minimumProfitPercent.min}
                      max={AUTO_BREAKEVEN_PARAMETER_LIMITS.minimumProfitPercent.max}
                      step="0.1"
                      value={minimumProfitPercent}
                      onChange={e => setMinimumProfitPercent(e.target.value)}
                      className={`${INPUT_CLS} pr-9`}
                    />
                    <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-[#858585]">%</span>
                  </div>
                  <p className="mt-1 text-xs text-[#697386]">{t('config.riskProtection.minimumProfitPercentDescription')}</p>
                </Field>
                <div className="rounded border border-[#2d3542] bg-[#15191f] px-3 py-2.5 text-sm text-[#b7c0ce]">
                  <div>
                    <span className="text-[#858f9f]">{t('config.riskProtection.formula')}:</span>
                    <span className="ml-2 font-mono text-[#e6ebf2]">max({triggerRMultiple || '—'}R, {minimumProfitPercent || '—'}%)</span>
                  </div>
                  <p className="mt-1.5 text-xs text-[#8b94a5]">{t('config.riskProtection.stopTarget')}</p>
                </div>
                <button type="submit" className="rounded bg-[#007acc] px-6 py-2 text-sm text-white hover:bg-blue-600">
                  {t('config.riskProtection.save')}
                </button>
              </form>
            </section>
          )}

          {activeCategory === 'orderbook' && (
            <section className="max-w-2xl space-y-4">
              <div>
                <h2 className="text-base font-semibold text-[#e6ebf2]">{t('config.category.orderbook')}</h2>
                <p className="mt-1 text-sm text-[#8b94a5]">{t('config.orderBookDepthDescription')}</p>
              </div>
              <div className="grid max-w-md gap-2">
                {([
                  ['level', t('config.orderBookDepth.level')],
                  ['cumulative', t('config.orderBookDepth.cumulative')],
                ] as Array<[OrderBookDepthMode, string]>).map(([mode, label]) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => handleDepthModeChange(mode)}
                    className={`flex items-center justify-between rounded border px-3 py-2 text-sm transition-colors ${
                      orderBookDepthMode === mode
                        ? 'border-[#007acc] bg-[#14263a] text-[#EAECEF]'
                        : 'border-[#2d3542] bg-[#0d131a] text-[#c5ccd8] hover:border-[#3a4454] hover:text-[#EAECEF]'
                    }`}
                  >
                    <span>{label}</span>
                    <span className={orderBookDepthMode === mode ? 'text-[#4da3ff]' : 'text-transparent'}>✓</span>
                  </button>
                ))}
              </div>
            </section>
          )}

          {activeCategory === 'chart' && (
            <section className="max-w-2xl space-y-4">
              <div>
                <h2 className="text-base font-semibold text-[#e6ebf2]">{t('config.category.chart')}</h2>
                <p className="mt-1 text-sm text-[#8b94a5]">{t('config.chartOrderMarkersDescription')}</p>
              </div>
              <div className="grid max-w-md gap-2">
                {([
                  [true, t('config.chartOrderMarkers.show')],
                  [false, t('config.chartOrderMarkers.hide')],
                ] as Array<[boolean, string]>).map(([visible, label]) => (
                  <button
                    key={String(visible)}
                    type="button"
                    onClick={() => handleChartOrderMarkersVisibleChange(visible)}
                    className={`flex items-center justify-between rounded border px-3 py-2 text-sm transition-colors ${
                      chartOrderMarkersVisible === visible
                        ? 'border-[#007acc] bg-[#14263a] text-[#EAECEF]'
                        : 'border-[#2d3542] bg-[#0d131a] text-[#c5ccd8] hover:border-[#3a4454] hover:text-[#EAECEF]'
                    }`}
                  >
                    <span>{label}</span>
                    <span className={chartOrderMarkersVisible === visible ? 'text-[#4da3ff]' : 'text-transparent'}>✓</span>
                  </button>
                ))}
              </div>
              <div>
                <h3 className="text-sm font-medium text-[#d6dbe4]">{t('config.chartOrderMarkerLabelsTitle')}</h3>
                <p className="mt-1 text-sm text-[#8b94a5]">{t('config.chartOrderMarkerLabelsDescription')}</p>
              </div>
              <div className="grid max-w-md gap-2">
                {([
                  [true, t('config.chartOrderMarkerLabels.show')],
                  [false, t('config.chartOrderMarkerLabels.hide')],
                ] as Array<[boolean, string]>).map(([visible, label]) => (
                  <button
                    key={`label-${String(visible)}`}
                    type="button"
                    onClick={() => handleChartOrderMarkerLabelsVisibleChange(visible)}
                    disabled={!chartOrderMarkersVisible}
                    className={`flex items-center justify-between rounded border px-3 py-2 text-sm transition-colors ${
                      chartOrderMarkerLabelsVisible === visible
                        ? 'border-[#007acc] bg-[#14263a] text-[#EAECEF]'
                        : 'border-[#2d3542] bg-[#0d131a] text-[#c5ccd8] hover:border-[#3a4454] hover:text-[#EAECEF]'
                    } ${!chartOrderMarkersVisible ? 'cursor-not-allowed opacity-50 hover:border-[#2d3542] hover:text-[#c5ccd8]' : ''}`}
                  >
                    <span>{label}</span>
                    <span className={chartOrderMarkerLabelsVisible === visible ? 'text-[#4da3ff]' : 'text-transparent'}>✓</span>
                  </button>
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  )
}

const INPUT_CLS = 'w-full bg-[#1e1e1e] border border-[#3e3e42] text-sm text-[#cccccc] rounded px-2 py-1.5 outline-none selectable focus:border-[#007acc]'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-[#858585] mb-1">{label}</label>
      {children}
    </div>
  )
}

function RiskParameterField({
  label,
  value,
  onChange,
  min,
  max,
  unit,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  min: number
  max: number
  unit: string
}) {
  return (
    <Field label={label}>
      <div className="relative">
        <input
          type="number"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          min={min}
          max={max}
          step="1"
          className={`${INPUT_CLS} pr-12`}
        />
        <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-[#858585]">{unit}</span>
      </div>
    </Field>
  )
}
