import { create } from 'zustand'

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
    try {
      const result = await window.electronAPI?.login(username, password)
      if (result?.ok) {
        set({ isAuthenticated: true, user: result.user, isLoading: false })
        return true
      } else {
        set({ isLoading: false, error: result?.error || 'Login failed' })
        return false
      }
    } catch (e: unknown) {
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

  clearError: () => set({ error: null }),
}))
