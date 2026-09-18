import { useCallback, useEffect, useRef } from 'react'
import { api, ApiPositionRecord } from '../api/client'
import { useAuthStore } from '../store/authStore'
import {
  CONSECUTIVE_LOSS_THRESHOLD,
  RISK_COOLDOWN_MS,
  useRiskWarningStore,
} from '../store/riskWarningStore'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'

function getPositionResult(position: ApiPositionRecord) {
  return position.net_pnl ?? position.realized_pnl ?? 0
}

function getPositionClosedAt(position: ApiPositionRecord) {
  const timestamp = Date.parse(position.close_time || '')
  return Number.isFinite(timestamp) ? timestamp : null
}

export function RiskWarningMonitor() {
  const enabled = useUiPreferencesStore((state) => state.riskWarningsEnabled)
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated)
  const user = useAuthStore((state) => state.user)
  const showWarning = useRiskWarningStore((state) => state.showWarning)
  const inFlight = useRef(false)

  const checkConsecutiveLosses = useCallback(async () => {
    if (!enabled || !isAuthenticated || !user || user.role === 'admin' || inFlight.current) return
    inFlight.current = true
    try {
      const positions = await api.getPositionRecords({ limit: 10 })
      let consecutiveLosses = 0
      for (const position of positions) {
        if (getPositionResult(position) < 0) consecutiveLosses += 1
        else break
      }
      if (consecutiveLosses < CONSECUTIVE_LOSS_THRESHOLD || positions.length === 0) return

      const latest = positions[0]
      const closedAt = getPositionClosedAt(latest)
      if (closedAt == null) return
      const cooldownUntil = closedAt + RISK_COOLDOWN_MS
      if (cooldownUntil <= Date.now()) return

      showWarning({
        kind: 'CONSECUTIVE_LOSSES',
        signature: `consecutive-losses:${user.username}:${latest.id}:${consecutiveLosses}`,
        username: user.username,
        consecutiveLosses,
        triggeredAt: closedAt,
        cooldownUntil,
      })
    } catch {
      // Risk reminders should never interrupt the trading screen when history is unavailable.
    } finally {
      inFlight.current = false
    }
  }, [enabled, isAuthenticated, showWarning, user])

  useEffect(() => {
    void checkConsecutiveLosses()
    if (!enabled || !isAuthenticated || !user || user.role === 'admin') return
    const timer = window.setInterval(() => void checkConsecutiveLosses(), 10_000)
    return () => window.clearInterval(timer)
  }, [checkConsecutiveLosses, enabled, isAuthenticated, user])

  return null
}
