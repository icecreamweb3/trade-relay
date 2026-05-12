import { create } from 'zustand'

export type ToastKind = 'success' | 'error' | 'info'

export interface ToastItem {
  id: number
  kind: ToastKind
  msg: string
  duration: number
}

interface ToastStore {
  toast: ToastItem | null
  showToast: (kind: ToastKind, msg: string, options?: { duration?: number }) => void
  dismissToast: () => void
}

let toastId = 0

export const useToastStore = create<ToastStore>((set) => ({
  toast: null,
  showToast: (kind, msg, options) => set({
    toast: {
      id: ++toastId,
      kind,
      msg,
      duration: options?.duration ?? 5000,
    },
  }),
  dismissToast: () => set({ toast: null }),
}))