import { test, expect } from '@playwright/test';
import { writeFileSync, mkdirSync } from 'fs';
import path from 'path';
import {
  openWidget, sendWidgetText, loginOperator, openConversationByPreview, fixture, TINY_PNG_BASE64,
} from './helpers';

function uniqueText(label: string) {
  return `${label} ${Date.now()}-${Math.floor(Math.random() * 10000)}`;
}

test.describe('Customer <-> operator core flows', () => {
  test('customer sends text, operator receives it and replies, customer receives the reply', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const outbound = uniqueText('سلام از مشتری');
    await openWidget(customer);
    await sendWidgetText(customer, outbound);
    await expect(customer.locator('.rasti-msg.visitor .rasti-bubble', { hasText: outbound })).toBeVisible();

    await loginOperator(operator);
    await openConversationByPreview(operator, outbound);
    await expect(operator.getByText(outbound).last()).toBeVisible();

    const reply = uniqueText('پاسخ اپراتور');
    await operator.locator('input[placeholder="پاسخ به مشتری…"]').fill(reply);
    await operator.locator('button:has-text("➤")').click();
    await expect(operator.getByText(reply).last()).toBeVisible();

    await expect(customer.locator('.rasti-msg.operator .rasti-bubble', { hasText: reply })).toBeVisible({ timeout: 10000 });

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('customer sends an image, operator sees it', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('پیام قبل عکس');
    await openWidget(customer);
    await sendWidgetText(customer, marker); // gives the operator list row a findable preview, before the preview changes to the image

    // Select the conversation while its preview still matches the marker text,
    // then send the image — this exercises the live websocket delivery path
    // rather than a post-hoc history reload.
    await loginOperator(operator);
    await openConversationByPreview(operator, marker);
    await expect(operator.getByText(marker).last()).toBeVisible();

    const tmpDir = path.resolve(__dirname, 'tmp');
    mkdirSync(tmpDir, { recursive: true });
    const pngPath = path.join(tmpDir, 'tiny.png');
    writeFileSync(pngPath, Buffer.from(TINY_PNG_BASE64, 'base64'));

    // setInputFiles sets the (hidden) file input directly via CDP and fires its
    // own change event — clicking the visible attach button first would additionally
    // trigger a native file-chooser interception, double-firing the upload.
    await customer.locator('#rasti-file').setInputFiles(pngPath);
    await expect(customer.locator('.rasti-bubble.rasti-img img')).toBeVisible({ timeout: 10000 });

    await expect(operator.locator('img[alt=""]')).toBeVisible({ timeout: 10000 });

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('operator shares a product, customer sees the correct card', async ({ browser }) => {
    test.skip(!fixture.productId, 'No seeded product available for this run');
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('شروع گفتگو محصول');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator);
    await openConversationByPreview(operator, marker);

    await operator.locator('button[title="معرفی محصول"]').click();
    const productButton = operator.locator('button', { hasText: 'شمع معطر وانیل و کهره' });
    await productButton.waitFor();
    await productButton.click();

    await expect(operator.getByText('شمع معطر وانیل و کهره').first()).toBeVisible();
    await expect(customer.locator('.rasti-bubble.rasti-product .rasti-p-name', { hasText: 'شمع معطر وانیل و کهره' }))
      .toBeVisible({ timeout: 10000 });
    await expect(customer.locator('.rasti-p-brand', { hasText: 'آرُم هوم' })).toBeVisible();

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('operator requests a rating, customer submits it, operator sees the result', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('شروع گفتگو امتیاز');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator);
    await openConversationByPreview(operator, marker);
    await operator.locator('button', { hasText: 'درخواست امتیاز' }).click();

    const ratingCard = customer.locator('.rasti-bubble.rasti-rating');
    await ratingCard.waitFor({ timeout: 10000 });
    await ratingCard.locator('button[data-r="5"]').click();
    await expect(ratingCard.locator('.rasti-r-thanks')).toBeVisible();

    await expect(operator.getByText(/مشتری امتیاز داد/)).toBeVisible({ timeout: 10000 });

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('refreshing the widget preserves conversation history', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const customer = await customerCtx.newPage();

    const marker = uniqueText('پیام قبل از رفرش');
    await openWidget(customer);
    await sendWidgetText(customer, marker);
    await expect(customer.locator('.rasti-bubble', { hasText: marker })).toBeVisible();

    await customer.reload();
    await customer.locator('#rasti-launcher').click();
    await expect(customer.locator('.rasti-bubble', { hasText: marker })).toBeVisible({ timeout: 10000 });

    await customerCtx.close();
  });

  test('reconnecting (page reload) does not duplicate existing messages', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const customer = await customerCtx.newPage();

    const marker = uniqueText('پیام یکتا');
    await openWidget(customer);
    await sendWidgetText(customer, marker);
    await expect(customer.locator('.rasti-bubble', { hasText: marker })).toBeVisible();

    await customer.reload();
    await customer.locator('#rasti-launcher').click();
    await customer.locator('.rasti-bubble', { hasText: marker }).first().waitFor({ timeout: 10000 });
    await expect(customer.locator('.rasti-bubble', { hasText: marker })).toHaveCount(1);

    await customerCtx.close();
  });

  test('customer A cannot see customer B\'s conversation', async ({ browser }) => {
    const ctxA = await browser.newContext();
    const ctxB = await browser.newContext();
    const customerA = await ctxA.newPage();
    const customerB = await ctxB.newPage();

    const textA = uniqueText('پیام مشتری آ');
    const textB = uniqueText('پیام مشتری ب');

    await openWidget(customerA);
    await sendWidgetText(customerA, textA);
    await expect(customerA.locator('.rasti-bubble', { hasText: textA })).toBeVisible();

    await openWidget(customerB);
    await sendWidgetText(customerB, textB);
    await expect(customerB.locator('.rasti-bubble', { hasText: textB })).toBeVisible();

    // Each anonymous session must get its own visitor/conversation — neither
    // customer's page should ever render the other's message.
    await expect(customerA.locator('.rasti-bubble', { hasText: textB })).toHaveCount(0);
    await expect(customerB.locator('.rasti-bubble', { hasText: textA })).toHaveCount(0);

    await ctxA.close();
    await ctxB.close();
  });

  test('customer sends a voice message, operator hears/sees it', async ({ browser }) => {
    const customerCtx = await browser.newContext({ permissions: ['microphone'] });
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('پیام قبل صدا');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator);
    await openConversationByPreview(operator, marker);
    await expect(operator.getByText(marker).last()).toBeVisible();

    await customer.locator('#rasti-mic-btn').click();
    await customer.waitForTimeout(1200); // must clear the 0.6s accidental-tap threshold
    await customer.locator('#rasti-mic-btn').click();

    await expect(customer.locator('.rasti-bubble.rasti-voice')).toBeVisible({ timeout: 10000 });
    // The <audio> element has no `controls` attribute (a custom play button drives it),
    // so it has a 0x0 box and never counts as Playwright-"visible" — assert it's attached
    // with a real src instead, and that the custom play control renders.
    await expect(operator.locator('audio[src]')).toHaveCount(1, { timeout: 10000 });
    await expect(operator.getByText('▶').last()).toBeVisible();

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('operator sends a voice message, customer hears/sees it', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext({ permissions: ['microphone'] });
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('شروع گفتگو صدای اپراتور');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator);
    await openConversationByPreview(operator, marker);

    await operator.locator('button[title="پیام صوتی"]').click();
    await operator.waitForTimeout(1200);
    await operator.locator('button[title="پیام صوتی"]').click();

    await expect(operator.locator('audio[src]')).toHaveCount(1, { timeout: 10000 });
    await expect(customer.locator('.rasti-bubble.rasti-voice')).toBeVisible({ timeout: 10000 });
    await expect(customer.locator('.rasti-vplay')).toBeVisible();

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('operator inbox: search filters the conversation list', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const markerA = uniqueText('جستجوپذیر آ');
    const markerB = uniqueText('جستجوپذیر ب');
    await openWidget(customer);
    await sendWidgetText(customer, markerA);

    const customer2Ctx = await browser.newContext();
    const customer2 = await customer2Ctx.newPage();
    await openWidget(customer2);
    await sendWidgetText(customer2, markerB);

    await loginOperator(operator);
    await operator.reload();
    await operator.getByText(markerA, { exact: true }).first().waitFor({ timeout: 15000 });
    await operator.getByText(markerB, { exact: true }).first().waitFor({ timeout: 15000 });

    await operator.locator('input[placeholder="جستجوی مشتری یا گفتگو…"]').fill(markerA);
    await expect(operator.getByText(markerA, { exact: true })).toBeVisible();
    await expect(operator.getByText(markerB, { exact: true })).toHaveCount(0);

    await customerCtx.close();
    await customer2Ctx.close();
    await operatorCtx.close();
  });

  test('operator can assign a conversation to themselves and reassign it to a teammate', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('شروع گفتگو واگذاری');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator);
    await openConversationByPreview(operator, marker);

    const teammateSelect = operator.locator('select[title="واگذاری به همکار"]');
    await expect(teammateSelect).toHaveValue('');

    await operator.locator('button', { hasText: 'واگذاری به من' }).click();
    await expect(teammateSelect.locator('option:checked')).toHaveText('operator');

    const otherOption = teammateSelect.locator('option').filter({ hasNotText: 'operator' }).filter({ hasNotText: 'واگذار نشده' });
    const otherValue = await otherOption.first().getAttribute('value');
    await teammateSelect.selectOption(otherValue!);
    await expect(teammateSelect.locator('option:checked')).not.toHaveText('operator');

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('operator can close and reopen a conversation', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('شروع گفتگو پایان');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator);
    await openConversationByPreview(operator, marker);

    await operator.locator('button', { hasText: 'پایان گفتگو' }).click();
    await expect(operator.getByText('بسته', { exact: true }).first()).toBeVisible();
    const reopenBtn = operator.locator('button', { hasText: 'بازگشایی' });
    await expect(reopenBtn).toBeVisible();

    await reopenBtn.click();
    await expect(operator.locator('button', { hasText: 'پایان گفتگو' })).toBeVisible();

    await customerCtx.close();
    await operatorCtx.close();
  });
});

