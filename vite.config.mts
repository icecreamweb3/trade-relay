import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import fs from 'fs'

const packageJson = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, 'package.json'), 'utf-8'),
) as { version?: string }

const appVersion = packageJson.version ?? '0.0.0'
const buildTime = new Date().toISOString()
const devServerPort = Number.parseInt(process.env.VITE_DEV_SERVER_PORT || process.env.DEV_SERVER_PORT || '5173', 10)

export default defineConfig({
  plugins: [react()],
  base: './',
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
    __BUILD_TIME__: JSON.stringify(buildTime),
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '127.0.0.1',
    port: Number.isFinite(devServerPort) ? devServerPort : 5173,
    strictPort: true,
  },
})