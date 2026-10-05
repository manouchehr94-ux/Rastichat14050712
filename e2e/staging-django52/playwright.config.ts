import { defineConfig, devices } from '@playwright/test';

// Chromium with device EMULATION covers viewport/touch/UA for mobile; real WebKit/Firefox engines are added when the
// runner has them installed (DJANGO52_EXTRA_BROWSERS=1) — Safari-class WebSocket/Audio behaviour is the main reason.
const extra = process.env.DJANGO52_EXTRA_BROWSERS === '1';

export default defineConfig({
  testDir: '.',
  testMatch: '*.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 1,
  timeout: 90000,
  expect: { timeout: 15000 },
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'django52-report' }]],
  use: {
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    ignoreHTTPSErrors: process.env.DJANGO52_IGNORE_HTTPS_ERRORS === '1',
    launchOptions: {
      executablePath: process.env.PW_CHROMIUM_PATH || undefined,
      args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--no-proxy-server', '--disable-background-networking', '--host-resolver-rules=' + (process.env.DJANGO52_HOST_RULES || 'MAP embed-allowed.example.test 127.0.0.1, MAP embed-forbidden.example.test 127.0.0.1')],
    },
  },
  projects: [
    { name: 'desktop-chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile-android', use: { ...devices['Pixel 7'], defaultBrowserType: 'chromium' } },
    { name: 'mobile-iphone-emulated', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
    ...(extra ? [
      { name: 'desktop-firefox', use: { ...devices['Desktop Firefox'] } },
      { name: 'desktop-webkit', use: { ...devices['Desktop Safari'] } },
      { name: 'mobile-iphone-webkit', use: { ...devices['iPhone 13'] } },
    ] : []),
  ],
});
