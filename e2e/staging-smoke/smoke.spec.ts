import { test, expect } from '@playwright/test';
import path from 'path';
import { mkdirSync, writeFileSync } from 'fs';
import {
  BACKEND_URL, OPERATOR_URL, PLATFORM_URL, WIDGET_URL, WS_URL, PROJECT_KEY,
  OWNER_EMAIL, OWNER_PASSWORD, OPERATOR_EMAIL, OPERATOR_PASSWORD, MONITORING_TOKEN,
} from './env';
import { TINY_PNG_BASE64 } from '../tests/helpers';

/** Staging/production smoke suite — run against a REAL deployed
 * environment over HTTPS/WSS (see env.ts; every URL is required from the
 * environment, there is no localhost anywhere in this file). Covers
 * scenarios 1-18 of the Phase 6 staging smoke checklist; scenarios 19
 * ("Redis restart/reconnect") and 20 ("container restart persistence")
 * require restarting server-side infrastructure a browser can't reach —
 * they're documented as manual/ops steps in
 * docs/testing/STAGING_MANUAL_QA.md instead of faked here.
 *
 * Prerequisite: `scripts/staging/deploy.sh` has run successfully, and
 * `common/management/commands/seed_staging_data.py --yes` has been run
 * once on that environment (its one-time credentials output is what
 * SMOKE_OWNER_PASSWORD/SMOKE_OPERATOR_PASSWORD should be set to).
 */

function uniqueText(label: string) {
  return `${label} ${Date.now()}-${Math.floor(Math.random() * 10000)}`;
}

function embedUrl() {
  const p = new URLSearchParams({ widgetUrl: WIDGET_URL, projectKey: PROJECT_KEY, apiBase: `${BACKEND_URL}/api/v1`, wsBase: WS_URL });
  const filePath = path.resolve(__dirname, 'embed.html');
  return `file://${filePath}?${p.toString()}`;
}

async function openWidget(page: import('@playwright/test').Page) {
  await page.goto(embedUrl());
  await page.locator('#rasti-launcher').click();
  await page.locator('#rasti-panel.open').waitFor();
}

async function loginOperator(page: import('@playwright/test').Page, email: string, password: string) {
  await page.goto(`${OPERATOR_URL}/login`);
  await page.locator('input[type="email"]').fill(email);
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole('button', { name: 'ورود' }).click();
  await page.waitForURL(`${OPERATOR_URL}/`);
}

