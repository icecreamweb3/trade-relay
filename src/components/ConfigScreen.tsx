import { useState, useEffect } from 'react'
import { api } from '../api/client'

interface Config {
  binance_api_key?: string; binance_api_secret?: string
  testnet?: boolean; mock_mode?: boolean
}

export function ConfigScreen() {
  const [form, setForm] = useState<Config>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const load = async () => {
      setLoading(true)
      try { setForm(await api.getMyConfig()) } catch { /* ignore */ }
      setLoading(false)
    }
    load()
  }, [])

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true); setError('')
    try {
      await api.saveMyConfig(form)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (err: unknown) {
      setError((err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '保存失败')
    }
    setSaving(false)
  }

  if (loading) return <div className="flex-1 flex items-center justify-center text-[#858585]">加载中...</div>

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e]">
      <div className="px-4 py-2 border-b border-[#3e3e42] shrink-0">
        <span className="text-sm font-semibold text-[#cccccc]">Binance API 配置</span>
      </div>
      <form onSubmit={handleSave} className="p-4 space-y-4 max-w-lg">
        {error && <div className="text-xs text-red-400 bg-red-900/20 rounded px-2 py-1">{error}</div>}
        {saved && <div className="text-xs text-green-400 bg-green-900/20 rounded px-2 py-1">配置已保存</div>}

        <Field label="API Key">
          <input type="text" value={form.binance_api_key || ''} onChange={e => setForm(f => ({ ...f, binance_api_key: e.target.value }))}
            placeholder="留空不修改" className={INPUT_CLS} />
        </Field>
        <Field label="API Secret">
          <input type="password" value={form.binance_api_secret || ''} onChange={e => setForm(f => ({ ...f, binance_api_secret: e.target.value }))}
            placeholder="留空不修改" className={INPUT_CLS} />
        </Field>

        <div className="flex gap-6">
          <Toggle label="测试网" checked={!!form.testnet} onChange={v => setForm(f => ({ ...f, testnet: v }))} />
          <Toggle label="模拟交易（不下真实订单）" checked={!!form.mock_mode} onChange={v => setForm(f => ({ ...f, mock_mode: v }))} />
        </div>

        <button type="submit" disabled={saving}
          className="px-6 py-2 bg-[#007acc] hover:bg-blue-600 disabled:opacity-50 text-white text-sm rounded">
          {saving ? '保存中...' : '保存配置'}
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

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 cursor-pointer">
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} className="w-3.5 h-3.5 accent-[#007acc]" />
      <span className="text-xs text-[#858585]">{label}</span>
    </label>
  )
}
