import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from '../i18n/translations'
import { useRiskWarningStore } from '../store/riskWarningStore'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'
import { useAuthStore } from '../store/authStore'

function formatCountdown(milliseconds: number) {
  const totalSeconds = Math.max(0, Math.ceil(milliseconds / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

export function GlobalRiskWarning() {
  const locale = useUiPreferencesStore((state) => state.locale)
  const enabled = useUiPreferencesStore((state) => state.riskWarningsEnabled)
  const warning = useRiskWarningStore((state) => state.warning)
  const username = useAuthStore((state) => state.user?.username)
  const dismissWarning = useRiskWarningStore((state) => state.dismissWarning)
  const expireWarning = useRiskWarningStore((state) => state.expireWarning)
  const [now, setNow] = useState(Date.now())
  const { t } = useTranslation(locale)

  useEffect(() => {
    if (enabled) return
    dismissWarning()
  }, [enabled, dismissWarning])

  useEffect(() => {
    if (!warning) return
    setNow(Date.now())
    const timer = window.setInterval(() => {
      setNow(Date.now())
      expireWarning()
    }, 1000)
    return () => window.clearInterval(timer)
  }, [warning, expireWarning])

  if (!enabled || !warning || !username || warning.username !== username) return null

  const message = warning.kind === 'LOSS_ADD'
    ? t('riskWarning.lossAdd.message', {
        symbol: warning.symbol ?? '—',
        pnl: Math.abs(warning.lossAmount ?? 0).toFixed(2),
      })
    : t('riskWarning.consecutiveLosses.message', { count: warning.consecutiveLosses ?? 2 })

  return createPortal(
    <section
      role="alertdialog"
      aria-live="assertive"
      aria-label={t('riskWarning.title')}
      className="fixed bottom-4 right-4 z-[10010] w-[min(400px,calc(100vw-32px))] overflow-hidden rounded-xl border border-[#F6465D]/60 bg-[#241419]/97 text-[#F5F5F5] shadow-[0_20px_60px_rgba(0,0,0,0.55)] ring-1 ring-[#F6465D]/15 backdrop-blur-md"
    >
      <div className="flex items-start gap-3 px-4 py-4 pr-12">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[#F6465D]/50 bg-[#F6465D]/15 text-xl font-bold text-[#ff6679]">!</span>
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-[#ff8796]">{t('riskWarning.title')}</h2>
          <p className="mt-1 text-[13px] leading-5 text-[#f3d9dd]">{message}</p>
          <p className="mt-2 text-xs text-[#c79da4]">
            {t('riskWarning.cooldownRemaining', { time: formatCountdown(warning.cooldownUntil - now) })}
          </p>
        </div>
      </div>
      <div className="h-1 bg-[#F6465D]/85" />
      <button
        type="button"
        onClick={dismissWarning}
        className="absolute right-3 top-3 flex h-8 w-8 items-center justify-center rounded text-2xl leading-none text-[#cfaeb3] transition-colors hover:bg-white/10 hover:text-white"
        aria-label={t('common.closeNotification')}
        title={t('riskWarning.dismiss')}
      >
        ×
      </button>
    </section>,
    document.body,
  )
}
