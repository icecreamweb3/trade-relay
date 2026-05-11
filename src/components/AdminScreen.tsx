import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import { useAuthStore } from '../store/authStore'

interface User {
  id: number; username: string; role: string; is_active: boolean
  binance_api_key?: string; created_at?: string
}

export function AdminScreen() {
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [editUser, setEditUser] = useState<User | null>(null)
  const { user: me } = useAuthStore()

  const load = useCallback(async () => {
    setLoading(true)
    try { setUsers(await api.getUsers()) } catch { /* ignore */ }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const handleToggleActive = async (u: User) => {
    try {
      await api.updateUser(u.id, { is_active: !u.is_active })
      load()
    } catch { /* ignore */ }
  }

  const handleDelete = async (u: User) => {
    if (!confirm(`确认删除用户 ${u.username}？`)) return
    try { await api.deleteUser(u.id); load() } catch { /* ignore */ }
  }

  return (
    <div className="h-full flex flex-col bg-[#1e1e1e]">
      {/* Header */}
      <div className="px-4 py-2 border-b border-[#3e3e42] flex items-center gap-3 shrink-0">
        <span className="text-sm font-semibold text-[#cccccc]">用户管理</span>
        <button
          onClick={() => { setEditUser(null); setShowForm(true) }}
          className="ml-auto px-3 py-1 bg-[#007acc] hover:bg-blue-600 text-white text-xs rounded"
        >
          + 新建用户
        </button>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        <table className="trade-table w-full">
          <thead><tr>
            <th>用户名</th><th>角色</th><th>状态</th><th>API Key</th><th>创建时间</th><th>操作</th>
          </tr></thead>
          <tbody>
            {users.length === 0 ? (
              <tr><td colSpan={6} className="text-center text-[#858585] py-6">{loading ? '...' : '暂无用户'}</td></tr>
            ) : users.map(u => (
              <tr key={u.id}>
                <td className="font-semibold">{u.username}{u.id === me?.id && <span className="ml-1 text-[#007acc] text-xs">(我)</span>}</td>
                <td><span className={`badge ${u.role === 'admin' ? 'badge-filled' : 'badge-mock'}`}>{u.role}</span></td>
                <td><span className={`badge ${u.is_active ? 'badge-filled' : 'badge-failed'}`}>{u.is_active ? '启用' : '禁用'}</span></td>
                <td className="font-mono text-[#858585]">{u.binance_api_key ? '****' + u.binance_api_key.slice(-4) : '-'}</td>
                <td className="text-[#858585]">{u.created_at ? new Date(u.created_at).toLocaleDateString() : '-'}</td>
                <td>
                  <div className="flex gap-1">
                    <ActionBtn onClick={() => { setEditUser(u); setShowForm(true) }}>编辑</ActionBtn>
                    {u.id !== me?.id && (
                      <>
                        <ActionBtn onClick={() => handleToggleActive(u)}>{u.is_active ? '禁用' : '启用'}</ActionBtn>
                        <ActionBtn onClick={() => handleDelete(u)} danger>删除</ActionBtn>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* User form modal */}
      {showForm && (
        <UserFormModal
          user={editUser}
          onSave={() => { setShowForm(false); load() }}
          onCancel={() => setShowForm(false)}
        />
      )}
    </div>
  )
}

function UserFormModal({ user, onSave, onCancel }: { user: User | null; onSave: () => void; onCancel: () => void }) {
  const [username, setUsername] = useState(user?.username || '')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState(user?.role || 'user')
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const isEdit = !!user

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!isEdit && (!username || !password)) { setError('用户名和密码必填'); return }
    setLoading(true); setError('')
    try {
      if (isEdit) {
        const update: Record<string, unknown> = { role }
        if (password) update.password = password
        if (apiKey) update.binance_api_key = apiKey
        if (apiSecret) update.binance_api_secret = apiSecret
        await api.updateUser(user!.id, update)
      } else {
        await api.createUser({ username, password, role, binance_api_key: apiKey || undefined, binance_api_secret: apiSecret || undefined })
      }
      onSave()
    } catch (err: unknown) {
      setError((err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '操作失败')
    }
    setLoading(false)
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onCancel}>
      <form
        onClick={e => e.stopPropagation()}
        onSubmit={handleSubmit}
        className="w-96 bg-[#252526] border border-[#3e3e42] rounded-lg p-6 space-y-3"
      >
        <h3 className="text-sm font-semibold text-[#cccccc] mb-2">{isEdit ? '编辑用户' : '新建用户'}</h3>
        {error && <div className="text-xs text-red-400 bg-red-900/20 rounded px-2 py-1">{error}</div>}

        {!isEdit && (
          <Field label="用户名">
            <Input value={username} onChange={setUsername} placeholder="username" />
          </Field>
        )}
        <Field label={isEdit ? '新密码（留空不修改）' : '密码'}>
          <Input type="password" value={password} onChange={setPassword} placeholder={isEdit ? '留空不修改' : '密码'} />
        </Field>
        <Field label="角色">
          <select value={role} onChange={e => setRole(e.target.value)}
            className="w-full bg-[#1e1e1e] border border-[#3e3e42] text-sm text-[#cccccc] rounded px-2 py-1.5 outline-none">
            <option value="user">user</option>
            <option value="admin">admin</option>
          </select>
        </Field>
        <Field label="Binance API Key（可选）">
          <Input value={apiKey} onChange={setApiKey} placeholder="留空不修改" />
        </Field>
        <Field label="Binance API Secret（可选）">
          <Input type="password" value={apiSecret} onChange={setApiSecret} placeholder="留空不修改" />
        </Field>

        <div className="flex gap-2 pt-2">
          <button type="submit" disabled={loading}
            className="flex-1 py-1.5 bg-[#007acc] hover:bg-blue-600 disabled:opacity-50 text-white text-sm rounded">
            {loading ? '...' : '保存'}
          </button>
          <button type="button" onClick={onCancel}
            className="flex-1 py-1.5 bg-[#3e3e42] hover:bg-[#4e4e52] text-sm rounded">
            取消
          </button>
        </div>
      </form>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-[#858585] mb-1">{label}</label>
      {children}
    </div>
  )
}

function Input({ value, onChange, placeholder, type = 'text' }: {
  value: string; onChange: (v: string) => void; placeholder?: string; type?: string
}) {
  return (
    <input type={type} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
      className="w-full bg-[#1e1e1e] border border-[#3e3e42] text-sm text-[#cccccc] rounded px-2 py-1.5 outline-none selectable focus:border-[#007acc]" />
  )
}

function ActionBtn({ onClick, children, danger }: { onClick: () => void; children: React.ReactNode; danger?: boolean }) {
  return (
    <button onClick={onClick}
      className={`px-2 py-0.5 text-xs rounded transition-colors ${danger ? 'text-red-400 hover:bg-red-900/20' : 'text-[#858585] hover:text-[#cccccc] hover:bg-[#3e3e42]'}`}
    >
      {children}
    </button>
  )
}
