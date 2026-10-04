import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  globalSetup: require.resolve('./global-setup'),
  fullyParallel: false,
  workers: 1,
  retries: 1,
  expect: { timeout: 8000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: {
      // The installed @playwright/test version's pinned browser build doesn't
      // match what's pre-installed in this sandbox; point at it explicitly
      // rather than downloading a second copy.
      executablePath: '/opt/pw-browsers/chromium',
      // Voice-message scenarios call getUserMedia({ audio: true }); these flags
      // give Chromium a synthetic audio device and auto-accept the permission
      // prompt instead of hanging on a real (nonexistent, in CI) microphone.
      args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
    },
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
