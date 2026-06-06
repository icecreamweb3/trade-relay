import { useState, useEffect } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { api } from '../api/client'
import { useToastStore } from '../store/toastStore'
import { Locale, translations, useTranslation } from '../i18n/translations'
import { OrderBookDepthMode, useUiPreferencesStore } from '../store/uiPreferencesStore'

type SettingsCategory = 'language' | 'password' | 'orderbook' | 'chart' | 'apikey'

export function ConfigScreen() {
  const locale = useUiPreferencesStore((state) => state.locale)
  const setLocale = useUiPreferencesStore((state) => state.setLocale)
  const orderBookDepthMode = useUiPreferencesStore((state) => state.orderBookDepthMode)
  const setOrderBookDepthMode = useUiPreferencesStore((state) => state.setOrderBookDepthMode)
  const chartOrderMarkersVisible = useUiPreferencesStore((state) => state.chartOrderMarkersVisible)
  const setChartOrderMarkersVisible = useUiPreferencesStore((state) => state.setChartOrderMarkersVisible)
  const chartOrderMarkerLabelsVisible = useUiPreferencesStore((state) => state.chartOrderMarkerLabelsVisible)
  const setChartOrderMarkerLabelsVisible = useUiPreferencesStore((state) => state.setChartOrderMarkerLabelsVisible)
  const { t } = useTranslation(locale)
  const [activeCategory, setActiveCategory] = useState<SettingsCategory>('language')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [saving, setSaving] = useState(false)

  // API Key settings
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [apiSecretMasked, setApiSecretMasked] = useState(false)
  const [apiTestnet, setApiTestnet] = useState(false)
  const [apiKeyLoading, setApiKeyLoading] = useState(false)
  const [apiKeySaving, setApiKeySaving] = useState(false)
  const [showApiSecret, setShowApiSecret] = useState(false)

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
