import { create } from 'zustand'

export interface RiskWarningParameters {
  tradeWindowMinutes: number
  tradeLimit: number
  cooldownMinutes: number
  consecutiveLossLimit: number
  addPositionLimit: number
}

export const DEFAULT_RISK_WARNING_PARAMETERS: RiskWarningParameters = {
  tradeWindowMinutes: 60,
  tradeLimit: 3,
  cooldownMinutes: 30,
  consecutiveLossLimit: 2,
  addPositionLimit: 2,
}

export const RISK_WARNING_PARAMETER_LIMITS = {
  tradeWindowMinutes: { min: 5, max: 1440 },
  tradeLimit: { min: 1, max: 100 },
  cooldownMinutes: { min: 1, max: 1440 },
  consecutiveLossLimit: { min: 1, max: 20 },
  addPositionLimit: { min: 0, max: 20 },
} as const

function storageKey(username: string) {
  return `trade-relay:risk-warning-parameters:${encodeURIComponent(username)}`
}

function normalizeInteger(value: unknown, fallback: number, min: number, max: number) {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed >= min && parsed <= max ? parsed : fallback
}

export function normalizeRiskWarningParameters(value: Partial<RiskWarningParameters>): RiskWarningParameters {
  return {
    tradeWindowMinutes: normalizeInteger(
      value.tradeWindowMinutes,
      DEFAULT_RISK_WARNING_PARAMETERS.tradeWindowMinutes,
      RISK_WARNING_PARAMETER_LIMITS.tradeWindowMinutes.min,
      RISK_WARNING_PARAMETER_LIMITS.tradeWindowMinutes.max,
    ),
    tradeLimit: normalizeInteger(
      value.tradeLimit,
      DEFAULT_RISK_WARNING_PARAMETERS.tradeLimit,
      RISK_WARNING_PARAMETER_LIMITS.tradeLimit.min,
      RISK_WARNING_PARAMETER_LIMITS.tradeLimit.max,
    ),
    cooldownMinutes: normalizeInteger(
      value.cooldownMinutes,
      DEFAULT_RISK_WARNING_PARAMETERS.cooldownMinutes,
      RISK_WARNING_PARAMETER_LIMITS.cooldownMinutes.min,
      RISK_WARNING_PARAMETER_LIMITS.cooldownMinutes.max,
    ),
    consecutiveLossLimit: normalizeInteger(
      value.consecutiveLossLimit,
      DEFAULT_RISK_WARNING_PARAMETERS.consecutiveLossLimit,
      RISK_WARNING_PARAMETER_LIMITS.consecutiveLossLimit.min,
      RISK_WARNING_PARAMETER_LIMITS.consecutiveLossLimit.max,
    ),
    addPositionLimit: normalizeInteger(
      value.addPositionLimit,
      DEFAULT_RISK_WARNING_PARAMETERS.addPositionLimit,
      RISK_WARNING_PARAMETER_LIMITS.addPositionLimit.min,
      RISK_WARNING_PARAMETER_LIMITS.addPositionLimit.max,
    ),
  }
}

function readStoredParameters(username: string) {
  if (!username) return DEFAULT_RISK_WARNING_PARAMETERS
  try {
    const raw = window.localStorage.getItem(storageKey(username))
    return raw
      ? normalizeRiskWarningParameters(JSON.parse(raw) as Partial<RiskWarningParameters>)
      : DEFAULT_RISK_WARNING_PARAMETERS
  } catch {
    return DEFAULT_RISK_WARNING_PARAMETERS
  }
}

function writeStoredParameters(username: string, parameters: RiskWarningParameters) {
  try {
    window.localStorage.setItem(storageKey(username), JSON.stringify(parameters))
  } catch {
    // Keep the in-memory setting if storage is unavailable.
  }
}

interface RiskWarningSettingsStore {
  byUsername: Record<string, RiskWarningParameters>
  loadParameters: (username: string) => void
  setParameters: (username: string, parameters: RiskWarningParameters) => void
}

export const useRiskWarningSettingsStore = create<RiskWarningSettingsStore>((set) => ({
  byUsername: {},
  loadParameters: (username) => {
    if (!username) return
    set((state) => ({
      byUsername: { ...state.byUsername, [username]: readStoredParameters(username) },
    }))
  },
  setParameters: (username, value) => {
    if (!username) return
    const parameters = normalizeRiskWarningParameters(value)
    writeStoredParameters(username, parameters)
    set((state) => ({
      byUsername: { ...state.byUsername, [username]: parameters },
    }))
  },
}))
