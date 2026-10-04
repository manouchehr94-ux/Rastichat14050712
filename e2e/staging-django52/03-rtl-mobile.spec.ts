import { test, expect } from '@playwright/test';
import { ALLOWED_EMBED_ORIGIN, FORBIDDEN_EMBED_ORIGIN, OPERATOR_URL, PLATFORM_URL } from './env';
import { openWidgetAt, sendWidgetText, serveEmbedAt, uniqueText } from './helpers';

const ORIGIN = ALLOWED_EMBED_ORIGIN || FORBIDDEN_EMBED_ORIGIN;

// Runs in every configured project (desktop + mobile emulation + optional WebKit/Firefox).
test.describe('Persian / RTL and responsive layout', () => {
  test('widget panel is RTL, fully inside the viewport, with no horizontal scroll, and usable', async ({ page, context }, info) => {
    await serveEmbedAt(context, ORIGIN);
    await openWidgetAt(page, ORIGIN);
    const dir = await page.locator('#rasti-container').evaluate((el) => getComputedStyle(el).direction);
    expect(dir).toBe('rtl');
    const vp = page.viewportSize()!;
    const box = (await page.locator('#rasti-panel').boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(-1);
    expect(box.x + box.width).toBeLessThanOrEqual(vp.width + 1);
    expect(box.y + box.height).toBeLessThanOrEqual(vp.height + 1);
    const overflow = await page.evaluate(() => document.scrollingElement!.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    const marker = uniqueText('DJ52-RTL ۱۲۳ سلام');
    await sendWidgetText(page, marker); // typing Persian + digits works and renders
    const input = (await page.locator('#rasti-input').boundingBox())!;
    expect(input.y + input.height).toBeLessThanOrEqual(vp.height + 1); // the composer is reachable (not under the fold / keyboard-less)
    await page.screenshot({ path: info.outputPath(`widget-${info.project.name}.png`), fullPage: false });
    await info.attach('widget', { path: info.outputPath(`widget-${info.project.name}.png`), contentType: 'image/png' });
  });

  for (const [name, url] of [['operator login', `${OPERATOR_URL}/login`], ['platform login', `${PLATFORM_URL}/login`]] as const) {
    test(`${name}: RTL document, no horizontal overflow, controls inside the viewport`, async ({ page }, info) => {
      await page.goto(url);
      await expect(page.locator('html')).toHaveAttribute('dir', 'rtl');
      await expect(page.locator('html')).toHaveAttribute('lang', 'fa');
      const overflow = await page.evaluate(() => document.scrollingElement!.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(1);
      const vp = page.viewportSize()!;
      for (const sel of ['input[type="email"]', 'input[type="password"]', 'button']) {
        const box = (await page.locator(sel).first().boundingBox())!;
        expect(box.x).toBeGreaterThanOrEqual(-1);
        expect(box.x + box.width).toBeLessThanOrEqual(vp.width + 1);
      }
      await info.attach(`${name}`, { body: await page.screenshot(), contentType: 'image/png' });
    });
  }
});
