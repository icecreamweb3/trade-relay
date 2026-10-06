import { create } from 'zustand'

export interface AutoTakeProfitParameters {
  minimumProfitPoints: number
}

export const DEFAULT_AUTO_TAKE_PROFIT_PARAMETERS: AutoTakeProfitParameters = {
  minimumProfitPoints: 500,
}

export const AUTO_TAKE_PROFIT_PARAMETER_LIMITS = {
  minimumProfitPoints: { min: 0, max: 1_000_000 },
} as const

function storageKey(username: string): string {
  return `trade-relay:auto-take-profit-parameters:${encodeURIComponent(username)}`
}

export function normalizeAutoTakeProfitParameters(
  value: Partial<AutoTakeProfitParameters>,
): AutoTakeProfitParameters {
  const minimumProfitPoints = value.minimumProfitPoints
  const limits = AUTO_TAKE_PROFIT_PARAMETER_LIMITS.minimumProfitPoints
  return {
    minimumProfitPoints: typeof minimumProfitPoints === 'number'
      && Number.isFinite(minimumProfitPoints)
      && minimumProfitPoints >= limits.min
      && minimumProfitPoints <= limits.max
      ? minimumProfitPoints
      : DEFAULT_AUTO_TAKE_PROFIT_PARAMETERS.minimumProfitPoints,
  }
}

function readStoredParameters(username: string): AutoTakeProfitParameters {
  if (!username) return DEFAULT_AUTO_TAKE_PROFIT_PARAMETERS
  try {
    const raw = window.localStorage.getItem(storageKey(username))
    if (!raw) return DEFAULT_AUTO_TAKE_PROFIT_PARAMETERS
    return normalizeAutoTakeProfitParameters(JSON.parse(raw) as Partial<AutoTakeProfitParameters>)
  } catch {
    return DEFAULT_AUTO_TAKE_PROFIT_PARAMETERS
  }
}

function writeStoredParameters(username: string, parameters: AutoTakeProfitParameters) {
  if (!username) return
  try {
    window.localStorage.setItem(storageKey(username), JSON.stringify(parameters))
  } catch {
    // Keep the in-memory setting if storage is unavailable.
  }
}

interface AutoTakeProfitSettingsStore {
  byUsername: Record<string, AutoTakeProfitParameters>
  loadParameters: (username: string) => void
  setParameters: (username: string, parameters: AutoTakeProfitParameters) => void
}

export const useAutoTakeProfitSettingsStore = create<AutoTakeProfitSettingsStore>((set) => ({
  byUsername: {},
  loadParameters: (username) => {
    if (!username) return
    set((state) => ({
      byUsername: {
        ...state.byUsername,
        [username]: readStoredParameters(username),
      },
    }))
  },
  setParameters: (username, value) => {
    if (!username) return
    const parameters = normalizeAutoTakeProfitParameters(value)
    writeStoredParameters(username, parameters)
    set((state) => ({
      byUsername: {
        ...state.byUsername,
        [username]: parameters,
      },
    }))
  },
}))
