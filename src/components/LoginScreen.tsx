import { useState, useEffect } from 'react'
import { useAuthStore } from '../store/authStore'
import { useToastStore } from '../store/toastStore'
import { Locale, useTranslation } from '../i18n/translations'
import { perf } from '../utils/perf'

const UI_LANG = (window as unknown as { electronAPI?: { uiLang?: string } }).electronAPI?.uiLang
const locale: Locale = (UI_LANG === 'en' ? 'en' : 'zh-CN')

export function LoginScreen() {
  const { t } = useTranslation(locale)
  const { login, isLoading, error, clearError } = useAuthStore()
  const showToast = useToastStore((state) => state.showToast)

  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')

  useEffect(() => {
    if (error) {
      showToast('error', error)
      clearError()
    }
  }, [error, clearError, showToast])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password.trim()) return
    perf.start('login-submit')
    await login(username.trim(), password)
  }

  return (
    <div className="h-screen bg-[#1e1e1e] flex flex-col items-center justify-center">
      {/* App icon + title */}
      <div className="mb-8 text-center">
        <div className="w-16 h-16 bg-[#007acc] rounded-2xl flex items-center justify-center mx-auto mb-4">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2">
            <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
            <polyline points="16 7 22 7 22 13" />
          </svg>
        </div>
        <h1 className="text-2xl font-bold text-[#cccccc]">{t('login.title')}</h1>
        <p className="text-sm text-[#858585] mt-1">{t('login.subtitle')}</p>
      </div>

      {/* Login card */}
      <form
        onSubmit={handleSubmit}
        className="w-80 bg-[#252526] rounded-lg border border-[#3e3e42] p-6 space-y-4"
      >
        {/* Username */}
        <div>
          <label className="block text-xs text-[#858585] mb-1.5">{t('login.username')}</label>
          <input
            type="text"
            value={username}
            onChange={e => setUsername(e.target.value)}
            autoFocus
            className="w-full bg-[#1e1e1e] border border-[#3e3e42] rounded px-3 py-2 text-sm text-[#cccccc] outline-none focus:border-[#007acc] transition-colors selectable"
            onKeyDown={e => e.key === 'Enter' && document.getElementById('tr-password')?.focus()}
          />
        </div>

        {/* Password */}
        <div>
          <label className="block text-xs text-[#858585] mb-1.5">{t('login.password')}</label>
          <input
            id="tr-password"
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            className="w-full bg-[#1e1e1e] border border-[#3e3e42] rounded px-3 py-2 text-sm text-[#cccccc] outline-none focus:border-[#007acc] transition-colors selectable"
          />
        </div>

        {/* Submit */}
        <button
          type="submit"
          disabled={isLoading}
          className="w-full bg-[#007acc] hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded py-2 text-sm font-semibold transition-colors"
        >
          {isLoading ? t('login.loading') : t('login.submit')}
        </button>
      </form>
    </div>
  )
}
