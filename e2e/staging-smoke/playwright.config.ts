import { defineConfig, devices } from '@playwright/test';

// Every URL this suite touches comes from the environment — there is no
// localhost fallback anywhere in this config or in smoke.spec.ts. Running
// it without these set fails immediately and loudly (see smoke.spec.ts's
// own env-var assertions) rather than silently testing the wrong thing.
export default defineConfig({
  testDir: '.',
  testMatch: 'smoke.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 1,
  timeout: 60000,
  expect: { timeout: 15000 },
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'smoke-report' }]],
  use: {
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ignoreHTTPSErrors: false,
    launchOptions: {
      args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
    },
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
