/**
 * Trade Relay — Electron Main Process Logger
 *
 * 每次启动以时间戳命名日志文件，单文件最大 100 MB 后自动滚动。
 * 格式: 时间 | 级别 | 文件名:行号 | 函数名 | 消息
 *
 * 使用方法:
 *   const { logger } = require('./logger')
 *   logger.info('Hello', { key: 'value' })
 */

const fs   = require('fs')
const os   = require('os')
const path = require('path')

// ── 配置 ──────────────────────────────────────────────────────────────────────
const MAX_BYTES     = 100 * 1024 * 1024   // 100 MB
const BACKUP_COUNT  = 9
const APP_NAME      = 'Trade Relay'

const LEVELS = { DEBUG: 0, INFO: 1, WARN: 2, ERROR: 3 }

// ── 内部状态 ──────────────────────────────────────────────────────────────────
let _fd        = null   // 当前日志文件描述符
let _filePath  = null   // 当前日志文件路径
let _written   = 0      // 已写入字节数
let _logDir    = null   // 当前日志目录
let _bootstrapFilePath = null

function _fallbackLogDir() {
  const explicitDir = String(process.env.TRADE_RELAY_LOG_DIR || '').trim()
  if (explicitDir) return explicitDir

  if (process.platform === 'win32') {
    const base = process.env.LOCALAPPDATA || process.env.APPDATA
    if (base) return path.join(base, APP_NAME, 'logs')
  }

  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Logs', APP_NAME)
  }

  const xdgStateHome = process.env.XDG_STATE_HOME
  if (xdgStateHome) return path.join(xdgStateHome, 'trade-relay', 'logs')
  return path.join(os.homedir(), '.local', 'state', 'trade-relay', 'logs')
}

function _logDirCandidates() {
  const candidates = []
  const explicitDir = String(process.env.TRADE_RELAY_LOG_DIR || '').trim()
  if (explicitDir) candidates.push(explicitDir)

  const packagedDir = __dirname.includes('app.asar')
  if (!packagedDir) {
    candidates.push(path.join(__dirname, '../logs'))
  } else {
    const exeDir = path.dirname(process.execPath)
    if (exeDir) candidates.push(path.join(exeDir, 'logs'))
    const cwd = process.cwd && process.cwd()
    if (cwd) candidates.push(path.join(cwd, 'logs'))
  }

  candidates.push(_fallbackLogDir())

  const seen = new Set()
  return candidates
    .filter(Boolean)
    .map((candidate) => path.resolve(candidate))
    .filter((candidate) => {
      if (seen.has(candidate)) return false
      seen.add(candidate)
      return true
    })
}

function _appendBootstrapLine(logDir, line) {
  try {
    if (!fs.existsSync(logDir)) fs.mkdirSync(logDir, { recursive: true })
    const bootstrapFile = path.join(logDir, 'electron_bootstrap.log')
    fs.appendFileSync(bootstrapFile, `${_timestamp()} | ${line}\n`, 'utf8')
    _bootstrapFilePath = bootstrapFile
    _logDir = logDir
    return true
  } catch {
    return false
  }
}

function _bootstrapLogDir() {
  if (_logDir && fs.existsSync(_logDir)) return
  for (const logDir of _logDirCandidates()) {
    if (_appendBootstrapLine(logDir, 'bootstrap:init')) {
      return
    }
  }
}

/** 确保日志目录存在，创建初始日志文件 */
function _init() {
  if (_fd !== null) return
  _bootstrapLogDir()
  const ts = _timestamp().replace(/[: ]/g, '-').replace(/\./g, '')
  const errors = []

  const candidates = _logDir
    ? [_logDir, ..._logDirCandidates().filter((candidate) => candidate !== _logDir)]
    : _logDirCandidates()

  for (const logDir of candidates) {
    try {
      if (!fs.existsSync(logDir)) fs.mkdirSync(logDir, { recursive: true })
      const filePath = path.join(logDir, `electron_${ts}.log`)
      const fd = fs.openSync(filePath, 'a')
      _fd = fd
      _filePath = filePath
      _logDir = logDir
      _written = fs.fstatSync(fd).size
      _appendBootstrapLine(logDir, `bootstrap:active file=${filePath}`)
      console.log(`[TradeRelay] Electron logger initialised at ${filePath}`)
      return
    } catch (error) {
      errors.push({ logDir, message: error && error.message ? error.message : String(error) })
    }
  }

  if (_bootstrapFilePath) {
    try {
      fs.appendFileSync(_bootstrapFilePath, `${_timestamp()} | bootstrap:error ${JSON.stringify(errors)}\n`, 'utf8')
    } catch {}
  }
  throw new Error(`Failed to initialize Electron logger: ${JSON.stringify(errors)}`)
}