test.describe('Staging smoke', () => {
  test.describe.configure({ timeout: 60000 });

  // Scenarios 1-2: HTTPS Widget loads and initializes.
  test('Widget loads over HTTPS and initializes', async ({ page }) => {
    await page.goto(embedUrl());
    await expect(page.locator('#rasti-launcher')).toBeVisible({ timeout: 15000 });
    const hasApi = await page.evaluate(() => typeof (window as unknown as { RastiChat?: unknown }).RastiChat === 'object');
    expect(hasApi).toBe(true);
  });

  // Scenarios 3-9: customer starts a conversation and sends a message,
  // operator logs in, sees it, replies, customer receives the reply over
  // WSS, and a reload preserves history.
  test('customer <-> operator round trip over HTTPS/WSS, survives a reload', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('STAGING-SMOKE سلام از استیجینگ');
    await openWidget(customer);
    await customer.locator('#rasti-input').fill(marker);
    await customer.locator('#rasti-send').click();
    await expect(customer.locator('.rasti-msg.visitor .rasti-bubble', { hasText: marker })).toBeVisible();

    await loginOperator(operator, OPERATOR_EMAIL, OPERATOR_PASSWORD);
    await expect(operator.getByText(marker).last()).toBeVisible({ timeout: 15000 });

    const reply = uniqueText('پاسخ استیجینگ');
    await operator.locator('input[placeholder="پاسخ به مشتری…"]').fill(reply);
    await operator.locator('button:has-text("➤")').click();
    await expect(operator.getByText(reply).last()).toBeVisible();
    await expect(customer.locator('.rasti-msg.operator .rasti-bubble', { hasText: reply })).toBeVisible({ timeout: 15000 });

    await customer.reload();
    await customer.locator('#rasti-launcher').click();
    await expect(customer.locator('.rasti-bubble', { hasText: marker })).toBeVisible({ timeout: 15000 });
    await expect(customer.locator('.rasti-bubble', { hasText: reply })).toBeVisible();

    await customerCtx.close();
    await operatorCtx.close();
  });

  // Scenario 10: image upload.
  test('customer sends an image, operator sees it', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('STAGING-SMOKE تصویر');
    await openWidget(customer);
    await customer.locator('#rasti-input').fill(marker);
    await customer.locator('#rasti-send').click();

    await loginOperator(operator, OPERATOR_EMAIL, OPERATOR_PASSWORD);
    await expect(operator.getByText(marker).last()).toBeVisible({ timeout: 15000 });

    const tmpDir = path.resolve(__dirname, 'tmp');
    mkdirSync(tmpDir, { recursive: true });
    const pngPath = path.join(tmpDir, 'tiny.png');
    writeFileSync(pngPath, Buffer.from(TINY_PNG_BASE64, 'base64'));
    await customer.locator('#rasti-file').setInputFiles(pngPath);
    await expect(customer.locator('.rasti-bubble.rasti-img img')).toBeVisible({ timeout: 15000 });
    await expect(operator.locator('img[alt=""]')).toBeVisible({ timeout: 15000 });

    await customerCtx.close();
    await operatorCtx.close();
  });

  // Scenario 11: voice upload (chromium launched with a fake media device
  // — see playwright.config.ts launchOptions).
  test('customer sends a voice message, operator hears/sees it', async ({ browser }) => {
    const customerCtx = await browser.newContext({ permissions: ['microphone'] });
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('STAGING-SMOKE صوتی');
    await openWidget(customer);
    await customer.locator('#rasti-input').fill(marker);
    await customer.locator('#rasti-send').click();

    await loginOperator(operator, OPERATOR_EMAIL, OPERATOR_PASSWORD);
    await expect(operator.getByText(marker).last()).toBeVisible({ timeout: 15000 });

    await customer.locator('#rasti-mic-btn').click();
    await customer.waitForTimeout(1200);
    await customer.locator('#rasti-mic-btn').click();
    await expect(customer.locator('.rasti-bubble.rasti-voice')).toBeVisible({ timeout: 15000 });
    await expect(operator.locator('audio[src]')).toHaveCount(1, { timeout: 15000 });

    await customerCtx.close();
    await operatorCtx.close();
  });

  // Scenario 12: Knowledge Base article card.
  test('operator publishes a KB article and shares it as a card; customer receives it', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const adminCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const admin = await adminCtx.newPage();

    const title = uniqueText('STAGING-SMOKE مقاله');
    await loginOperator(admin, OWNER_EMAIL, OWNER_PASSWORD);
    await admin.goto(`${OPERATOR_URL}/knowledge-base`);
    await admin.getByText('+ مقاله جدید').click();
    await admin.locator('input').first().fill(title);
    await admin.getByText('ذخیره').click();
    await expect(admin.getByText(title)).toBeVisible({ timeout: 15000 });
    const row = admin.locator('tr', { hasText: title });
    await row.getByText('انتشار').click();

    const marker = uniqueText('STAGING-SMOKE شروع برای مقاله');
    await openWidget(customer);
    await customer.locator('#rasti-input').fill(marker);
    await customer.locator('#rasti-send').click();
    await admin.reload();
    await admin.goto(`${OPERATOR_URL}/`);
    await expect(admin.getByText(marker).last()).toBeVisible({ timeout: 15000 });

    await admin.getByTitle('جستجوی پایگاه دانش').click();
    await admin.getByPlaceholder('جستجو در عنوان، خلاصه و متن…').fill(title);
    await admin.getByText('ارسال کارت مقاله').click();
    await expect(customer.locator('.rasti-bubble', { hasText: title })).toBeVisible({ timeout: 15000 });

    await customerCtx.close();
    await adminCtx.close();
  });

  // Scenario 13: Macro execution (activates the seed_staging_data macro,
  // never assumes it's already active).
  test('admin activates the seeded macro, operator runs it', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const adminCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const admin = await adminCtx.newPage();

    await loginOperator(admin, OWNER_EMAIL, OWNER_PASSWORD);
    await admin.goto(`${OPERATOR_URL}/macros`);
    const row = admin.locator('tr', { hasText: 'STAGING — پاسخ نمونه' });
    const statusBtn = row.locator('button', { hasText: /^(فعال|غیرفعال)$/ });
    if ((await statusBtn.innerText()) === 'غیرفعال') {
      await statusBtn.click();
      await expect(row.locator('button', { hasText: 'فعال' })).toBeVisible({ timeout: 10000 });
    }

    const marker = uniqueText('STAGING-SMOKE ماکرو');
    await openWidget(customer);
    await customer.locator('#rasti-input').fill(marker);
    await customer.locator('#rasti-send').click();

    await admin.goto(`${OPERATOR_URL}/`);
    await expect(admin.getByText(marker).last()).toBeVisible({ timeout: 15000 });
    await admin.getByTitle('اجرای ماکرو').click();
    await admin.getByText('STAGING — پاسخ نمونه').click();
    await admin.getByText('تأیید و اجرا').click();
    await expect(customer.locator('.rasti-bubble', { hasText: 'پاسخ نمونه محیط آزمایشی' })).toBeVisible({ timeout: 15000 });

    await customerCtx.close();
    await adminCtx.close();
  });

  // Scenario 14: automation execution (activates the seeded rule).
  test('admin activates the seeded automation rule; it fires on a new conversation', async ({ browser }) => {
    const adminCtx = await browser.newContext();
    const admin = await adminCtx.newPage();
    await loginOperator(admin, OWNER_EMAIL, OWNER_PASSWORD);
    await admin.goto(`${OPERATOR_URL}/automations`);
    const row = admin.locator('tr', { hasText: 'STAGING — تعیین اولویت بالا' });
    const toggle = row.getByText(/^(فعال‌سازی|غیرفعال‌سازی)$/);
    if ((await toggle.innerText()) === 'فعال‌سازی') {
      await toggle.click();
      await expect(row.getByText('فعال', { exact: true })).toBeVisible({ timeout: 10000 });
    }

    const customerCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const marker = uniqueText('STAGING-SMOKE اتوماسیون');
    await openWidget(customer);
    await customer.locator('#rasti-input').fill(marker);
    await customer.locator('#rasti-send').click();

    await admin.goto(`${OPERATOR_URL}/`);
    const convRow = admin.locator('*', { hasText: marker }).last();
    await expect(convRow).toBeVisible({ timeout: 15000 });
    await convRow.click();
    // The seeded rule sets priority to HIGH ('بالا'), not URGENT ('فوری').
    await expect(admin.getByText('بالا', { exact: true })).toBeVisible({ timeout: 15000 });

    await customerCtx.close();
    await adminCtx.close();
  });

  // Scenario 15: internal notes never reach the customer.
  test('an internal note is visible to the operator but never reaches the customer', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('STAGING-SMOKE یادداشت داخلی');
    const noteText = uniqueText('STAGING-SMOKE یادداشت محرمانه');
    await openWidget(customer);
    await customer.locator('#rasti-input').fill(marker);
    await customer.locator('#rasti-send').click();

    await loginOperator(operator, OPERATOR_EMAIL, OPERATOR_PASSWORD);
    await expect(operator.getByText(marker).last()).toBeVisible({ timeout: 15000 });
    await operator.getByText('💬 پاسخ به مشتری (کلیک برای یادداشت داخلی)').click();
    await operator.getByPlaceholder('یادداشت داخلی (فقط برای همکاران)…').fill(noteText);
    await operator.locator('button:has-text("➤")').click();
    await expect(operator.getByText('🔒 یادداشت داخلی')).toBeVisible({ timeout: 15000 });

    await customer.waitForTimeout(3000);
    await expect(customer.getByText(noteText)).toHaveCount(0);

    await customerCtx.close();
    await operatorCtx.close();
  });

  // Scenario 16: cross-workspace/cross-tenant access rejected — tested at
  // the public API boundary, which needs no operator UI/second workspace
  // account to exercise on a real staging environment.
  test('a bogus project_key is rejected, not silently served as this workspace', async ({ request }) => {
    const res = await request.get(`${BACKEND_URL}/api/v1/kb/public/articles/?project_key=00000000-0000-0000-0000-000000000000`);
    expect([400, 404]).toContain(res.status());
  });

  // Scenario 17: Platform Dashboard loads.
  test('Platform Dashboard loads over HTTPS', async ({ page }) => {
    await page.goto(`${PLATFORM_URL}/login`);
    await expect(page.locator('input[type="email"]')).toBeVisible({ timeout: 15000 });
  });

  // Scenario 18: health endpoints report ready.
  test('health endpoints report ready over HTTPS', async ({ request }) => {
    for (const path of ['health/live', 'health/ready', 'health/monitoring']) {
      const res = await request.get(`${BACKEND_URL}/api/v1/${path}/`);
      // health/monitoring requires MONITORING_TOKEN (see
      // common.views.MonitoringView) — an anonymous 401 here is expected
      // and correct, not a failure; only a real 5xx is.
      expect(res.status(), `GET /api/v1/${path}/`).toBeLessThan(500);
    }
    const ready = await request.get(`${BACKEND_URL}/api/v1/health/ready/`);
    const body = await ready.json();
    expect(body.status).toBe('ready');
    expect(body.components.database.up).toBe(true);
    expect(body.components.redis.up).toBe(true);
  });

  // Scenario 18b: monitoring endpoint auth — anonymous is rejected,
  // token-bearing is accepted. Only runs the positive half if
  // SMOKE_MONITORING_TOKEN was provided (optional — see env.ts).
  test('health/monitoring requires the monitoring token', async ({ request }) => {
    const anon = await request.get(`${BACKEND_URL}/api/v1/health/monitoring/`);
    expect(anon.status()).toBe(401);

    if (MONITORING_TOKEN) {
      const authed = await request.get(`${BACKEND_URL}/api/v1/health/monitoring/`, {
        headers: { 'X-Monitoring-Token': MONITORING_TOKEN },
      });
      expect(authed.status()).toBe(200);
      const body = await authed.json();
      expect(body).toHaveProperty('schedulers');
      expect(body).toHaveProperty('disk');
      expect(body).toHaveProperty('backup');
    }
  });
});
