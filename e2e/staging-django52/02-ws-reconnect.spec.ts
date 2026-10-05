import { test, expect } from '@playwright/test';
import { ALLOWED_EMBED_ORIGIN, FORBIDDEN_EMBED_ORIGIN } from './env';
import { authFrames, captureSockets, loginOperator, openConversationWithText, openWidgetAt, operatorReply, restartBackendWorkers, sendWidgetText, serveEmbedAt, uniqueText } from './helpers';

const ORIGIN = ALLOWED_EMBED_ORIGIN || FORBIDDEN_EMBED_ORIGIN;

test.describe('WebSocket reconnect behaviour (Django 5.2 / Channels stack)', () => {
  test('the widget shows the offline banner on network loss, reconnects with a FRESH ticket and keeps chatting', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    await serveEmbedAt(customerCtx, ORIGIN);
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();
    const sockets = captureSockets(customer);

    await openWidgetAt(customer, ORIGIN);
    await expect.poll(() => sockets.some((s) => s.received.some((f) => f.includes('auth.ok')))).toBe(true);
    const firstTicket = authFrames(sockets[0])[0].ticket;

    const before = uniqueText('DJ52-before-outage');
    await sendWidgetText(customer, before);
    await loginOperator(operator);
    await openConversationWithText(operator, before);

    // outage: every ASGI worker restarts, so every open socket is dropped by the server; the widget must say so ...
    test.skip(!restartBackendWorkers(), 'DJANGO52_RESTART_BACKEND_CMD is not set');
    await expect(customer.locator('#rasti-offline-banner.show')).toBeVisible({ timeout: 30000 });
    // ... and recover on its own once the backend is back
    await expect(customer.locator('#rasti-offline-banner.show')).toBeHidden({ timeout: 60000 });
    await expect.poll(() => sockets.filter((s) => s.received.some((f) => f.includes('auth.ok'))).length, { timeout: 30000 }).toBeGreaterThanOrEqual(2);
    const secondTicket = authFrames(sockets[sockets.length - 1])[0].ticket;
    expect(secondTicket).not.toBe(firstTicket); // single-use tickets: every reconnect mints a new one

    const after = uniqueText('DJ52-after-outage');
    await sendWidgetText(customer, after);
    await expect(operator.getByText(after).last()).toBeVisible({ timeout: 30000 });
    const reply = uniqueText('DJ52-reply-after-outage');
    await operatorReply(operator, reply);
    await expect(customer.locator('.rasti-msg.operator .rasti-bubble', { hasText: reply })).toBeVisible({ timeout: 20000 });
    await customerCtx.close();
    await operatorCtx.close();
  });

  test('two customer tabs (two sockets, same visitor) both receive the operator reply', async ({ browser }) => {
    const ctx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    await serveEmbedAt(ctx, ORIGIN);
    const tabA = await ctx.newPage();
    const tabB = await ctx.newPage();
    const operator = await operatorCtx.newPage();
    await openWidgetAt(tabA, ORIGIN);
    const marker = uniqueText('DJ52-multi');
    await sendWidgetText(tabA, marker);
    await openWidgetAt(tabB, ORIGIN); // same localStorage => same session and conversation
    await loginOperator(operator);
    await openConversationWithText(operator, marker);
    const reply = uniqueText('DJ52-multi-reply');
    await operatorReply(operator, reply);
    await expect(tabA.locator('.rasti-msg.operator .rasti-bubble', { hasText: reply })).toBeVisible({ timeout: 20000 });
    await expect(tabB.locator('.rasti-msg.operator .rasti-bubble', { hasText: reply })).toBeVisible({ timeout: 20000 });
    await ctx.close();
    await operatorCtx.close();
  });
});
