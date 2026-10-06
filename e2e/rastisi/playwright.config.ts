import { defineConfig, devices } from '@playwright/test';

// Cross-system E2E: real RastiSi (adapter) + real RastiChat, both local and synthetic. Services are started by run.sh.
export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  expect: { timeout: 10000 },
  reporter: [['list']],
  use: {
    trace: 'retain-on-failure',
    launchOptions: { executablePath: process.env.PW_CHROMIUM || '/opt/pw-browsers/chromium' },
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
  ],
});