/** 获取当前时间字符串（本地时间） */
function _timestamp() {
  const d = new Date()
  const pad = (n, len = 2) => String(n).padStart(len, '0')
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ` +
         `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`
}

/** 解析调用栈，返回 { file, line, func } */
function _caller() {
  const err = new Error()
  const lines = (err.stack || '').split('\n')
  // 跳过 Error、_caller、_write、log/info/… 共 4 帧
  for (let i = 4; i < lines.length; i++) {
    const m = lines[i].match(/at (?:(.+?) \()?(?:.+[/\\])?([^/\\(]+):(\d+):\d+\)?/)
    if (m) {
      return {
        func: m[1] || '<anonymous>',
        file: m[2] || '?',
        line: m[3] || '?',
      }
    }
  }
  return { func: '?', file: '?', line: '?' }
}

/** 滚动日志文件 */
function _rotate() {
  if (_fd !== null) {
    try { fs.closeSync(_fd) } catch {}
    _fd = null
  }
  // 将旧备份向后移一位
  for (let i = BACKUP_COUNT - 1; i >= 1; i--) {
    const src = `${_filePath}.${i}`
    const dst = `${_filePath}.${i + 1}`
    if (fs.existsSync(src)) {
      try { fs.renameSync(src, dst) } catch {}
    }
  }
  if (fs.existsSync(_filePath)) {
    try { fs.renameSync(_filePath, `${_filePath}.1`) } catch {}
  }
  _fd = fs.openSync(_filePath, 'a')
  _written = 0
}

/** 核心写入函数 */
function _write(levelName, message, extra) {
  try {
    _init()
  } catch (error) {
    const consoleFn = levelName === 'ERROR' ? console.error
      : levelName === 'WARN'  ? console.warn
      : levelName === 'DEBUG' ? console.debug
      : console.log
    consoleFn(`[TradeRelay] logger-init-failed ${error && error.message ? error.message : String(error)}`)
    return
  }
  const { func, file, line } = _caller()
  const extraStr = extra !== undefined
    ? ' ' + (typeof extra === 'string' ? extra : JSON.stringify(extra))
    : ''
  const line_ = `${_timestamp()} | ${levelName.padEnd(5)} | ${file}:${line} | ${func} | ${message}${extraStr}\n`
  const buf = Buffer.from(line_, 'utf8')

  if (_written + buf.length > MAX_BYTES) _rotate()

  try {
    fs.writeSync(_fd, buf)
    _written += buf.length
  } catch (e) {
    // 写失败时尝试重新打开文件
    try {
      _fd = fs.openSync(_filePath, 'a')
      fs.writeSync(_fd, buf)
      _written += buf.length
    } catch {}
  }

  // 同时输出到控制台
  const consoleFn = levelName === 'ERROR' ? console.error
    : levelName === 'WARN'  ? console.warn
    : levelName === 'DEBUG' ? console.debug
    : console.log
  consoleFn(`[TradeRelay] ${line_.trimEnd()}`)
}

// ── 公开 API ──────────────────────────────────────────────────────────────────
const logger = {
  debug: (msg, extra) => _write('DEBUG', msg, extra),
  info:  (msg, extra) => _write('INFO',  msg, extra),
  warn:  (msg, extra) => _write('WARN',  msg, extra),
  error: (msg, extra) => _write('ERROR', msg, extra),

  /** 获取当前日志文件路径（供外部查询） */
  getLogFile: () => { _init(); return _filePath },
  getBootstrapFile: () => {
    _bootstrapLogDir()
    return _bootstrapFilePath
  },

  /** 关闭文件句柄（进程退出前调用） */
  close: () => {
    if (_fd !== null) {
      try { fs.closeSync(_fd) } catch {}
      _fd = null
    }
  },
}

_bootstrapLogDir()

module.exports = { logger }
