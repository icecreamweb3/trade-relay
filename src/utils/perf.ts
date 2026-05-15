/**
 * Lightweight login performance logger.
 * Writes timing info to the Electron log file (and DevTools console).
 */

let _start = 0
let _prev = 0

function log(msg: string) {
  console.log(msg)
  try { window.electronAPI?.logToMain?.('info', msg) } catch { /* ignore */ }
}

export const perf = {
  start(label = 'login-submit') {
    _start = performance.now()
    _prev = _start
    log(`[perf] ▶ START — ${label}`)
  },

  mark(label: string) {
    if (_start === 0) return
    const now = performance.now()
    const elapsed = Math.round(now - _start)
    const delta = Math.round(now - _prev)
    _prev = now
    log(`[perf]   ${String(elapsed).padStart(6)}ms  (+${String(delta).padStart(5)}ms)  ${label}`)
  },

  finish(label = 'all-data-loaded') {
    if (_start === 0) return
    this.mark(label)
    const total = Math.round(performance.now() - _start)
    log(`[perf] ■ TOTAL: ${total}ms`)
    _start = 0
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

