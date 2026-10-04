import { test, expect } from '@playwright/test';
import { ALLOWED_EMBED_ORIGIN, BACKEND_URL, FORBIDDEN_EMBED_ORIGIN, PROJECT_KEY } from './env';
import { captureSockets, serveEmbedAt } from './helpers';

test.describe('P1-4 allowed domains, CORS and WebSocket origin (needs DJANGO52_ALLOWED_EMBED_ORIGIN configured on the project)', () => {
  test.skip(!ALLOWED_EMBED_ORIGIN, 'set DJANGO52_ALLOWED_EMBED_ORIGIN to an origin listed in the project allowed_domains');

  test('an allowed embedding origin starts the widget and opens a socket', async ({ browser }) => {
    const ctx = await browser.newContext();
    await serveEmbedAt(ctx, ALLOWED_EMBED_ORIGIN);
    const page = await ctx.newPage();
    const sockets = captureSockets(page);
    const init = page.waitForResponse((r) => r.url().includes('/widget/init/'));
    await page.goto(`${ALLOWED_EMBED_ORIGIN}/embed`);
    expect((await init).status()).toBe(200);
    await expect.poll(() => sockets.some((s) => s.received.some((f) => f.includes('auth.ok')))).toBe(true);
    await ctx.close();
  });

  test('a forbidden embedding origin cannot create a session (403 or CORS-blocked) and never opens a socket', async ({ browser }) => {
    const ctx = await browser.newContext();
    await serveEmbedAt(ctx, FORBIDDEN_EMBED_ORIGIN);
    const page = await ctx.newPage();
    const sockets = captureSockets(page);
    const statuses: number[] = [];
    page.on('response', (r) => { if (r.url().includes('/widget/init/')) statuses.push(r.status()); });
    const failed: string[] = [];
    page.on('requestfailed', (r) => { if (r.url().includes('/widget/init/')) failed.push(r.failure()?.errorText || ''); });
    await page.goto(`${FORBIDDEN_EMBED_ORIGIN}/embed`);
    await page.waitForTimeout(4000);
    const blocked = statuses.every((s) => s === 403) && statuses.length > 0 ? true : failed.length > 0; // 403 JSON, or the browser blocked it at CORS
    expect(blocked, `statuses=${statuses} failed=${failed}`).toBe(true);
    expect(sockets.length).toBe(0);
    await ctx.close();
  });

  test('API level: forbidden Origin gets 403 origin_not_allowed, missing Origin gets origin_required, allowed passes', async ({ request }) => {
    const body = { project_key: PROJECT_KEY };
    const forbidden = await request.post(`${BACKEND_URL}/api/v1/widget/init/`, { data: body, headers: { Origin: FORBIDDEN_EMBED_ORIGIN } });
    expect(forbidden.status()).toBe(403);
    expect((await forbidden.json()).code).toBe('origin_not_allowed');
    const none = await request.post(`${BACKEND_URL}/api/v1/widget/init/`, { data: body });
    expect(none.status()).toBe(403);
    expect((await none.json()).code).toBe('origin_required');
    const ok = await request.post(`${BACKEND_URL}/api/v1/widget/init/`, { data: body, headers: { Origin: ALLOWED_EMBED_ORIGIN } });
    expect(ok.status()).toBe(200);
  });

  test('CORS preflight: configured origin is echoed for widget endpoints only, never for dashboard endpoints', async ({ request }) => {
    const pre = (path: string) => request.fetch(`${BACKEND_URL}${path}`, {
      method: 'OPTIONS',
      headers: { Origin: ALLOWED_EMBED_ORIGIN, 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type,x-widget-session' },
    });
    const widget = await pre('/api/v1/widget/init/');
    expect(widget.headers()['access-control-allow-origin']).toBe(ALLOWED_EMBED_ORIGIN);
    expect(widget.headers()['access-control-allow-headers'].toLowerCase()).toContain('x-widget-session');
    for (const path of ['/api/v1/auth/login/', '/api/v1/ws/ticket/', '/api/v1/support/']) {
      expect((await pre(path)).headers()['access-control-allow-origin'], path).toBeUndefined();
    }
  });
});
