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

export async function openWidgetAt(page: Page, origin: string) {
  await page.goto(`${origin}/embed`);
  await page.locator('#rasti-launcher').click();
  await page.locator('#rasti-panel.open').waitFor();
}

export async function loginOperator(page: Page) {
  await page.goto(`${OPERATOR_URL}/login`);
  await page.locator('input[type="email"], form input[type="text"]').fill(OPERATOR_EMAIL);
  await page.locator('input[type="password"]').fill(OPERATOR_PASSWORD);
  await page.getByRole('button', { name: 'ورود' }).click();
  await page.waitForURL((u) => u.toString().replace(/\/$/, '') === OPERATOR_URL);
}

export async function sendWidgetText(page: Page, text: string) {
  await page.locator('#rasti-input').fill(text);
  await page.locator('#rasti-send').click();
  await expect(page.locator('.rasti-msg.visitor .rasti-bubble', { hasText: text })).toBeVisible();
}

/**
 * Lets a test cut ONE page's widget websocket (and refuse its reconnects) while everything else (REST, other pages, the
 * operator dashboard) keeps working: a per-visitor network outage. Call before the page opens the widget.
 */
export async function widgetSocketGate(page: Page) {
  let blocked = false;
  const live: Array<{ page: { close(o?: { code?: number }): void }; server: { close(): void } }> = [];
  await page.routeWebSocket(/\/ws\/v2\/widget\//, (route) => {
    if (blocked) { route.close({ code: 1006 }); return; }
    const server = route.connectToServer();
    live.push({ page: route, server });
  });
  return {
    cut() { blocked = true; for (const l of live.splice(0)) { try { l.server.close(); } catch { /* already closed */ } try { l.page.close({ code: 1006 }); } catch { /* already closed */ } } },
    restore() { blocked = false; },
  };
}

export function authFrames(log: SocketLog) {
  return log.sent.map((s) => { try { return JSON.parse(s); } catch { return null; } }).filter((f) => f && f.type === 'auth');
}

/** Any credential-looking material in a URL: JWTs (three base64url parts), UUID session tokens, query tokens. */
export function urlLeaksCredential(url: string): boolean {
  return /eyJ[\w-]+\.[\w-]+\.[\w-]+/.test(url) || /session_token=/.test(url) || /\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/[0-9a-f]{8}-/.test(url);
}
