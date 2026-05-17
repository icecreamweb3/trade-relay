import { useEffect } from 'react'
import { useToastStore } from '../store/toastStore'
import { Locale, useTranslation } from '../i18n/translations'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'

export function GlobalToast() {
  const locale = useUiPreferencesStore((state) => state.locale)
  const toast = useToastStore((state) => state.toast)
  const dismissToast = useToastStore((state) => state.dismissToast)
  const { t } = useTranslation(locale)

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => dismissToast(), toast.duration)
    return () => clearTimeout(timer)
  }, [toast, dismissToast])

  if (!toast) return null

  return (
    <div className="floating-toast fixed right-4 bottom-4 z-50 w-[min(320px,calc(100vw-32px))] overflow-hidden rounded-[24px] border border-white/6 bg-[#3A4048]/94 text-[13px] text-[#F5F5F5] shadow-[0_18px_44px_rgba(8,12,18,0.28)] ring-1 ring-white/5 backdrop-blur-md">
      <div className="flex items-center gap-3 px-4 py-3 pr-11">
        <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-[14px] font-semibold ${
          toast.kind === 'success'
            ? 'border-[#0ECB81]/40 bg-[#0ECB81]/12 text-[#0ECB81]'
            : toast.kind === 'error'
              ? 'border-[#F6465D]/40 bg-[#F6465D]/12 text-[#F6465D]'
              : 'border-[#F0B90B]/40 bg-[#F0B90B]/12 text-[#F0B90B]'
        }`}>
          {toast.kind === 'success' ? '✓' : toast.kind === 'error' ? '!' : 'i'}
        </span>
        <span className="leading-5 tracking-[0.01em] text-[#F2F4F7]">{toast.msg}</span>
      </div>
      <div className={`h-[3px] w-full ${
        toast.kind === 'success'
          ? 'bg-[#0ECB81]/85'
          : toast.kind === 'error'
            ? 'bg-[#F6465D]/85'
            : 'bg-[#F0B90B]/85'
      }`} />
      <button
        type="button"
        onClick={dismissToast}
        className="absolute right-3 top-1/2 -translate-y-1/2 text-[24px] leading-none text-[#A9B1BA] transition-colors hover:text-white"
        aria-label={t('common.closeNotification')}
      >
        ×
      </button>
    </div>
  )
}