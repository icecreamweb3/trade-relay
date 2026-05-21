#!/usr/bin/env node

import { createServer } from 'node:net'
import { spawn } from 'node:child_process'
import http from 'node:http'

const DEFAULT_PORT = Number.parseInt(process.env.DEV_SERVER_PORT || '5173', 10)
const HOST = '127.0.0.1'
const STARTUP_TIMEOUT_MS = 30_000
const POLL_INTERVAL_MS = 300
const DRY_RUN = process.argv.includes('--dry-run')

function resolveNpmCommand() {
  return process.platform === 'win32' ? 'npm.cmd' : 'npm'
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function canBindPort(port) {
  return new Promise((resolve) => {
    const server = createServer()
    server.once('error', () => resolve(false))
    server.listen(port, HOST, () => {
      server.close(() => resolve(true))
    })
  })
}

async function findAvailablePort(startPort) {
  const initialPort = Number.isFinite(startPort) ? startPort : 5173
  for (let offset = 0; offset < 20; offset += 1) {
    const candidate = initialPort + offset
    if (await canBindPort(candidate)) {
      return candidate
    }
  }
  throw new Error(`No available dev port found starting from ${initialPort}`)
}

function isServerReady(url) {
  return new Promise((resolve) => {
    const request = http.get(url, (response) => {
      response.resume()
      resolve((response.statusCode ?? 500) < 500)
    })
    request.on('error', () => resolve(false))
    request.setTimeout(1000, () => {
      request.destroy()
      resolve(false)
    })
  })
}

async function waitForServer(url, viteProcess) {
  const startedAt = Date.now()
  while (Date.now() - startedAt < STARTUP_TIMEOUT_MS) {
    if (viteProcess.exitCode !== null) {
      throw new Error(`Vite exited early with code ${viteProcess.exitCode}`)
    }
    if (await isServerReady(url)) {
      return
    }
    await wait(POLL_INTERVAL_MS)
  }
  throw new Error(`Timed out waiting for Vite dev server at ${url}`)
}

async function main() {
  const npmCommand = resolveNpmCommand()
  const port = await findAvailablePort(DEFAULT_PORT)
  const devServerUrl = `http://${HOST}:${port}`

  console.log(`[dev] using ${devServerUrl}`)
  if (DRY_RUN) {
    return
  }

  const sharedEnv = {
    ...process.env,
    DEV_SERVER_PORT: String(port),
    VITE_DEV_SERVER_PORT: String(port),
    DEV_SERVER_URL: devServerUrl,
  }

  const viteProcess = spawn(
    npmCommand,
    ['run', 'vite', '--', '--host', HOST, '--port', String(port), '--strictPort'],
    {
      cwd: process.cwd(),
      env: sharedEnv,
      stdio: 'inherit',
    },
  )

  let electronProcess = null
  const terminateChildren = () => {
    if (electronProcess && electronProcess.exitCode === null) {
      electronProcess.kill('SIGTERM')
    }
    if (viteProcess.exitCode === null) {
      viteProcess.kill('SIGTERM')
    }
  }

  process.on('SIGINT', () => {
    terminateChildren()
    process.exit(130)
  })
  process.on('SIGTERM', () => {
    terminateChildren()
    process.exit(143)
  })

  try {
    await waitForServer(devServerUrl, viteProcess)
  } catch (error) {
    terminateChildren()
    throw error
  }

  electronProcess = spawn(npmCommand, ['run', 'electron:dev'], {
    cwd: process.cwd(),
    env: sharedEnv,
    stdio: 'inherit',
  })

  electronProcess.on('exit', (code) => {
    if (viteProcess.exitCode === null) {
      viteProcess.kill('SIGTERM')
    }
    process.exit(code ?? 0)
  })

  viteProcess.on('exit', (code) => {
    if (electronProcess && electronProcess.exitCode === null) {
      electronProcess.kill('SIGTERM')
    }
    process.exit(code ?? 0)
  })
}

main().catch((error) => {
  console.error(`[dev] ${error instanceof Error ? error.message : String(error)}`)
  process.exit(1)
})