import { defineConfig } from '@playwright/test';
import path from 'path';

// Standalone config for the LAN smoke test — deliberately independent of
// e2e/playwright.config.ts, whose globalSetup shells out to a local (non-
// Docker) `python manage.py`. Here the backend runs inside Docker and the
// seeded project key is already resolved by RastiChat-LAN-Manager.ps1 into
// LAN_WIDGET_URL, so no globalSetup/fixture file is needed.
const runtimeDir = path.resolve(__dirname, '..', 'runtime');

export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60000,
  expect: { timeout: 10000 },
  outputDir: path.join(runtimeDir, 'smoke-test-results'),
  reporter: [
    ['list'],
    ['json', { outputFile: path.join(runtimeDir, 'smoke-results.json') }],
  ],
  use: {
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: {
      // Only set when the environment needs a non-default Chromium (e.g. a
      // sandbox with a pre-installed browser at a fixed path). Left unset,
      // Playwright uses its normal bundled/installed browser.
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined,
    },
  },
});
