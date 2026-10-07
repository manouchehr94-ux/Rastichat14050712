import { defineConfig } from '@playwright/test';

// Integration-platform contract checks against the ISOLATED SANDBOX STAGING stack (nginx TLS -> balancer -> 2 Daphne workers),
// driven over real HTTPS/WSS. Pure protocol tests: no browser needed (the browser flows run in reference-host / rastisi / staging-django52).
// Env comes from scripts/staging/sandbox/pw.env + $SANDBOX_DIR/stg/integration.json (see run-matrix.sh).
export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.mjs/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120000,
  expect: { timeout: 15000 },
  reporter: [['list']],
});
