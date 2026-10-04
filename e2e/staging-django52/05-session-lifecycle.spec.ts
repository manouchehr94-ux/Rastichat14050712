import { test, expect } from '@playwright/test';
import { ALLOWED_EMBED_ORIGIN, BACKEND_URL, FORBIDDEN_EMBED_ORIGIN } from './env';
import { captureSockets, openWidgetAt, sendWidgetText, serveEmbedAt, uniqueText } from './helpers';

const ORIGIN = ALLOWED_EMBED_ORIGIN || FORBIDDEN_EMBED_ORIGIN;

test.describe('P1-2 visitor session lifecycle in a real browser', () => {
  test('logout revokes server-side, clears the browser and the next visit starts a clean guest session', async ({ browser }) => {
    const ctx = await browser.newContext();
    await serveEmbedAt(ctx, ORIGIN);
    const page = await ctx.newPage();
    await openWidgetAt(page, ORIGIN);
    const marker = uniqueText('DJ52-before-logout');
    await sendWidgetText(page, marker);
    const token = await page.evaluate(() => localStorage.getItem('rasti_session'));
    expect(token).toBeTruthy();

    await page.evaluate(() => window.RastiChat!.logout!());
    expect(await page.evaluate(() => localStorage.getItem('rasti_session'))).toBeNull();
    // the old credential is dead on the server (history endpoint, credential in the header)
    const dead = await page.request.get(`${BACKEND_URL}/api/v1/widget/conversations/00000000-0000-0000-0000-000000000000/messages/`, { headers: { 'X-Widget-Session': token!, Origin: ORIGIN } });
    expect(dead.status()).toBe(401);

    await page.reload();
    await page.locator('#rasti-launcher').click();
    await expect.poll(async () => page.evaluate(() => localStorage.getItem('rasti_session'))).not.toBeNull();
    expect(await page.evaluate(() => localStorage.getItem('rasti_session'))).not.toBe(token);
    await expect(page.locator('.rasti-bubble', { hasText: marker })).toHaveCount(0); // a fresh guest does not see the old history
    await ctx.close();
  });

  test('a session revoked behind the widget\'s back is recovered automatically on the next load', async ({ browser }) => {
    const ctx = await browser.newContext();
    await serveEmbedAt(ctx, ORIGIN);
    const page = await ctx.newPage();
    await openWidgetAt(page, ORIGIN);
    const token = (await page.evaluate(() => localStorage.getItem('rasti_session')))!;
    const res = await page.request.post(`${BACKEND_URL}/api/v1/widget/session/revoke/`, { data: { session_token: token }, headers: { Origin: ORIGIN } });
    expect(res.status()).toBe(204);
    const sockets = captureSockets(page);
    await page.reload();
    await page.locator('#rasti-launcher').click();
    await expect.poll(async () => page.evaluate(() => localStorage.getItem('rasti_session')), { timeout: 20000 }).not.toBe(token);
    await expect.poll(() => sockets.some((s) => s.received.some((f) => f.includes('auth.ok'))), { timeout: 20000 }).toBe(true);
    await ctx.close();
  });
});
