import { Page, BrowserContext } from '@playwright/test';
import { execFileSync } from 'child_process';
import { readFileSync } from 'fs';
import path from 'path';

export const DASHBOARD_URL = 'http://localhost:3000';
export const PLATFORM_DASHBOARD_URL = 'http://localhost:3001';
export const WIDGET_URL = 'http://localhost:8081/e2e.html';

export const fixture: { projectKey: string; productId: string | null; teamId: string | null; queueId: string | null } = JSON.parse(
  readFileSync(path.resolve(__dirname, '..', '.fixture.json'), 'utf-8'),
);

export async function openWidget(page: Page) {
  await page.goto(`${WIDGET_URL}?projectKey=${fixture.projectKey}`);
  await page.locator('#rasti-launcher').click();
  await page.locator('#rasti-panel.open').waitFor();
}

export async function sendWidgetText(page: Page, text: string) {
  await page.locator('#rasti-input').fill(text);
  await page.locator('#rasti-send').click();
}

export async function loginOperator(page: Page, email = 'operator@ws.com', password = 'pass1234') {
  await page.goto(`${DASHBOARD_URL}/login`);
  await page.locator('input[type="email"]').fill(email);
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole('button', { name: 'ورود' }).click();
  await page.waitForURL(`${DASHBOARD_URL}/`);
}

export async function loginPlatformSupport(page: Page, email = 'support@platform.com', password = 'pass1234') {
  await page.goto(`${PLATFORM_DASHBOARD_URL}/login`);
  await page.locator('input[type="email"]').fill(email);
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole('button', { name: 'ورود' }).click();
  await page.waitForURL(`${PLATFORM_DASHBOARD_URL}/inbox`);
}

/** Runs a one-off Django shell script against the live DB — the same
 * mechanism global-setup already uses to pull fixture IDs. Used here to set
 * up/observe state a browser can't reach directly (deterministic SLA due
 * dates, cross-tenant fixtures, presence/capacity) without inventing
 * test-only backend endpoints.
 */
export function runDjangoScript(script: string): string {
  const backendDir = path.resolve(__dirname, '..', '..', 'backend');
  const pythonBin = process.env.E2E_PYTHON || 'python';
  return execFileSync(pythonBin, ['manage.py', 'shell', '-c', script], {
    cwd: backendDir, env: process.env, encoding: 'utf-8',
  });
}

/** Runs the idempotent SLA evaluator once against the live DB — the
 * deterministic fixture the E2E suite uses instead of waiting on real
 * wall-clock time to observe approaching/breach states.
 */
export function runEvaluateSla() {
  const backendDir = path.resolve(__dirname, '..', '..', 'backend');
  const pythonBin = process.env.E2E_PYTHON || 'python';
  execFileSync(pythonBin, ['manage.py', 'evaluate_sla'], { cwd: backendDir, env: process.env, encoding: 'utf-8' });
}

/** Finds the conversation a just-sent unique marker message landed in —
 * lets a test locate "its" conversation without any ID ever appearing in
 * the UI.
 */
export function findConversationIdByMessageContent(content: string): string {
  const output = runDjangoScript(`
from conversations.models import Message
m = Message.objects.filter(content=${JSON.stringify(content)}).order_by('-created_at').first()
print('CONV_ID=' + (str(m.conversation_id) if m else 'None'))
`);
  const line = output.split('\n').find((l) => l.startsWith('CONV_ID='));
  const id = line ? line.replace('CONV_ID=', '') : 'None';
  if (id === 'None') throw new Error(`No conversation found for message: ${content}`);
  return id;
}

/** Backdates a conversation's ConversationSLA.first_response_due_at into
 * the "approaching" window (a few seconds out) or the past (breached),
 * bypassing real wall-clock waiting with a deterministic fixture.
 */
export function forceSlaFirstResponseState(convId: string, state: 'approaching' | 'breached') {
  const offset = state === 'approaching' ? 'timedelta(seconds=5)' : '-timedelta(minutes=1)';
  runDjangoScript(`
from django.utils import timezone
from datetime import timedelta
from sla.models import ConversationSLA
sla = ConversationSLA.objects.get(conversation_id=${JSON.stringify(convId)})
sla.first_response_due_at = timezone.now() + ${offset}
sla.first_response_breached_at = None
sla.first_response_approaching_notified_at = None
sla.save(update_fields=['first_response_due_at', 'first_response_breached_at', 'first_response_approaching_notified_at'])
`);
}

/** Reload the dashboard's conversation list, then open the row whose preview matches `text` exactly. */
export async function openConversationByPreview(page: Page, text: string) {
  await page.reload();
  const row = page.getByText(text, { exact: true }).first();
  await row.waitFor({ timeout: 15000 });
  await row.click();
}

export async function newContext(browser: { newContext: () => Promise<BrowserContext> }) {
  return browser.newContext();
}

// A 1x1 transparent PNG, used to exercise the real (now-validated) upload path.
export const TINY_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';
