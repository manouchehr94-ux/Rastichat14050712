import { test, expect, Browser, Page } from '@playwright/test';

// Proof of reuse: an unrelated host (examples/reference-host) drives RastiChat through the public contract only.
//   host provisions tenants -> widget config -> identity bootstrap -> small launcher -> optional pre-chat ->
//   customer message -> operator inbox (staff SSO, no second login) -> operator reply -> customer realtime reply.
const HOST = process.env.HOST_URL || 'http://localhost:4000';
// where the operator dashboard lives: localhost:3000 on the local stack, operator-stg.example.test on the sandbox staging stack
const DASH = (process.env.OPERATOR_HOST || 'localhost:3000').replace(/\./g, '\\.');
const uniq = (label: string) => `${label} ${Date.now()}-${Math.floor(Math.random() * 10000)}`;

async function operatorViaHostSso(browser: Browser, staffUser: string) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto(`${HOST}/login?as=${staffUser}`);
  await page.goto(`${HOST}/staff/chat`);            // host -> dashboard /sso#assertion=…  (no password typed anywhere)
  await page.waitForURL(new RegExp(`${DASH}/admin/?$`));
  expect(page.url()).not.toContain('assertion');    // the credential was removed from the address bar
  return { ctx, page };
}

async function openConversation(page: Page, text: string) {
  const row = page.getByText(text, { exact: true }).first();
  await expect(async () => { await page.reload(); await row.waitFor({ timeout: 4000 }); }).toPass({ timeout: 25000 });
  await row.click();
}

