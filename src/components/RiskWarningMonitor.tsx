import { useCallback, useEffect, useRef } from 'react'
import { api, ApiPositionRecord } from '../api/client'
import { useAuthStore } from '../store/authStore'
import {
  useRiskWarningStore,
} from '../store/riskWarningStore'
import {
  DEFAULT_RISK_WARNING_PARAMETERS,
  useRiskWarningSettingsStore,
} from '../store/riskWarningSettingsStore'
import { useUiPreferencesStore } from '../store/uiPreferencesStore'

function getPositionResult(position: ApiPositionRecord) {
  return position.net_pnl ?? position.realized_pnl ?? 0
}

function getPositionClosedAt(position: ApiPositionRecord) {
  const timestamp = Date.parse(position.close_time || '')
  return Number.isFinite(timestamp) ? timestamp : null
}

function getOrderFilledAt(order: { filled_at?: string | null; updated_at?: string | null; created_at?: string }) {
  const timestamp = Date.parse(order.filled_at || order.updated_at || order.created_at || '')
  return Number.isFinite(timestamp) ? timestamp : null
}

export function RiskWarningMonitor() {
  const enabled = useUiPreferencesStore((state) => state.riskWarningsEnabled)
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated)
  const user = useAuthStore((state) => state.user)
  const showWarning = useRiskWarningStore((state) => state.showWarning)
  const storedParameters = useRiskWarningSettingsStore((state) => (
    user?.username
      ? state.byUsername[user.username]
      : undefined
  ))
  const parameters = storedParameters ?? DEFAULT_RISK_WARNING_PARAMETERS
  const loadParameters = useRiskWarningSettingsStore((state) => state.loadParameters)
  const inFlight = useRef(false)

  useEffect(() => {
    if (user?.username) loadParameters(user.username)
  }, [loadParameters, user?.username])

  const checkRiskWarnings = useCallback(async () => {
    if (!enabled || !isAuthenticated || !user || !storedParameters || user.role === 'admin' || inFlight.current) return
    inFlight.current = true
    try {
      const now = Date.now()
      const cooldownMs = parameters.cooldownMinutes * 60_000
      const windowStartedAt = now - parameters.tradeWindowMinutes * 60_000
      const [positionRecordsResult, ordersResult, openPositionsResult] = await Promise.allSettled([
        api.getPositionRecords({ limit: 10 }),
        api.getOrders({
          limit: 200,
          username: user.username,
          trade_direction: 'OPEN',
        }),
        api.getPositions(),
      ])

      if (positionRecordsResult.status === 'fulfilled') {
        const positions = positionRecordsResult.value
        let consecutiveLosses = 0
        for (const position of positions) {
          if (getPositionResult(position) < 0) consecutiveLosses += 1
          else break
        }
        if (consecutiveLosses >= parameters.consecutiveLossLimit && positions.length > 0) {
          const latest = positions[0]
          const closedAt = getPositionClosedAt(latest)
          const cooldownUntil = closedAt == null ? 0 : closedAt + cooldownMs
          if (cooldownUntil > now) {
            showWarning({
              kind: 'CONSECUTIVE_LOSSES',
              signature: `consecutive-losses:${user.username}:${latest.id}:${consecutiveLosses}`,
              username: user.username,
              consecutiveLosses,
              triggeredAt: closedAt as number,
              cooldownUntil,
            })
          }
        }
      }

      if (ordersResult.status === 'fulfilled') {
        const recentOpenFills = ordersResult.value
          .filter((order) => order.username === user.username && Number(order.filled_qty ?? 0) > 0)
          .map((order) => ({ order, filledAt: getOrderFilledAt(order) }))
          .filter((entry): entry is { order: typeof entry.order; filledAt: number } => (
            entry.filledAt != null && entry.filledAt >= windowStartedAt
          ))
          .sort((left, right) => right.filledAt - left.filledAt)

        if (recentOpenFills.length > parameters.tradeLimit) {
          const latest = recentOpenFills[0]
          const cooldownUntil = latest.filledAt + cooldownMs
          if (cooldownUntil > now) {
            showWarning({
              kind: 'OVERTRADING',
              signature: `overtrading:${user.username}:${latest.order.id}:${recentOpenFills.length}`,
              username: user.username,
              tradeCount: recentOpenFills.length,
              tradeWindowMinutes: parameters.tradeWindowMinutes,
              triggeredAt: latest.filledAt,
              cooldownUntil,
            })
          }
        }

        if (openPositionsResult.status === 'fulfilled') {
          for (const position of openPositionsResult.value) {
            const positionOpenedAt = Date.parse(position.opened_at || '')
            if (!Number.isFinite(positionOpenedAt)) continue
            const openingSide = position.side === 'LONG' ? 'BUY' : position.side === 'SHORT' ? 'SELL' : null
            if (!openingSide) continue
            const positionEntries = ordersResult.value
              .filter((order) => {
                const filledAt = getOrderFilledAt(order)
                return order.username === user.username
                  && order.symbol.toUpperCase() === position.symbol.toUpperCase()
                  && order.side === openingSide
                  && Number(order.filled_qty ?? 0) > 0
                  && filledAt != null
                  && filledAt >= positionOpenedAt
              })
              .map((order) => ({ order, filledAt: getOrderFilledAt(order) as number }))
              .sort((left, right) => right.filledAt - left.filledAt)
            const addPositionCount = Math.max(0, positionEntries.length - 1)
            if (addPositionCount <= parameters.addPositionLimit || positionEntries.length === 0) continue
            const latest = positionEntries[0]
            const cooldownUntil = latest.filledAt + cooldownMs
            if (cooldownUntil <= now) continue
            showWarning({
              kind: 'ADD_POSITION_LIMIT',
              signature: `add-position-limit:${user.username}:${position.id}:${latest.order.id}:${addPositionCount}`,
              username: user.username,
              symbol: position.symbol,
              addPositionCount,
              addPositionLimit: parameters.addPositionLimit,
              triggeredAt: latest.filledAt,
              cooldownUntil,
            })
          }
        }
      }
    } catch {
      // Risk reminders should never interrupt the trading screen when history is unavailable.
    } finally {
      inFlight.current = false
    }
  }, [enabled, isAuthenticated, parameters, showWarning, storedParameters, user])

  useEffect(() => {
    void checkRiskWarnings()
    if (!enabled || !isAuthenticated || !user || user.role === 'admin') return
    const timer = window.setInterval(() => void checkRiskWarnings(), 10_000)
    return () => window.clearInterval(timer)
  }, [checkRiskWarnings, enabled, isAuthenticated, user])

  return null
}
