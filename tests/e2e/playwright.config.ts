import { defineConfig } from '@playwright/test'

// Runs against the development stack with the labelled fake provider: `scripts/dev --reset --seed`.
// AMSG_E2E_BROWSER=webkit runs the suite on the engine macOS uses. Install it with `playwright install webkit`.
const webkit = process.env.AMSG_E2E_BROWSER === 'webkit'

export default defineConfig({
  testDir: './specs',
  timeout: 30000,
  workers: 1,
  fullyParallel: false,
  reporter: [['list']],
  use: {
    baseURL: process.env.AMSG_E2E_URL ?? 'http://127.0.0.1:1420',
    ...(webkit
      ? { browserName: 'webkit' as const }
      : { launchOptions: { executablePath: process.env.CHROMIUM ?? '/usr/bin/chromium' } }),
    colorScheme: 'light',
    viewport: { width: 1065, height: 800 },
    trace: 'off',
  },
})
