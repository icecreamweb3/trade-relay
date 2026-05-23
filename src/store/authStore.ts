import { create } from 'zustand'
import { perf, perfExpectDone } from '../utils/perf'

export interface UserInfo {
  id: number
  username: string
  role: 'admin' | 'user'
  is_active: boolean
}

interface AuthStore {
  isAuthenticated: boolean
  user: UserInfo | null
  token: string | null
  isLoading: boolean
  error: string | null

  login: (username: string, password: string) => Promise<boolean>
  logout: () => Promise<void>
  checkAuth: () => Promise<void>
  expireSession: () => void
  clearError: () => void
}

export const useAuthStore = create<AuthStore>((set) => ({
  isAuthenticated: false,
  user: null,
  token: null,
  isLoading: false,
  error: null,

  login: async (username, password) => {
    set({ isLoading: true, error: null })
    perf.mark('authStore.login — start')
    try {
      perf.mark('ipc auth-login — sent')
      const result = await window.electronAPI?.login(username, password)
      perf.mark('ipc auth-login — response received')
      if (result?.ok) {
        perfExpectDone(2)  // waiting for: account-summary + positions
        set({ isAuthenticated: true, user: result.user, isLoading: false })
        perf.mark('isAuthenticated — set true (render starting)')
        return true
      } else {
        perf.reset()
        set({ isLoading: false, error: result?.error || 'Login failed' })
        return false
      }
    } catch (e: unknown) {
      perf.reset()
      set({ isLoading: false, error: String(e) })
      return false
    }
  },

  logout: async () => {
    await window.electronAPI?.logout()
    set({ isAuthenticated: false, user: null, token: null })
  },

  checkAuth: async () => {
    set({ isLoading: true })
    try {
      const status = await window.electronAPI?.getAuthStatus()
      if (status?.authenticated && status.user) {
        set({ isAuthenticated: true, user: status.user, isLoading: false })
      } else {
        set({ isAuthenticated: false, user: null, isLoading: false })
      }
    } catch {
      set({ isAuthenticated: false, user: null, isLoading: false })
    }
  },

  expireSession: () => {
    set({ isAuthenticated: false, user: null, token: null, isLoading: false })
  },

  clearError: () => set({ error: null }),
}))
