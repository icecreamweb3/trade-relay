import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import { useAuthStore } from '../store/authStore'
import { useToastStore } from '../store/toastStore'

interface User {
  id: number
  username: string
  role: string
  is_active: boolean
  binance_api_key?: string
  binance_api_secret?: string
  created_at?: string
  updated_at?: string
}

type FormMode = 'create' | 'edit'

export function AdminScreen() {
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [formMode, setFormMode] = useState<FormMode>('create')
  const { user: me } = useAuthStore()
  const showToast = useToastStore((state) => state.showToast)

  const selectedUser = useMemo(
    () => users.find((user) => user.id === selectedUserId) ?? null,
    [selectedUserId, users],
  )

  const loadUsers = useCallback(async () => {
    setLoading(true)
    try {
      const nextUsers = await api.getUsers()
      setUsers(nextUsers)
      setSelectedUserId((current) => {
        if (current != null && nextUsers.some((user: User) => user.id === current)) return current
        return nextUsers[0]?.id ?? null
      })
    } catch (err: unknown) {
      showToast('error', getApiError(err, '加载用户失败'))
    } finally {
      setLoading(false)
    }
  }, [showToast])

  useEffect(() => { loadUsers() }, [loadUsers])

  const openCreate = () => {
    setFormMode('create')
    setShowForm(true)
  }

  const openEdit = () => {
    if (!selectedUser) return
    setFormMode('edit')
    setShowForm(true)
  }

  const handleDelete = async () => {
    if (!selectedUser || selectedUser.id === me?.id) return
    if (!confirm(`确认删除用户 ${selectedUser.username}？`)) return
    try {
      await api.deleteUser(selectedUser.id)
      await loadUsers()
      showToast('success', '已删除')
    } catch (err: unknown) {
      showToast('error', getApiError(err, '删除用户失败'))
    }
  }

  const handleSetActive = async (isActive: boolean) => {
    if (!selectedUser) return
    if (!isActive && selectedUser.id === me?.id) return
    try {
      await api.updateUser(selectedUser.id, { is_active: isActive })
      await loadUsers()
      showToast('success', isActive ? '已启用' : '已停用')
    } catch (err: unknown) {
      showToast('error', getApiError(err, isActive ? '启用用户失败' : '停用用户失败'))
    }
  }

  const canEdit = !!selectedUser
  const canDelete = !!selectedUser && selectedUser.id !== me?.id
  const canActivate = !!selectedUser && !selectedUser.is_active
  const canDeactivate = !!selectedUser && selectedUser.is_active && selectedUser.id !== me?.id

  return (
    <div className="h-full min-h-0 flex flex-col bg-[#0d1219] text-[#dde4ef]">
      <div className="flex items-center gap-2 border-b border-[#2a303c] px-4 py-3 shrink-0">
        <div className="text-sm font-semibold tracking-[0.16em] uppercase text-[#8b94a5]">User Management</div>
        <div className="ml-auto flex items-center gap-2">
          <ToolbarButton onClick={openCreate} tone="primary">Add User</ToolbarButton>
          <ToolbarButton onClick={openEdit} disabled={!canEdit} tone="primary">Edit User</ToolbarButton>
          <ToolbarButton onClick={handleDelete} disabled={!canDelete} tone="danger">Delete User</ToolbarButton>
          <ToolbarButton onClick={() => handleSetActive(true)} disabled={!canActivate} tone="primary">Activate</ToolbarButton>
          <ToolbarButton onClick={() => handleSetActive(false)} disabled={!canDeactivate} tone="danger">Deactivate</ToolbarButton>
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        <table className="w-full border-collapse text-[12px]">
          <thead>
            <tr className="bg-[#171c24] text-[#8b94a5]">
              <TableHeader className="w-[70px] text-right">ID</TableHeader>
              <TableHeader className="w-[110px]">Username</TableHeader>
              <TableHeader className="w-[90px]">Role</TableHeader>
              <TableHeader className="w-[70px]">Active</TableHeader>
              <TableHeader>API Key</TableHeader>
              <TableHeader>API Secret</TableHeader>
              <TableHeader className="w-[130px] text-right">Created</TableHeader>
              <TableHeader className="w-[130px] text-right">Updated</TableHeader>
            </tr>
          </thead>
          <tbody>
            {users.length === 0 ? (
              <tr>
                <td colSpan={8} className="border border-[#2a303c] px-4 py-8 text-center text-[#7d8696]">
                  {loading ? 'Loading users...' : 'No users'}
                </td>
              </tr>
            ) : users.map((user) => {
              const isSelected = user.id === selectedUserId
              return (
                <tr
                  key={user.id}
                  onClick={() => setSelectedUserId(user.id)}
                  onDoubleClick={() => {
                    setSelectedUserId(user.id)
                    setFormMode('edit')
                    setShowForm(true)
                  }}
                  className={`cursor-pointer ${isSelected ? 'bg-[#122035]' : 'bg-[#0d1219] hover:bg-[#121923]'}`}
                >
                  <TableCell className="text-right text-[#c5ccd8]">{user.id}</TableCell>
                  <TableCell className="font-medium text-[#edf2fb]">
                    {user.username}
                    {user.id === me?.id && <span className="ml-2 text-[10px] uppercase tracking-wide text-[#3a84f7]">Self</span>}
                  </TableCell>
                  <TableCell>{formatRole(user.role)}</TableCell>
                  <TableCell>{user.is_active ? 'Yes' : 'No'}</TableCell>
                  <TableCell className="font-mono text-[#d5dce8]">{user.binance_api_key || ''}</TableCell>
                  <TableCell className="font-mono text-[#d5dce8]">{user.binance_api_secret || ''}</TableCell>
                  <TableCell className="text-right text-[#c5ccd8]">{formatDateTime(user.created_at)}</TableCell>
                  <TableCell className="text-right text-[#c5ccd8]">{formatDateTime(user.updated_at)}</TableCell>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {showForm && (
        <UserFormModal
          mode={formMode}
          user={formMode === 'edit' ? selectedUser : null}
          onCancel={() => setShowForm(false)}
          onSaved={async () => {
            setShowForm(false)
            await loadUsers()
          }}
        />
      )}
    </div>
  )
}

function UserFormModal({
  mode,
  user,
  onCancel,
  onSaved,
}: {
  mode: FormMode
  user: User | null
  onCancel: () => void
  onSaved: () => Promise<void>
}) {
  const isEdit = mode === 'edit'
  const [username, setUsername] = useState(user?.username ?? '')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [role, setRole] = useState(user?.role ?? 'user')
  const [apiKey, setApiKey] = useState(user?.binance_api_key ?? '')
  const [apiSecret, setApiSecret] = useState(user?.binance_api_secret ?? '')
  const [submitting, setSubmitting] = useState(false)
  const showToast = useToastStore((state) => state.showToast)

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()

    const trimmedUsername = username.trim()
    const trimmedPassword = password.trim()

    if (!trimmedUsername) {
      showToast('error', '用户名必填')
      return
    }

    if (!isEdit && !trimmedPassword) {
      showToast('error', '用户名和密码必填')
      return
    }

    if (trimmedPassword || confirmPassword.trim()) {
      if (trimmedPassword !== confirmPassword.trim()) {
        showToast('error', '两次输入的密码不一致')
        return
      }
    }

    setSubmitting(true)
    try {
      if (isEdit && user) {
        const payload: Record<string, unknown> = {
          username: trimmedUsername,
          role,
          binance_api_key: apiKey.trim(),
          binance_api_secret: apiSecret.trim(),
        }
        if (trimmedPassword) payload.password = trimmedPassword
        await api.updateUser(user.id, payload)
      } else {
        await api.createUser({
          username: trimmedUsername,
          password: trimmedPassword,
          role,
          binance_api_key: apiKey.trim() || undefined,
          binance_api_secret: apiSecret.trim() || undefined,
        })
      }
      await onSaved()
      showToast('success', isEdit ? '已更新' : '已创建')
    } catch (err: unknown) {
      showToast('error', getApiError(err, '保存用户失败'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6" onClick={onCancel}>
      <form
        onClick={(event) => event.stopPropagation()}
        onSubmit={handleSubmit}
        className="w-full max-w-[520px] rounded-xl border border-[#2b3240] bg-[#121923] shadow-[0_28px_80px_rgba(0,0,0,0.55)]"
      >
        <div className="flex items-start justify-between border-b border-[#2b3240] px-5 py-4">
          <div>
            <div className="text-base font-semibold text-white">{isEdit ? 'Edit User' : 'Add User'}</div>
            <div className="mt-1 text-xs text-[#8b94a5]">Manage account role and Binance credentials.</div>
          </div>
          <button type="button" onClick={onCancel} className="text-[#8b94a5] hover:text-white text-xl leading-none">×</button>
        </div>

        <div className="grid grid-cols-2 gap-4 px-5 py-5">
          <div className="col-span-2">
            <Field label="Username">
              <Input value={username} onChange={setUsername} placeholder="username" />
            </Field>
          </div>

          <div className={isEdit ? 'col-span-2' : ''}>
            <Field label={isEdit ? 'New Password' : 'Password'}>
              <Input
                type="password"
                value={password}
                onChange={setPassword}
                placeholder={isEdit ? '留空表示不修改' : 'password'}
              />
            </Field>
          </div>

          <div className="col-span-2">
            <Field label={isEdit ? 'Confirm New Password' : 'Confirm Password'}>
              <Input
                type="password"
                value={confirmPassword}
                onChange={setConfirmPassword}
                placeholder={isEdit ? '再次输入新密码' : 'confirm password'}
              />
            </Field>
          </div>

          <div className="col-span-2">
            <Field label="Role">
              <select
                value={role}
                onChange={(event) => setRole(event.target.value)}
                className="w-full rounded-md border border-[#2d3542] bg-[#0d131a] px-3 py-2 text-sm text-[#e6ebf2] outline-none focus:border-[#3182f6]"
              >
                <option value="user">User</option>
                <option value="admin">Admin</option>
              </select>
            </Field>
          </div>

          <div className="col-span-2">
            <Field label="API Key">
              <Input value={apiKey} onChange={setApiKey} placeholder="Binance API key" />
            </Field>
          </div>

          <div className="col-span-2">
            <Field label="API Secret">
              <Input type="password" value={apiSecret} onChange={setApiSecret} placeholder="Binance API secret" />
            </Field>
          </div>
        </div>

        <div className="flex items-center justify-end gap-3 border-t border-[#2b3240] px-5 py-4">
          <button type="button" onClick={onCancel} className="rounded-md border border-[#394152] px-4 py-2 text-sm text-[#d7dde7] hover:bg-[#18202b]">
            Cancel
          </button>
          <button type="submit" disabled={submitting} className="rounded-md bg-[#2f7cf6] px-4 py-2 text-sm font-medium text-white hover:bg-[#4b90fb] disabled:opacity-60">
            {submitting ? 'Saving...' : isEdit ? 'Save Changes' : 'Create User'}
          </button>
        </div>
      </form>
    </div>
  )
}

function ToolbarButton({
  children,
  onClick,
  disabled,
  tone,
}: {
  children: React.ReactNode
  onClick: () => void
  disabled?: boolean
  tone: 'primary' | 'danger'
}) {
  const toneClass = tone === 'danger'
    ? 'bg-[#d83d35] hover:bg-[#f04e45]'
    : 'bg-[#2f7cf6] hover:bg-[#4b90fb]'

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`rounded-md px-4 py-2 text-xs font-medium text-white transition-colors ${toneClass} disabled:cursor-not-allowed disabled:bg-[#334052] disabled:text-[#7b8596]`}
    >
      {children}
    </button>
  )
}

function TableHeader({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <th className={`border border-[#2a303c] px-4 py-3 text-[11px] font-semibold ${className}`}>
      {children}
    </th>
  )
}

function TableCell({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <td className={`border border-[#252b36] px-4 py-3 align-middle ${className}`}>
      {children}
    </td>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="mb-1.5 text-xs font-medium uppercase tracking-[0.08em] text-[#8b94a5]">{label}</div>
      {children}
    </label>
  )
}

function Input({
  value,
  onChange,
  placeholder,
  type = 'text',
}: {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  type?: string
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      className="selectable w-full rounded-md border border-[#2d3542] bg-[#0d131a] px-3 py-2 text-sm text-[#e6ebf2] outline-none focus:border-[#3182f6]"
    />
  )
}

function formatRole(role: string) {
  return role.toLowerCase() === 'admin' ? 'Admin' : 'User'
}

function formatDateTime(value?: string) {
  if (!value) return '-'
  const normalized = value.includes('T') ? value : value.replace(' ', 'T')
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return value
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  return `${year}-${month}-${day} ${hour}:${minute}`
}

function getApiError(error: unknown, fallback: string) {
  return (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || fallback
}
