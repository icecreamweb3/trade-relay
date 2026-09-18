import { create } from 'zustand'

export interface AutoBreakevenParameters {
  triggerRMultiple: number
  minimumProfitPercent: number
}

export const DEFAULT_AUTO_BREAKEVEN_PARAMETERS: AutoBreakevenParameters = {
  triggerRMultiple: 1,
  minimumProfitPercent: 0.4,
}

export const AUTO_BREAKEVEN_PARAMETER_LIMITS = {
  triggerRMultiple: { min: 1, max: 20 },
  minimumProfitPercent: { min: 0.1, max: 10 },
} as const

function storageKey(username: string): string {
  return `trade-relay:auto-breakeven-parameters:${encodeURIComponent(username)}`
}

function isWithin(value: number, min: number, max: number): boolean {
  return Number.isFinite(value) && value >= min && value <= max
}

export function normalizeAutoBreakevenParameters(value: Partial<AutoBreakevenParameters>): AutoBreakevenParameters {
  const triggerRMultiple = Number(value.triggerRMultiple)
  const minimumProfitPercent = Number(value.minimumProfitPercent)
  return {
    triggerRMultiple: isWithin(
      triggerRMultiple,
      AUTO_BREAKEVEN_PARAMETER_LIMITS.triggerRMultiple.min,
      AUTO_BREAKEVEN_PARAMETER_LIMITS.triggerRMultiple.max,
    ) ? triggerRMultiple : DEFAULT_AUTO_BREAKEVEN_PARAMETERS.triggerRMultiple,
    minimumProfitPercent: isWithin(
      minimumProfitPercent,
      AUTO_BREAKEVEN_PARAMETER_LIMITS.minimumProfitPercent.min,
      AUTO_BREAKEVEN_PARAMETER_LIMITS.minimumProfitPercent.max,
    ) ? minimumProfitPercent : DEFAULT_AUTO_BREAKEVEN_PARAMETERS.minimumProfitPercent,
  }
}

function readStoredParameters(username: string): AutoBreakevenParameters {
  if (!username) return DEFAULT_AUTO_BREAKEVEN_PARAMETERS
  try {
    const raw = window.localStorage.getItem(storageKey(username))
    if (!raw) return DEFAULT_AUTO_BREAKEVEN_PARAMETERS
    return normalizeAutoBreakevenParameters(JSON.parse(raw) as Partial<AutoBreakevenParameters>)
  } catch {
    return DEFAULT_AUTO_BREAKEVEN_PARAMETERS
  }
}

function writeStoredParameters(username: string, parameters: AutoBreakevenParameters) {
  if (!username) return
  try {
    window.localStorage.setItem(storageKey(username), JSON.stringify(parameters))
  } catch {
    // Keep the in-memory setting if storage is unavailable.
  }
}

interface AutoBreakevenSettingsStore {
  byUsername: Record<string, AutoBreakevenParameters>
  loadParameters: (username: string) => void
  setParameters: (username: string, parameters: AutoBreakevenParameters) => void
}

export const useAutoBreakevenSettingsStore = create<AutoBreakevenSettingsStore>((set) => ({
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
    const parameters = normalizeAutoBreakevenParameters(value)
    writeStoredParameters(username, parameters)
    set((state) => ({
      byUsername: {
        ...state.byUsername,
        [username]: parameters,
      },
    }))
  },
}))
