import { readFileSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { test, expect } from '@playwright/test';
import { ALLOWED_EMBED_ORIGIN, FORBIDDEN_EMBED_ORIGIN } from './env';
import { authFrames, captureSockets, loginOperator, openWidgetAt, sendWidgetText, serveEmbedAt, uniqueText, widgetSocketGate } from './helpers';

const ORIGIN = ALLOWED_EMBED_ORIGIN || FORBIDDEN_EMBED_ORIGIN;

const BAL = process.env.STG_BALANCER_PIDFILE;
const netDown = () => { if (BAL) execSync(`kill -USR1 ${readFileSync(BAL, 'utf8').trim()}`); };
const netUp = () => { if (BAL) execSync(`kill -USR2 ${readFileSync(BAL, 'utf8').trim()}`); };

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
    await expect(operator.getByText(before).last()).toBeVisible({ timeout: 20000 });
    await operator.getByText(before).last().click(); // open the conversation (the dashboard does not auto-select one)

    // network loss: the browser drops the socket; the widget must say so ...
    await customerCtx.setOffline(true);
    netDown(); // Chromium keeps an open WebSocket alive under setOffline: really cut the connections at the balancer
    await expect(customer.locator('#rasti-offline-banner.show')).toBeVisible({ timeout: 20000 });
    // ... and recover on its own once the network is back
    netUp();
    await customerCtx.setOffline(false);
    await expect(customer.locator('#rasti-offline-banner.show')).toBeHidden({ timeout: 30000 });
    await expect.poll(() => sockets.filter((s) => s.received.some((f) => f.includes('auth.ok'))).length, { timeout: 30000 }).toBeGreaterThanOrEqual(2);
    const secondTicket = authFrames(sockets[sockets.length - 1])[0].ticket;
    expect(secondTicket).not.toBe(firstTicket); // single-use tickets: every reconnect mints a new one

    const after = uniqueText('DJ52-after-outage');
    await sendWidgetText(customer, after);
    await expect(operator.getByText(after).last()).toBeVisible({ timeout: 20000 });
    const reply = uniqueText('DJ52-reply-after-outage');
    await operator.locator('input[placeholder="پاسخ به مشتری…"]').fill(reply);
    await operator.locator('button:has-text("➤")').click();
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
    const socketsB = captureSockets(tabB);
    await openWidgetAt(tabB, ORIGIN); // same localStorage => same session and conversation
    await expect.poll(() => socketsB.some((x) => x.received.some((f) => f.includes('auth.ok'))), { timeout: 20000, message: 'tab B socket never authenticated' }).toBe(true);
    await loginOperator(operator);
    await expect(operator.getByText(marker).last()).toBeVisible({ timeout: 20000 });
    await operator.getByText(marker).last().click(); // open the conversation (the dashboard does not auto-select one)
    const reply = uniqueText('DJ52-multi-reply');
    await operator.locator('input[placeholder="پاسخ به مشتری…"]').fill(reply);
    await operator.locator('button:has-text("➤")').click();
    await expect(tabA.locator('.rasti-msg.operator .rasti-bubble', { hasText: reply })).toBeVisible({ timeout: 20000 });
    await expect(tabB.locator('.rasti-msg.operator .rasti-bubble', { hasText: reply })).toBeVisible({ timeout: 20000 });
    await ctx.close();
    await operatorCtx.close();
  });

  test('F-3: two tabs lose their sockets, the operator replies meanwhile; after the reconnect both tabs show the reply (history resync)', async ({ browser }) => {
    const ctx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    await serveEmbedAt(ctx, ORIGIN);
    const tabA = await ctx.newPage();
    const tabB = await ctx.newPage();
    const gateA = await widgetSocketGate(tabA);
    const gateB = await widgetSocketGate(tabB);
    const operator = await operatorCtx.newPage();
    const socketsA = captureSockets(tabA);
    const socketsB = captureSockets(tabB);
    await openWidgetAt(tabA, ORIGIN);
    const marker = uniqueText('DJ52-gap');
    await sendWidgetText(tabA, marker);
    await openWidgetAt(tabB, ORIGIN);
    const authed = (s: ReturnType<typeof captureSockets>) => s.filter((x) => x.received.some((f) => f.includes('auth.ok'))).length;
    await expect.poll(() => authed(socketsA) + authed(socketsB), { timeout: 20000 }).toBeGreaterThanOrEqual(2);
    await loginOperator(operator);
    await operator.getByText(marker).last().click();

    // outage of BOTH visitor sockets; the operator is unaffected and answers while they are gone
    gateA.cut(); gateB.cut();
    await expect(tabA.locator('#rasti-offline-banner.show')).toBeVisible({ timeout: 20000 });
    await expect(tabB.locator('#rasti-offline-banner.show')).toBeVisible({ timeout: 20000 });
    const reply = uniqueText('DJ52-gap-reply');
    await operator.locator('input[placeholder="پاسخ به مشتری…"]').fill(reply);
    await operator.locator('button:has-text("➤")').click();
    await expect(operator.getByText(reply).last()).toBeVisible({ timeout: 20000 });
    await expect(tabA.locator('.rasti-msg.operator .rasti-bubble', { hasText: reply })).toHaveCount(0);

    // connectivity returns: each tab reconnects with a fresh ticket, resyncs the history and shows the reply exactly once
    gateA.restore(); gateB.restore();
    for (const tab of [tabA, tabB]) {
      await expect(tab.locator('.rasti-msg.operator .rasti-bubble', { hasText: reply })).toHaveCount(1, { timeout: 30000 });
      await expect(tab.locator('#rasti-offline-banner.show')).toBeHidden({ timeout: 30000 });
    }
    // a message typed while offline is kept, shown as pending, and delivered after the reconnect (F-2)
    await expect.poll(() => authed(socketsA), { timeout: 30000 }).toBeGreaterThanOrEqual(2);
    gateA.cut();
    await expect(tabA.locator('#rasti-offline-banner.show')).toBeVisible({ timeout: 20000 });
    const offlineMsg = uniqueText('DJ52-typed-offline');
    await tabA.locator('#rasti-input').fill(offlineMsg);
    await tabA.locator('#rasti-send').click();
    await expect(tabA.locator('.rasti-msg.visitor.rasti-pending .rasti-bubble', { hasText: offlineMsg })).toBeVisible();
    gateA.restore();
    await expect(operator.getByText(offlineMsg).last()).toBeVisible({ timeout: 40000 });
    await expect(tabA.locator('.rasti-msg.visitor .rasti-bubble', { hasText: offlineMsg })).toHaveCount(1);
    await expect(tabA.locator('.rasti-msg.rasti-pending')).toHaveCount(0);
    await ctx.close();
    await operatorCtx.close();
  });
});
