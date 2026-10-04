/** Every value here comes from the environment — no localhost default, no
 * hardcoded domain. Missing a required one fails fast with a clear message
 * naming exactly which variable is missing, rather than the suite silently
 * running against `undefined` and producing confusing failures later.
 */
function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `${name} is required to run the staging smoke suite (see docs/runbooks/STAGING_DEPLOYMENT.md ` +
      `"Staging smoke suite" section for the full list and example values). Got: unset.`,
    );
  }
  return value;
}

export const BACKEND_URL = required('SMOKE_BACKEND_URL'); // e.g. https://chat-staging.rastisi.ir
export const OPERATOR_URL = required('SMOKE_OPERATOR_URL'); // e.g. https://operator-chat-staging.rastisi.ir
export const PLATFORM_URL = required('SMOKE_PLATFORM_URL'); // e.g. https://platform-chat-staging.rastisi.ir
export const WIDGET_URL = required('SMOKE_WIDGET_URL'); // e.g. https://chat-staging.rastisi.ir/widget.js
export const WS_URL = required('SMOKE_WS_URL'); // e.g. wss://chat-staging.rastisi.ir/ws
export const PROJECT_KEY = required('SMOKE_PROJECT_KEY'); // Project.public_key from seed_staging_data's output

// Credentials from seed_staging_data's one-time output (see
// common/management/commands/seed_staging_data.py) — never a fixed
// default, always whatever that command actually generated on this
// environment.
export const OWNER_EMAIL = required('SMOKE_OWNER_EMAIL');
export const OWNER_PASSWORD = required('SMOKE_OWNER_PASSWORD');
export const OPERATOR_EMAIL = required('SMOKE_OPERATOR_EMAIL');
export const OPERATOR_PASSWORD = required('SMOKE_OPERATOR_PASSWORD');

// Optional — /api/v1/health/monitoring/ requires this (see
// common.views.MonitoringView). Not required to run the suite: without it,
// the monitoring-endpoint assertion only checks for a non-5xx response
// (a 401 is expected and correct in that case), same as before this
// existed.
export const MONITORING_TOKEN = process.env.SMOKE_MONITORING_TOKEN || '';
