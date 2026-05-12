import { useState } from 'react'
import { api } from '../api/client'
import { Locale, useTranslation } from '../i18n/translations'

const UI_LANG = (window as unknown as { electronAPI?: { uiLang?: string } }).electronAPI?.uiLang
const locale: Locale = (UI_LANG === 'en' ? 'en' : 'zh-CN')

export function ConfigScreen() {
  const { t } = useTranslation(locale)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    const current = currentPassword.trim()
    const next = newPassword.trim()
    const confirm = confirmPassword.trim()

    if (!current || !next || !confirm) {
      setError(t('config.error.required'))
      return
    }

    if (next !== confirm) {
      setError(t('config.error.mismatch'))
      return
    }

    setSaving(true)
    setError('')
    try {
      await api.changeMyPassword({
        current_password: current,
        new_password: next,
      })
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (err: unknown) {
      setError((err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || t('config.error.required'))
    }
    setSaving(false)
  }

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e]">
      <div className="px-4 py-2 border-b border-[#3e3e42] shrink-0">
        <span className="text-sm font-semibold text-[#cccccc]">{t('config.title')}</span>
      </div>
      <form onSubmit={handleSave} className="p-4 space-y-4 max-w-lg">
        {error && <div className="text-xs text-red-400 bg-red-900/20 rounded px-2 py-1">{error}</div>}
        {saved && <div className="text-xs text-green-400 bg-green-900/20 rounded px-2 py-1">{t('config.success')}</div>}

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
