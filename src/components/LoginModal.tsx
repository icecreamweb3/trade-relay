import { useEffect, useRef, useState } from 'react'
import { X, Activity } from 'lucide-react'
import { useAuthStore } from '../store/authStore'
import { Locale, useTranslation } from '../i18n/translations'

const UI_LANG = (window as unknown as { electronAPI?: { uiLang?: string } }).electronAPI?.uiLang
const locale: Locale = (UI_LANG === 'en' ? 'en' : 'zh-CN')

interface LoginModalProps {
  onClose: () => void
}

export function LoginModal({ onClose }: LoginModalProps) {
  const { t } = useTranslation(locale)
  const { login, isLoading, error, clearError } = useAuthStore()

  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const usernameRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    usernameRef.current?.focus()
  }, [])

  useEffect(() => {
    if (error) {
      const timer = setTimeout(clearError, 4000)
      return () => clearTimeout(timer)
    }
  }, [error, clearError])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password.trim()) return
    await login(username.trim(), password)
    // App.tsx will close this modal via isAuthenticated effect
  }

  return (
    /* Backdrop */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      {/* Card */}
      <div className="relative w-80 bg-[#252526] rounded-lg border border-[#3e3e42] shadow-2xl p-6">
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute top-3 right-3 w-6 h-6 flex items-center justify-center rounded text-[#858585] hover:text-white hover:bg-[#3e3e42] transition-colors"
        >
          <X size={13} />
        </button>

        {/* Header */}
        <div className="mb-6 text-center">
          <div className="w-12 h-12 bg-[#007acc] rounded-xl flex items-center justify-center mx-auto mb-3">
            <Activity size={22} className="text-white" />
          </div>
          <h2 className="text-base font-semibold text-[#cccccc]">{t('login.title')}</h2>
          <p className="text-xs text-[#858585] mt-0.5">{t('login.subtitle')}</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="bg-red-900/30 border border-red-700/50 rounded px-3 py-2 text-red-400 text-xs">
              {error}
            </div>
          )}

          <div>
            <label className="block text-xs text-[#858585] mb-1.5">{t('login.username')}</label>
            <input
              ref={usernameRef}
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              className="w-full bg-[#1e1e1e] border border-[#3e3e42] rounded px-3 py-2 text-sm text-[#cccccc] outline-none focus:border-[#007acc] transition-colors selectable"
              onKeyDown={e => e.key === 'Enter' && document.getElementById('lm-password')?.focus()}
            />
          </div>

          <div>
            <label className="block text-xs text-[#858585] mb-1.5">{t('login.password')}</label>
            <input
              id="lm-password"
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              className="w-full bg-[#1e1e1e] border border-[#3e3e42] rounded px-3 py-2 text-sm text-[#cccccc] outline-none focus:border-[#007acc] transition-colors selectable"
            />
          </div>

          <button
            type="submit"
            disabled={isLoading || !username.trim() || !password.trim()}
            className="w-full py-2 bg-[#007acc] hover:bg-[#0069b3] disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium rounded transition-colors"
          >
            {isLoading ? t('login.loading') : t('login.submit')}
          </button>
        </form>
      </div>
    </div>
  )
}
