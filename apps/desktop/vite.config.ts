import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// Development proxy. The token stays on this side: the browser only ever sees same-origin /api.
const devDir = resolve(__dirname, '../../.dev')

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'AMSG_')
  const target = env.AMSG_DEV_API ?? process.env.AMSG_DEV_API ?? 'http://127.0.0.1:8710'
  const tokenFile = process.env.AMSG_DEV_TOKEN_FILE ?? resolve(devDir, 'device-token')
  return {
    plugins: [react()],
    resolve: { alias: { '@': resolve(__dirname, 'src') } },
    clearScreen: false,
    build: { target: 'es2022', sourcemap: false },
    server: {
      host: '127.0.0.1',
      port: 1420,
      strictPort: true,
      proxy: {
        '/api': {
          target,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
          configure: (proxy) => {
            proxy.on('proxyReq', (request) => {
              if (existsSync(tokenFile)) {
                request.setHeader('Authorization', `Bearer ${readFileSync(tokenFile, 'utf8').trim()}`)
              }
            })
          },
        },
      },
    },
  }
})
