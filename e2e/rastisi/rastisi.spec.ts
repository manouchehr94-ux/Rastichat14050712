import { test, expect, Browser, BrowserContext, Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

// Cross-system proof that RastiSi (first host adapter) drives RastiChat only through the generic Integration Contract:
//   platform enables chat per store -> customer launcher -> merchant inbox via host SSO (no second login)
//   -> merchant<->platform support -> platform-owner-initiated message to a store; isolation / gating / revocation.
const seed = JSON.parse(readFileSync(process.env.SEED_FILE!, 'utf8'));
const S = seed.stores as Record<string, { public_id: string; name: string; storefront: string; admin: string }>;
const uniq = (label: string) => `${label} ${Date.now()}-${Math.floor(Math.random() * 10000)}`;

async function loggedIn(browser: Browser, username: string, origin: string): Promise<{ ctx: BrowserContext; page: Page }> {
  const ctx = await browser.newContext();
  await ctx.addCookies(Object.entries(seed.sessions[username] as Record<string, string>)
    .map(([name, value]) => ({ name, value, url: origin })));
  return { ctx, page: await ctx.newPage() };
}

function siShell(code: string) {
  execFileSync(process.env.SI_PY!, ['manage.py', 'shell', '-c', code], { cwd: process.env.SI_DIR!, env: process.env, stdio: 'pipe' });
}

async function openInbox(page: Page, text: string) {
  const row = page.getByText(text, { exact: true }).first();
  await expect(async () => { await page.reload(); await row.waitFor({ timeout: 4000 }); }).toPass({ timeout: 30000 });
  await row.click();
}

async function enableViaPlatformUi(browser: Browser, slug: string) {
  const { ctx, page } = await loggedIn(browser, 'platform_root', seed.platform);
  await page.goto(`${seed.platform}/stores/${S[slug].public_id}/?tab=chat`);
  await page.getByRole('button', { name: 'فعال‌سازی گفتگو برای این فروشگاه' }).click();
  await expect(page.getByRole('button', { name: 'غیرفعال‌سازی برای این فروشگاه' }).first()).toBeVisible();
  await ctx.close();
}

test.describe.serial('RastiSi x RastiChat', () => {
  test('default OFF: no store shows any chat entry point before the platform enables it', async ({ browser }) => {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto(S.sa.storefront + '/');
    await page.waitForLoadState('load');
    await expect(page.locator('#rasti-launcher')).toHaveCount(0);
    expect(await page.content()).not.toContain('widget.iife.js');
    await ctx.close();
    const { ctx: m, page: merchant } = await loggedIn(browser, 'owner1', S.sa.admin);
    const r = await merchant.goto(`${S.sa.admin}/admin-portal/chat/customers/`);
    expect(r!.status()).toBe(404);
    await m.close();
  });

  test('platform owner enables chat for pilot stores A and B only (C stays off)', async ({ browser }) => {
    await enableViaPlatformUi(browser, 'sa');
    await enableViaPlatformUi(browser, 'sb');
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto(S.sc.storefront + '/');
    await expect(page.locator('#rasti-launcher')).toHaveCount(0);
    await ctx.close();
  });

  test('path 1 — guest customer: small icon, message reaches the store admin (SSO, no second login), reply is live', async ({ browser }) => {
    const guestCtx = await browser.newContext();
    const guest = await guestCtx.newPage();
    await guest.goto(S.sa.storefront + '/');
    const launcher = guest.locator('#rasti-launcher');
    await expect(launcher).toBeVisible({ timeout: 20000 });
    await expect(launcher.locator('.rasti-l-label')).toHaveText('');           // icon only
    await launcher.click();
    await expect(guest.locator('#rasti-prechat form')).toHaveCount(0);         // no questions by default
    const message = uniq('سلام، موجودی این کالا چقدر است؟');
    await guest.locator('#rasti-input').fill(message);
    await guest.locator('#rasti-send').click();
    await expect(guest.locator('.rasti-msg.visitor .rasti-bubble', { hasText: message })).toBeVisible();

    const { ctx: adminCtx, page: admin } = await loggedIn(browser, 'admin_a', S.sa.admin);
    await admin.goto(`${S.sa.admin}/admin-portal/chat/customers/`);
    await admin.waitForURL(/localhost:3000\/admin\/?$/);
    expect(admin.url()).not.toContain('assertion');                            // credential removed from the address bar
    await openInbox(admin, message);
    await expect(admin.getByText('✓ هویت تأییدشده')).toHaveCount(0);            // guest is never "verified"
    const reply = uniq('موجود است');
    await admin.locator('input[placeholder="پاسخ به مشتری…"]').fill(reply);
    await admin.locator('button:has-text("➤")').click();
    await expect(guest.locator('.rasti-msg.operator .rasti-bubble', { hasText: reply })).toBeVisible({ timeout: 15000 });
    await guestCtx.close();
    await adminCtx.close();
  });

  test('path 1 — signed-in customer is identified with no second login; store B never sees store A conversations', async ({ browser }) => {
    const { ctx, page } = await loggedIn(browser, 'cust1', S.sa.storefront);
    await page.goto(S.sa.storefront + '/');
    const launcher = page.locator('#rasti-launcher');
    await expect(launcher).toBeVisible({ timeout: 20000 });
    await launcher.click();
    const message = uniq('پیگیری سفارش من');
    await page.locator('#rasti-input').fill(message);
    await page.locator('#rasti-send').click();
    await expect(page.locator('.rasti-msg.visitor .rasti-bubble', { hasText: message })).toBeVisible();

    const a = await loggedIn(browser, 'owner1', S.sa.admin);
    await a.page.goto(`${S.sa.admin}/admin-portal/chat/customers/`);
    await a.page.waitForURL(/localhost:3000\/admin\/?$/);
    await openInbox(a.page, message);
    await expect(a.page.getByText('✓ هویت تأییدشده').first()).toBeVisible();   // verified by the host's assertion

    // the same multi-store owner entering through store B's admin host gets store B's inbox: nothing from A
    const b = await loggedIn(browser, 'owner1', S.sb.admin);
    await b.page.goto(`${S.sb.admin}/admin-portal/chat/customers/`);
    await b.page.waitForURL(/localhost:3000\/admin\/?$/);
    await b.page.waitForLoadState('networkidle');
    await expect(b.page.getByText(message, { exact: true })).toHaveCount(0);
    await a.ctx.close(); await b.ctx.close(); await ctx.close();
  });

  test('isolation — a member of another store, and an order manager on support, are refused', async ({ browser }) => {
    const other = await loggedIn(browser, 'owner_b', S.sa.admin);                // owner of B has no membership on A
    const r = await other.page.goto(`${S.sa.admin}/admin-portal/chat/customers/`);
    expect(r!.url()).not.toContain('localhost:3000');
    await other.ctx.close();

    const mgr = await loggedIn(browser, 'manager_a', S.sa.admin);                // order manager: customer chat yes, platform support no
    await mgr.page.goto(`${S.sa.admin}/admin-portal/chat/customers/`);
    await mgr.page.waitForURL(/localhost:3000\/admin\/?$/);
    const r2 = await mgr.page.goto(`${S.sa.admin}/admin-portal/chat/support/`);
    expect(r2!.status()).toBe(403);
    await mgr.ctx.close();
  });

  test('path 2 — merchant opens a support thread to the platform team; platform owner answers in the platform inbox', async ({ browser }) => {
    const merchant = await loggedIn(browser, 'owner1', S.sa.admin);
    await merchant.page.goto(`${S.sa.admin}/admin-portal/chat/support/`);
    await merchant.page.waitForURL(/localhost:3000\/admin\/support\/?$/);
    const subject = uniq('مشکل پرداخت');
    const body = uniq('درگاه پرداخت خطا می‌دهد');
    await merchant.page.getByRole('button', { name: 'تیکت جدید' }).click();
    await merchant.page.getByPlaceholder('موضوع').fill(subject);
    await merchant.page.getByPlaceholder('پیام اولیه').fill(body);
    await merchant.page.getByRole('button', { name: 'ارسال درخواست' }).click();
    await expect(merchant.page.getByText(body).first()).toBeVisible({ timeout: 15000 });
    await merchant.page.getByText(subject).first().click();                         // open the thread: live updates
    await expect(merchant.page.getByPlaceholder('پاسخ...')).toBeVisible();

    const root = await loggedIn(browser, 'platform_root', seed.platform);
    await root.page.goto(`${seed.platform}/chat/inbox/`);
    await root.page.waitForURL(/localhost:3001\/platform\/inbox\/?$/);
    expect(root.page.url()).not.toContain('assertion');
    await expect(async () => { await root.page.reload(); await root.page.getByText(S.sa.name).first().waitFor({ timeout: 4000 }); }).toPass({ timeout: 30000 });
    await root.page.getByText(S.sa.name).first().click();
    await expect(root.page.getByText(body).first()).toBeVisible({ timeout: 15000 });
    const answer = uniq('در حال بررسی هستیم');
    const replyBox = root.page.getByPlaceholder('پاسخ...');
    await replyBox.fill(answer);
    await replyBox.press('Enter');
    await expect(merchant.page.getByText(answer).first()).toBeVisible({ timeout: 20000 });
    await merchant.ctx.close(); await root.ctx.close();
  });

  test('path 3 — platform owner starts a conversation with a store that never wrote first', async ({ browser }) => {
    const root = await loggedIn(browser, 'platform_root', seed.platform);
    await root.page.goto(`${seed.platform}/stores/${S.sb.public_id}/chat/message/`);
    const subject = uniq('اطلاعیه');
    const text = uniq('لطفاً اطلاعات تماس را تکمیل کنید');
    await root.page.locator('#chat-subject').fill(subject);
    await root.page.locator('#chat-body').fill(text);
    await root.page.getByRole('button', { name: 'ارسال' }).click();
    await root.page.waitForLoadState('load');

    const owner = await loggedIn(browser, 'owner_b', S.sb.admin);
    await owner.page.goto(`${S.sb.admin}/admin-portal/chat/support/`);
    await owner.page.waitForURL(/localhost:3000\/admin\/support\/?$/);
    await expect(async () => { await owner.page.reload(); await owner.page.getByText(subject).first().waitFor({ timeout: 4000 }); }).toPass({ timeout: 30000 });
    await owner.page.getByText(subject).first().click();
    await expect(owner.page.getByText(text).first()).toBeVisible({ timeout: 15000 });
    // store A's merchant must not see the message sent to B
    const a = await loggedIn(browser, 'admin_a', S.sa.admin);
    await a.page.goto(`${S.sa.admin}/admin-portal/chat/support/`);
    await a.page.waitForURL(/localhost:3000\/admin\/support\/?$/);
    await a.page.waitForLoadState('networkidle');
    await expect(a.page.getByText(subject)).toHaveCount(0);
    await root.ctx.close(); await owner.ctx.close(); await a.ctx.close();
  });

  test('revocation — removing a membership in RastiSi removes chat access; disabling a store removes the launcher', async ({ browser }) => {
    siShell(`
from apps.stores.models import StoreMembership
from apps.stores.services import membership_service
m = StoreMembership.objects.get(user__username="manager_a")
membership_service.revoke_membership(m, actor=None)
`);
    const mgr = await loggedIn(browser, 'manager_a', S.sa.admin);
    const r = await mgr.page.goto(`${S.sa.admin}/admin-portal/chat/customers/`);
    expect(r!.url()).not.toContain('localhost:3000/admin');
    await mgr.ctx.close();

    const { ctx, page } = await loggedIn(browser, 'platform_root', seed.platform);
    await page.goto(`${seed.platform}/stores/${S.sa.public_id}/?tab=chat`);
    await page.getByRole('button', { name: 'غیرفعال‌سازی برای این فروشگاه' }).first().click();
    await expect(page.getByRole('button', { name: 'فعال‌سازی گفتگو برای این فروشگاه' })).toBeVisible();
    await ctx.close();
    const c2 = await browser.newContext();
    const sf = await c2.newPage();
    await sf.goto(S.sa.storefront + '/');
    await expect(sf.locator('#rasti-launcher')).toHaveCount(0);
    await c2.close();
  });
});
