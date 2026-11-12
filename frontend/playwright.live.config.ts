import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  testMatch: 'live-stack.spec.ts',
  fullyParallel: false,
  reporter: 'list',
  timeout: 30000,
  use: { ...devices['Desktop Chrome'], baseURL: 'http://127.0.0.1:8080', trace: 'retain-on-failure' },
})
