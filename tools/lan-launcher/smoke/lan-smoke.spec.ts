import { test, expect, ConsoleMessage } from '@playwright/test';

// RastiChat-LAN-Manager.ps1's `smoke` command sets these to the real LAN
// URLs (never localhost) before invoking `npx playwright test` here — see
// Invoke-CmdSmoke. Running this file by hand without them is a user error,
// not something the test should quietly work around.
const WIDGET_URL = process.env.LAN_WIDGET_URL;
const OPERATOR_URL = process.env.LAN_OPERATOR_URL;
const OPERATOR_EMAIL = process.env.LAN_OPERATOR_EMAIL || 'operator@ws.com';
const OPERATOR_PASSWORD = process.env.LAN_OPERATOR_PASSWORD || 'pass1234';

if (!WIDGET_URL || !OPERATOR_URL) {
  throw new Error(
    'LAN_WIDGET_URL and LAN_OPERATOR_URL must be set. This spec is meant to be run via ' +
    '"RastiChat-LAN-Manager.ps1 smoke" (or Start-RastiChat-LAN.bat), not invoked directly.',
  );
}

function marker(label: string) {
  return `${label} ${Date.now()}-${Math.floor(Math.random() * 10000)}`;
}

function isCorsNoise(text: string) {
  return /cors|cross-origin request blocked|access-control-allow-origin/i.test(text);
}

test.describe('RastiChat LAN smoke', () => {
  test('widget <-> operator works over the real LAN IP, both directions, and survives a refresh', async ({ browser }) => {
    const corsErrors: string[] = [];
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    for (const page of [customer, operator]) {
      page.on('console', (msg: ConsoleMessage) => {
        if (msg.type() === 'error' && isCorsNoise(msg.text())) corsErrors.push(msg.text());
      });
      page.on('pageerror', (err) => {
        if (isCorsNoise(err.message)) corsErrors.push(err.message);
      });
    }

    // --- Widget loads over the LAN URL, with no blank page/stuck spinner ---
    const widgetResponse = await customer.goto(WIDGET_URL!, { waitUntil: 'load' });
    expect(widgetResponse?.status(), 'widget page must return HTTP 200 over the LAN URL').toBe(200);

    await customer.locator('#rasti-launcher').click();
    await customer.locator('#rasti-panel.open').waitFor({ timeout: 15000 });
    await expect(customer.locator('#rasti-panel')).toBeVisible();
    await expect(customer.locator('#rasti-input')).toBeVisible();

    const hasInit = await customer.evaluate(() => typeof (window as unknown as { RastiChat?: unknown }).RastiChat !== 'undefined');
    expect(hasInit, 'window.RastiChat must be initialized on the widget page').toBe(true);

    // --- Customer sends a message (Persian text) -> creates a real conversation ---
    const outboundMarker = marker('سلام از تست LAN');
    await customer.locator('#rasti-input').fill(outboundMarker);
    await customer.locator('#rasti-send').click();
    await expect(customer.locator('.rasti-msg.visitor .rasti-bubble', { hasText: outboundMarker })).toBeVisible({ timeout: 15000 });

    // --- Operator logs in over the LAN URL and finds the conversation ---
    const operatorBase = OPERATOR_URL!.replace(/\/login\/?$/, '');
    await operator.goto(`${operatorBase}/login`);
    await operator.locator('input[type="email"]').fill(OPERATOR_EMAIL);
    await operator.locator('input[type="password"]').fill(OPERATOR_PASSWORD);
    await operator.getByRole('button', { name: 'ورود' }).click();
    await operator.waitForURL(new RegExp(`${operatorBase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/?$`), { timeout: 15000 });

    await operator.reload();
    const row = operator.getByText(outboundMarker, { exact: true }).first();
    await row.waitFor({ timeout: 20000 });
    await row.click();
    await expect(operator.getByText(outboundMarker).last()).toBeVisible();

    // --- Operator replies; delivery to the customer happens live (websocket over the LAN IP) ---
    const replyMarker = marker('پاسخ تست LAN');
    await operator.locator('input[placeholder="پاسخ به مشتری…"]').fill(replyMarker);
    await operator.locator('button:has-text("➤")').click();
    await expect(operator.getByText(replyMarker).last()).toBeVisible();
    await expect(customer.locator('.rasti-msg.operator .rasti-bubble', { hasText: replyMarker })).toBeVisible({ timeout: 15000 });

    // --- Refresh preserves conversation history ---
    await customer.reload();
    await customer.locator('#rasti-launcher').click();
    await customer.locator('#rasti-panel.open').waitFor({ timeout: 15000 });
    await expect(customer.locator('.rasti-bubble', { hasText: outboundMarker })).toBeVisible({ timeout: 15000 });
    await expect(customer.locator('.rasti-bubble', { hasText: replyMarker })).toBeVisible({ timeout: 15000 });

    // --- No CORS failures were observed anywhere in the flow above ---
    expect(corsErrors, `CORS-related console/page errors over the LAN URLs:\n${corsErrors.join('\n')}`).toHaveLength(0);

    await customerCtx.close();
    await operatorCtx.close();
  });
});
