import { test, expect } from '@playwright/test';
import { ALLOWED_EMBED_ORIGIN, BACKEND_URL, FORBIDDEN_EMBED_ORIGIN } from './env';
import { authFrames, captureSockets, loginOperator, openWidgetAt, sendWidgetText, serveEmbedAt, uniqueText, urlLeaksCredential } from './helpers';

const ORIGIN = ALLOWED_EMBED_ORIGIN || FORBIDDEN_EMBED_ORIGIN; // when no domains are configured any origin works

test.describe('P1-3 ticket-authenticated WebSockets (Django 5.2 stack)', () => {
  test('no credential in any socket URL, ticket sent as the first frame, chat round trip works', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    await serveEmbedAt(customerCtx, ORIGIN);
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();
    const customerSockets = captureSockets(customer);
    const operatorSockets = captureSockets(operator);

    const requestUrls: string[] = [];
    customer.on('request', (r) => requestUrls.push(r.url()));

    const marker = uniqueText('DJ52-TICKET سلام');
    await openWidgetAt(customer, ORIGIN);
    await sendWidgetText(customer, marker);
    await loginOperator(operator);
    await expect(operator.getByText(marker).last()).toBeVisible({ timeout: 20000 });
    await operator.getByText(marker).last().click(); // open the conversation (the dashboard does not auto-select one)
    const reply = uniqueText('DJ52-پاسخ');
    await operator.locator('input[placeholder="پاسخ به مشتری…"]').fill(reply);
    await operator.locator('button:has-text("➤")').click();
    await expect(customer.locator('.rasti-msg.operator .rasti-bubble', { hasText: reply })).toBeVisible({ timeout: 20000 });

    for (const log of [...customerSockets, ...operatorSockets]) {
      expect(urlLeaksCredential(log.url), `socket URL leaks a credential: ${log.url}`).toBe(false);
      expect(log.url).toContain('/ws/v2/');
      const frames = authFrames(log);
      expect(frames.length, `no auth frame on ${log.url}`).toBe(1);
      expect(String(log.sent[0])).toContain('"type":"auth"'); // the ticket is the FIRST thing sent
      expect(log.received.some((f) => f.includes('auth.ok'))).toBe(true);
    }
    expect(requestUrls.filter(urlLeaksCredential)).toEqual([]); // nor in any REST URL (history uses a header)
    await customerCtx.close();
    await operatorCtx.close();
  });

  test('a replayed ticket is refused by the server', async ({ browser }) => {
    const ctx = await browser.newContext();
    await serveEmbedAt(ctx, ORIGIN);
    const page = await ctx.newPage();
    const sockets = captureSockets(page);
    await openWidgetAt(page, ORIGIN);
    await expect.poll(() => sockets.some((s) => s.received.some((f) => f.includes('auth.ok')))).toBe(true);
    const ticket = authFrames(sockets[0])[0].ticket as string;
    const convUrl = sockets[0].url;
    const result = await page.evaluate(({ url, ticket }) => new Promise<string>((resolve) => {
      const ws = new WebSocket(url);
      ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', ticket }));
      ws.onmessage = (e) => resolve('message:' + e.data);
      ws.onclose = (e) => resolve('close:' + e.code);
      setTimeout(() => resolve('timeout'), 8000);
    }), { url: convUrl, ticket });
    expect(result).toBe('close:4401');
    await ctx.close();
  });

  test('legacy credentials-in-URL access is refused (staging default)', async ({ request }) => {
    test.skip(process.env.DJANGO52_EXPECT_LEGACY_URL_OFF === '0', 'legacy switch deliberately enabled for a rollout test');
    const res = await request.get(`${BACKEND_URL}/api/v1/widget/conversations/00000000-0000-0000-0000-000000000000/messages/?session_token=00000000-0000-0000-0000-000000000000`);
    expect(res.status()).toBe(401);
  });
});
