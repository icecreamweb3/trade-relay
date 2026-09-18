import { create } from 'zustand'

export type RiskWarningKind = 'LOSS_ADD' | 'CONSECUTIVE_LOSSES' | 'OVERTRADING' | 'ADD_POSITION_LIMIT'

export interface RiskWarningItem {
  id: number
  kind: RiskWarningKind
  signature: string
  username: string
  symbol?: string
  lossAmount?: number
  consecutiveLosses?: number
  tradeCount?: number
  tradeWindowMinutes?: number
  addPositionCount?: number
  addPositionLimit?: number
  triggeredAt: number
  cooldownUntil: number
}

interface RiskWarningStore {
  warning: RiskWarningItem | null
  showWarning: (warning: Omit<RiskWarningItem, 'id'>) => void
  dismissWarning: () => void
  expireWarning: () => void
}

const ACTIVE_WARNING_STORAGE_KEY = 'trade-relay:active-risk-warning'
const HANDLED_SIGNATURES_STORAGE_KEY = 'trade-relay:handled-risk-warning-signatures'
const MAX_HANDLED_SIGNATURES = 50

function readActiveWarning(): RiskWarningItem | null {
  try {
    const raw = window.localStorage.getItem(ACTIVE_WARNING_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as RiskWarningItem
    if (!parsed.signature || !Number.isFinite(parsed.cooldownUntil) || parsed.cooldownUntil <= Date.now()) {
      window.localStorage.removeItem(ACTIVE_WARNING_STORAGE_KEY)
      return null
    }
    return parsed
  } catch {
    return null
  }
}

function readHandledSignatures(): string[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(HANDLED_SIGNATURES_STORAGE_KEY) || '[]')
    return Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === 'string') : []
  } catch {
    return []
  }
}

function persistActiveWarning(warning: RiskWarningItem | null) {
  try {
    if (warning) window.localStorage.setItem(ACTIVE_WARNING_STORAGE_KEY, JSON.stringify(warning))
    else window.localStorage.removeItem(ACTIVE_WARNING_STORAGE_KEY)
  } catch {
    // Keep the warning in memory when local storage is unavailable.
  }
}

function markSignatureHandled(signature: string) {
  try {
    const next = [signature, ...readHandledSignatures().filter((item) => item !== signature)]
      .slice(0, MAX_HANDLED_SIGNATURES)
    window.localStorage.setItem(HANDLED_SIGNATURES_STORAGE_KEY, JSON.stringify(next))
  } catch {
    // A storage failure may allow the warning to reappear after an app restart.
  }
}

let warningId = Date.now()

export const useRiskWarningStore = create<RiskWarningStore>((set, get) => ({
  warning: readActiveWarning(),
  showWarning: (next) => {
    if (next.cooldownUntil <= Date.now()) return
    const current = get().warning
    if (current?.signature === next.signature || readHandledSignatures().includes(next.signature)) return
    const warning = { ...next, id: ++warningId }
    persistActiveWarning(warning)
    set({ warning })
  },
  dismissWarning: () => {
    const current = get().warning
    if (current) markSignatureHandled(current.signature)
    persistActiveWarning(null)
    set({ warning: null })
  },
  expireWarning: () => {
    const current = get().warning
    if (!current || current.cooldownUntil > Date.now()) return
    markSignatureHandled(current.signature)
    persistActiveWarning(null)
    set({ warning: null })
  },
}))