test.describe('reference host (not RastiSi)', () => {
  test('Mode A — icon-only launcher, no questions, signed-in customer, full round trip with staff SSO', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    await customer.goto(`${HOST}/login?as=alice`);
    await expect(customer.locator('#who')).toContainText('Alice');

    const launcher = customer.locator('#rasti-launcher');
    await expect(launcher).toBeVisible();
    await expect(launcher.locator('.rasti-l-label')).toHaveText('');              // icon only
    await expect(launcher).toHaveAttribute('role', 'button');
    // verified identity came from the host's assertion, not from a guest session
    await expect.poll(() => customer.evaluate(() => localStorage.getItem('rasti_session_sub'))).toBe('alice');

    await launcher.click();
    await expect(customer.locator('#rasti-panel.open')).toBeVisible();
    await expect(customer.locator('#rasti-prechat form')).toHaveCount(0);          // no questions
    const outbound = uniq('I have a question about my order');
    await customer.locator('#rasti-input').fill(outbound);
    await customer.locator('#rasti-send').click();
    await expect(customer.locator('.rasti-msg.visitor .rasti-bubble', { hasText: outbound })).toBeVisible();

    const { ctx: opCtx, page: operator } = await operatorViaHostSso(browser, 'bob');
    await openConversation(operator, outbound);
    await expect(operator.getByText('✓ هویت تأییدشده').first()).toBeVisible();     // verified-identity badge
    const reply = uniq('Happy to help');
    await operator.locator('input[placeholder="پاسخ به مشتری…"]').fill(reply);
    await operator.locator('button:has-text("➤")').click();
    await expect(customer.locator('.rasti-msg.operator .rasti-bubble', { hasText: reply })).toBeVisible({ timeout: 15000 });

    // reload: history + identity resume without any login
    await customer.reload();
    await customer.locator('#rasti-launcher').click();
    await expect(customer.locator('.rasti-bubble', { hasText: reply })).toBeVisible({ timeout: 15000 });
    await customerCtx.close();
    await opCtx.close();
  });

  test('Mode B — one question as a guest; the answer reaches the operator; nothing is created before the first message', async ({ browser }) => {
    const ctx = await browser.newContext();
    const guest = await ctx.newPage();
    await guest.goto(`${HOST}/o/org-b`);
    const launcher = guest.locator('#rasti-launcher');
    await expect(launcher.locator('.rasti-l-label')).toHaveText('Ask us');         // icon + text
    await launcher.click();
    const form = guest.locator('#rasti-prechat form');
    await expect(form).toBeVisible();
    await expect(form.locator('.rasti-field')).toHaveCount(1);
    await form.locator('.rasti-submit').click();                                   // required question
    await expect(guest.locator('#rasti-pc-help-err')).not.toBeEmpty();
    const answer = uniq('Where is my certificate');
    await guest.locator('#rasti-pc-help').fill(answer);
    await form.locator('.rasti-submit').click();
    await expect(guest.locator('#rasti-input')).toBeVisible();
    const message = uniq('Hello from a guest');
    await guest.locator('#rasti-input').fill(message);
    await guest.locator('#rasti-send').click();
    await expect(guest.locator('.rasti-msg.visitor .rasti-bubble', { hasText: message })).toBeVisible();

    const { ctx: opCtx, page: operator } = await operatorViaHostSso(browser, 'erin');
    await openConversation(operator, message);
    await expect(operator.getByTestId('pre-chat-answers').first()).toContainText(answer);
    await expect(operator.getByText('✓ هویت تأییدشده')).toHaveCount(0);            // a guest is never shown as verified
    await ctx.close();
    await opCtx.close();
  });

  test('Mode C — structured form with select + consent; server validation; tenant isolation of the operator side', async ({ browser }) => {
    const ctx = await browser.newContext();
    const guest = await ctx.newPage();
    await guest.goto(`${HOST}/o/org-c`);
    await expect(guest.locator('#rasti-launcher')).toBeVisible();
    await guest.locator('#rasti-launcher').click();
    const form = guest.locator('#rasti-prechat form');
    await expect(form.locator('h2')).toHaveText('Before we start');
    await form.locator('.rasti-submit').click();
    await expect(guest.locator('#rasti-pc-topic')).toHaveAttribute('aria-invalid', 'true');
    await guest.locator('#rasti-pc-topic').selectOption('billing');
    await guest.locator('#rasti-pc-agree').check();
    await form.locator('.rasti-submit').click();
    const message = uniq('Billing question');
    await guest.locator('#rasti-input').fill(message);
    await guest.locator('#rasti-send').click();
    await expect(guest.locator('.rasti-msg.visitor .rasti-bubble', { hasText: message })).toBeVisible();

    // erin staffs org-b only: org-c's conversation must never appear for her
    const { ctx: opCtx, page: operator } = await operatorViaHostSso(browser, 'erin');
    await operator.waitForTimeout(1500);
    await operator.reload();
    await expect(operator.getByText(message, { exact: true })).toHaveCount(0);
    await ctx.close();
    await opCtx.close();
  });

  test('a signed-out host page is a guest; logging out of the host does not leak the previous customer session', async ({ browser }) => {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto(`${HOST}/login?as=alice`);
    await expect.poll(() => page.evaluate(() => localStorage.getItem('rasti_session_sub'))).toBe('alice');
    const verifiedToken = await page.evaluate(() => localStorage.getItem('rasti_session'));
    await page.goto(`${HOST}/logout`);
    await expect(page.locator('#who')).toContainText('guest');
    await expect.poll(() => page.evaluate(() => localStorage.getItem('rasti_session_sub'))).toBeNull();
    expect(await page.evaluate(() => localStorage.getItem('rasti_session'))).not.toBe(verifiedToken);
    await ctx.close();
  });

  test('@mobile launcher and form are usable on a phone viewport', async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 393, height: 851 }, isMobile: true, hasTouch: true });
    const page = await ctx.newPage();
    await page.goto(`${HOST}/o/org-b`);
    await page.locator('#rasti-launcher').tap();
    const panel = page.locator('#rasti-panel');
    await expect(panel).toBeVisible();
    const box = await panel.boundingBox();
    expect(box!.width).toBeGreaterThanOrEqual(390);                                 // full-screen on mobile
    await expect(page.locator('#rasti-prechat form')).toBeVisible();
    await ctx.close();
  });
});
