/**
 * Lightweight login performance logger.
 * Writes timing info to the Electron log file (and DevTools console).
 */

let _start = 0
let _prev = 0
const _spans = new Map<string, { label: string; startedAt: number }>()

function log(msg: string) {
  console.log(msg)
  try { window.electronAPI?.logToMain?.('info', msg) } catch { /* ignore */ }
}

function fmtElapsed(now: number) {
  return `${Math.round(now - _start)}ms`
}

export const perf = {
  start(label = 'login-submit') {
    _start = performance.now()
    _prev = _start
    log(`[FRONTEND_PERF] START | ${label}`)
  },

  mark(label: string) {
    if (_start === 0) return
    const now = performance.now()
    const elapsed = Math.round(now - _start)
    const delta = Math.round(now - _prev)
    _prev = now
    log(`[FRONTEND_PERF] MARK | t=${elapsed}ms prev=+${delta}ms | ${label}`)
  },

  spanStart(label: string) {
    if (_start === 0) return null
    const token = `${label}#${performance.now()}#${Math.random().toString(36).slice(2, 8)}`
    const now = performance.now()
    _spans.set(token, { label, startedAt: now })
    log(`[FRONTEND_PERF] SPAN_START | t=${fmtElapsed(now)} | ${label}`)
    return token
  },

  spanEnd(token: string | null, status: 'ok' | 'error' = 'ok') {
    if (!token || _start === 0) return
    const span = _spans.get(token)
    if (!span) return
    _spans.delete(token)
    const now = performance.now()
    const duration = Math.round(now - span.startedAt)
    log(`[FRONTEND_PERF] SPAN_END | t=${fmtElapsed(now)} | ${span.label} | ${status} | duration=${duration}ms`)
  },

  finish(label = 'all-data-loaded') {
    if (_start === 0) return
    this.mark(label)
    const total = Math.round(performance.now() - _start)
    log(`[FRONTEND_PERF] TOTAL | ${total}ms`)
    _start = 0
    _prev = 0
    _spans.clear()
  },

  isActive() { return _start !== 0 },

  reset() { _start = 0; _prev = 0 },
}

// Coordinate finish() across multiple async loaders
let _expected = 0
let _done = 0

export function perfExpectDone(n: number) { _expected = n; _done = 0 }

export function perfSignalDone(label: string) {
  if (!perf.isActive()) return
  perf.mark(label)
  _done++
  if (_expected > 0 && _done >= _expected) perf.finish()
}

