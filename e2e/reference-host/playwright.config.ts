import { defineConfig, devices } from '@playwright/test';

// E2E for the NON-RastiSi reference host (examples/reference-host). Services are started by run.sh.
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
    { name: 'mobile', use: { ...devices['Pixel 7'] }, grep: /@mobile/ },
  ],
});
