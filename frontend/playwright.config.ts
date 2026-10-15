/**
 * End-to-end click-through (design 7.7 / 8.4 / 8.6): the installed Microsoft
 * Edge (no browser download), headless, ONE worker, against a running Django
 * (+ Vite dev server, or the built SPA served by Django) with a small seeded
 * data set. Run by the PM once the backend is up:
 *
 *   E2E_MAKER_USER=... E2E_MAKER_PASSWORD=... \
 *   E2E_CHECKER_USER=... E2E_CHECKER_PASSWORD=... npm run e2e
 *
 * E2E_BASE_URL defaults to the Vite dev server (http://localhost:5173); use
 * http://localhost:8000 to test the production build served by Django.
 */
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  outputDir: 'test-results',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    channel: 'msedge',
    headless: true,
    viewport: { width: 1366, height: 768 },
    locale: 'en-IN',
    timezoneId: 'Asia/Kolkata',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
