/**
 * Environment for the Django 5.2 STAGING verification suite.
 *
 * This suite exists to prove the Django 4.2 -> 5.2 LTS upgrade (plus the P1 WebSocket/session/domain
 * hardening) on an ISOLATED staging stack with synthetic data BEFORE anything is deployed for real.
 * It therefore refuses to run unless the operator explicitly confirms isolation, and refuses any host
 * that looks like the live chat or the live RastiSi platform. Every URL comes from the environment.
 */
function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required (see docs/runbooks/DJANGO52_STAGING_TEST_PLAN.md §4). Got: unset.`);
  return value;
}

const FORBIDDEN_DEFAULT = ['chatchat.rastisi.ir', 'rastisi.ir', 'www.rastisi.ir', 'app.rastisi.ir'];

export function hostOf(url: string): string {
  return new URL(url).hostname.toLowerCase();
}

export function assertIsolated(urls: Record<string, string>): void {
  if (process.env.DJANGO52_ISOLATED_STAGING !== 'yes-this-is-an-isolated-staging-stack') {
    throw new Error(
      'Refusing to run: set DJANGO52_ISOLATED_STAGING=yes-this-is-an-isolated-staging-stack to confirm this points at a ' +
      'separate staging stack with synthetic data (never the live chat / RastiSi).',
    );
  }
  const forbidden = new Set([...FORBIDDEN_DEFAULT, ...(process.env.DJANGO52_FORBIDDEN_HOSTS || '').split(',').map((h) => h.trim().toLowerCase()).filter(Boolean)]);
  for (const [name, url] of Object.entries(urls)) {
    const host = hostOf(url);
    if (forbidden.has(host)) throw new Error(`Refusing to run: ${name}=${url} is a forbidden (live) host (${host}).`);
  }
}

export const BACKEND_URL = required('SMOKE_BACKEND_URL').replace(/\/$/, '');
export const OPERATOR_URL = required('SMOKE_OPERATOR_URL').replace(/\/$/, '');
export const PLATFORM_URL = required('SMOKE_PLATFORM_URL').replace(/\/$/, '');
export const WIDGET_URL = required('SMOKE_WIDGET_URL');
export const WS_URL = required('SMOKE_WS_URL').replace(/\/$/, '');
export const PROJECT_KEY = required('SMOKE_PROJECT_KEY');
export const OPERATOR_EMAIL = required('SMOKE_OPERATOR_EMAIL');
export const OPERATOR_PASSWORD = required('SMOKE_OPERATOR_PASSWORD');

/** Origins used by the domain tests: ALLOWED must be in the project's allowed_domains, FORBIDDEN must not. They are
 * served by Playwright itself (route fulfilment) so they need no DNS. Example: https://embed-allowed.example.test */
export const ALLOWED_EMBED_ORIGIN = process.env.DJANGO52_ALLOWED_EMBED_ORIGIN || '';
export const FORBIDDEN_EMBED_ORIGIN = process.env.DJANGO52_FORBIDDEN_EMBED_ORIGIN || 'https://embed-forbidden.example.test';

/** Set when the stack under test runs the P1 hardening with LEGACY_URL_CREDENTIALS_ENABLED off (the staging default). */
export const EXPECT_LEGACY_URL_OFF = process.env.DJANGO52_EXPECT_LEGACY_URL_OFF !== '0';

assertIsolated({ SMOKE_BACKEND_URL: BACKEND_URL, SMOKE_OPERATOR_URL: OPERATOR_URL, SMOKE_PLATFORM_URL: PLATFORM_URL, SMOKE_WIDGET_URL: WIDGET_URL, SMOKE_WS_URL: WS_URL.replace(/^ws/, 'http') });
