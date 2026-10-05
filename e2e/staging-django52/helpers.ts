import { execSync } from 'node:child_process';
import { expect, type BrowserContext, type Page } from '@playwright/test';
import {
  BACKEND_URL, OPERATOR_URL, WIDGET_URL, WS_URL, PROJECT_KEY, OPERATOR_EMAIL, OPERATOR_PASSWORD,
} from './env';

export function uniqueText(label: string) {
  return `${label} ${Date.now()}-${Math.floor(Math.random() * 10000)}`;
}

/** HTML of a minimal storefront page embedding the widget exactly as a customer site would. */
export function embedHtml(): string {
  return `<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>embed</title></head>
<body><script src="${WIDGET_URL}"></script>
<script>window.addEventListener('load', function () { window.RastiChat.init({ projectKey: ${JSON.stringify(PROJECT_KEY)}, apiBase: ${JSON.stringify(BACKEND_URL + '/api/v1')}, wsBase: ${JSON.stringify(WS_URL)} }); });</script></body></html>`;
}

/** Serve the embed page from an arbitrary (fake) origin — no DNS needed; the browser sends that origin as `Origin`. */
export async function serveEmbedAt(context: BrowserContext, origin: string) {
  if (process.env.DJANGO52_REAL_EMBED_SITES === '1') return; // the stack serves real storefront origins (stack.sh embed)
  await context.route(`${origin}/**`, (route) => route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: embedHtml() }));
}

export interface SocketLog { url: string; sent: string[]; received: string[]; closedAt?: number }

/** Records every WebSocket the page opens, with the frames in both directions. */
export function captureSockets(page: Page): SocketLog[] {
  const sockets: SocketLog[] = [];
  page.on('websocket', (ws) => {
    const log: SocketLog = { url: ws.url(), sent: [], received: [] };
    sockets.push(log);
    ws.on('framesent', (f) => log.sent.push(String(f.payload)));
    ws.on('framereceived', (f) => log.received.push(String(f.payload)));
    ws.on('close', () => { log.closedAt = Date.now(); });
  });
  return sockets;
}

/** Open the widget and wait until its realtime socket is authenticated (`auth.ok`): sending earlier is ignored by the widget. */
export async function openWidgetAt(page: Page, origin: string) {
  await page.goto(`${origin}/embed`);
  // registered BEFORE the click so an auth.ok that arrives within a millisecond of the socket opening cannot be missed
  const authenticated = new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('the widget socket never reached auth.ok')), 25000);
    page.on('websocket', (ws) => ws.on('framereceived', (f) => {
      if (String(f.payload).includes('auth.ok')) { clearTimeout(timer); resolve(); }
    }));
  });
  await page.locator('#rasti-launcher').click();
  await page.locator('#rasti-panel.open').waitFor();
  await authenticated;
}

/** The operator inbox lists the conversation by its last message; select it so the composer appears. */
export async function openConversationWithText(operator: Page, text: string) {
  const row = operator.getByText(text).last();
  await expect(row).toBeVisible({ timeout: 30000 });
  await row.click();
  await expect(operator.locator('input[placeholder="پاسخ به مشتری…"]')).toBeVisible({ timeout: 15000 });
}

export async function operatorReply(operator: Page, text: string) {
  await operator.locator('input[placeholder="پاسخ به مشتری…"]').fill(text);
  await operator.locator('button:has-text("➤")').click();
}

/** Drops every open WebSocket the way a real outage does: restarts the ASGI workers (hook provided by the stack). */
export function restartBackendWorkers() {
  const cmd = process.env.DJANGO52_RESTART_BACKEND_CMD;
  if (!cmd) return false;
  execSync(cmd, { stdio: 'inherit', timeout: 120000 });
  return true;
}

let cachedOperatorSession: { access: string; user: unknown } | null = null;

/**
 * Operator signed in WITHOUT driving the login form every time: nginx rate-limits /auth/login/ (10/min per IP, as in
 * production) and the matrix would trip it. The JWT is obtained once through the real login API and placed where the
 * dashboard keeps it; the UI login form itself is exercised once in `loginViaForm`.
 */
export async function loginOperator(page: Page) {
  if (!cachedOperatorSession) {
    const res = await page.request.post(`${BACKEND_URL}/api/v1/auth/login/`, { data: { email: OPERATOR_EMAIL, password: OPERATOR_PASSWORD } });
    expect(res.status(), 'operator API login').toBe(200);
    const body = await res.json();
    cachedOperatorSession = { access: body.access, user: body.user };
  }
  const { access, user } = cachedOperatorSession;
  await page.addInitScript(([token, userJson]) => { localStorage.setItem('token', token); localStorage.setItem('user', userJson); }, [access, JSON.stringify(user)]);
  await page.goto(`${OPERATOR_URL}/`);
  await expect(page.locator('input[placeholder^="جستجوی"]')).toBeVisible({ timeout: 30000 });
}

/** The real login form (one use per run, see loginOperator). */
export async function loginViaForm(page: Page) {
  await page.goto(`${OPERATOR_URL}/login`);
  await page.locator('input[type="email"], input[type="text"]').first().fill(OPERATOR_EMAIL);
  await page.locator('input[type="password"]').fill(OPERATOR_PASSWORD);
  await page.getByRole('button', { name: 'ورود' }).click();
  await page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 30000, waitUntil: 'commit' });
  await expect(page.locator('input[placeholder^="جستجوی"]')).toBeVisible({ timeout: 30000 });
}

export async function sendWidgetText(page: Page, text: string) {
  await page.locator('#rasti-input').fill(text);
  await page.locator('#rasti-send').click();
  await expect(page.locator('.rasti-msg.visitor .rasti-bubble', { hasText: text })).toBeVisible();
}

export function authFrames(log: SocketLog) {
  return log.sent.map((s) => { try { return JSON.parse(s); } catch { return null; } }).filter((f) => f && f.type === 'auth');
}

/** Any credential-looking material in a URL: JWTs (three base64url parts), UUID session tokens, query tokens. */
export function urlLeaksCredential(url: string): boolean {
  return /eyJ[\w-]+\.[\w-]+\.[\w-]+/.test(url) || /session_token=/.test(url) || /\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/[0-9a-f]{8}-/.test(url);
}
