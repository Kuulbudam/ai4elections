import { defineConfig, devices } from '@playwright/test'

// End-to-end test of the DEPLOYED site (Phase 8). Run from GitHub Actions (e2e.yml); needs
// E2E_WEB_URL, SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (to create a fresh test account only).
export default defineConfig({
  testDir: './e2e',
  timeout: 15 * 60 * 1000,
  expect: { timeout: 30_000 },
  retries: 0,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: process.env.E2E_WEB_URL ?? 'https://ai4elections-4d9a.vercel.app',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 30_000,
  },
  projects: [{ name: 'desktop', use: { ...devices['Desktop Chrome'] } }],
})