test.describe('Mobile responsive flows', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('mobile widget: open and send a message', async ({ browser }) => {
    const customerCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const customer = await customerCtx.newPage();

    const marker = uniqueText('پیام موبایل');
    await openWidget(customer);
    await sendWidgetText(customer, marker);
    await expect(customer.locator('.rasti-msg.visitor .rasti-bubble', { hasText: marker })).toBeVisible();

    await customerCtx.close();
  });

  test('mobile operator: list-first navigation with a working back control', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('پیام موبایل اپراتور');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator);
    await operator.reload();

    // List-first: the chat pane placeholder must not be visible before selecting anything.
    await expect(operator.getByText('یک گفتگو را انتخاب کنید')).toBeHidden();

    const row = operator.getByText(marker, { exact: true }).first();
    await row.waitFor({ timeout: 15000 });
    await row.click();

    // After selecting, the chat header (with its mobile-only back arrow) must be visible.
    const backButton = operator.getByText('›');
    await expect(backButton).toBeVisible();
    await expect(operator.getByText(marker).last()).toBeVisible();

    await backButton.click();
    // Back in the list: the visitor row should be reachable/visible again.
    await expect(operator.getByText('گفتگوهای مشتریان')).toBeVisible();

    await customerCtx.close();
    await operatorCtx.close();
  });
});
