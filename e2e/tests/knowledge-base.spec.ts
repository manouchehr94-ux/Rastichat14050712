import { test, expect } from '@playwright/test';
import {
  openWidget, sendWidgetText, loginOperator, openConversationByPreview, runDjangoScript, fixture, DASHBOARD_URL,
} from './helpers';

function uniqueText(label: string) {
  return `${label} ${Date.now()}-${Math.floor(Math.random() * 10000)}`;
}

function publishArticle(opts: { title: string; body?: string; excerpt?: string; visibility?: string; category?: string }) {
  runDjangoScript(`
from workspaces.models import Workspace
from knowledge_base import services
from knowledge_base.models import KnowledgeBaseArticle, KnowledgeBaseCategory
ws = Workspace.objects.get(name='Sample Workspace')
KnowledgeBaseArticle.objects.filter(workspace=ws, title=${JSON.stringify(opts.title)}).delete()
category = None
${opts.category ? `category, _ = KnowledgeBaseCategory.objects.get_or_create(workspace=ws, slug=${JSON.stringify(opts.category)}, defaults={'name': ${JSON.stringify(opts.category)}})` : ''}
article = services.create_article(
    ws, None, title=${JSON.stringify(opts.title)}, body=${JSON.stringify(opts.body || 'متن نمونه')},
    excerpt=${JSON.stringify(opts.excerpt || '')}, category=category,
    visibility=${JSON.stringify(opts.visibility || 'CUSTOMER')},
)
services.publish_article(article, None)
print('SLUG=' + article.slug)
`);
}

function deleteArticlesByTitlePrefix(prefix: string) {
  runDjangoScript(`
from workspaces.models import Workspace
from knowledge_base.models import KnowledgeBaseArticle
for ws_name in ['Sample Workspace', 'E2E-KB-Workspace-B']:
    ws = Workspace.objects.filter(name=ws_name).first()
    if ws:
        KnowledgeBaseArticle.objects.filter(workspace=ws, title__startswith=${JSON.stringify(prefix)}).delete()
`);
}

test.describe('Knowledge Base', () => {
  // These scenarios drive two real browser contexts through a live
  // websocket round trip plus a debounced-search UI popover — measured at
  // ~32s in isolation, which is already past Playwright's 30s default test
  // timeout under any extra load (e.g. Next.js compiling a route on first
  // hit). 60s gives real headroom without masking a genuine hang.
  test.describe.configure({ timeout: 60000 });

  test.afterEach(() => {
    deleteArticlesByTitlePrefix('E2E-KB-');
  });

  test('admin creates a category and an article', async ({ page }) => {
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/knowledge-base/categories`);
    const catName = uniqueText('E2E-KB-دسته');
    await page.getByPlaceholder('نام دسته‌بندی جدید').fill(catName);
    await page.getByText('+ افزودن').click();
    // The new category name also appears as an <option> in the parent-
    // category picker <select> — scope to the visible tree row (a <span>),
    // not bare text, to avoid an ambiguous match.
    await expect(page.locator('span', { hasText: catName })).toBeVisible();

    await page.goto(`${DASHBOARD_URL}/knowledge-base`);
    await page.getByText('+ مقاله جدید').click();
    const title = uniqueText('E2E-KB-مقاله');
    const inputs = page.locator('input');
    await inputs.first().fill(title);
    await page.getByText('ذخیره').click();
    await expect(page.getByText(title)).toBeVisible({ timeout: 10000 });
  });

  test('draft article is invisible to a visitor', async ({ page }) => {
    const title = uniqueText('E2E-KB-پیش‌نویس');
    runDjangoScript(`
from workspaces.models import Workspace
from knowledge_base import services
ws = Workspace.objects.get(name='Sample Workspace')
services.create_article(ws, None, title=${JSON.stringify(title)}, body='متن', visibility='CUSTOMER')
`);
    const res = await page.request.get(`http://localhost:8080/api/v1/kb/public/articles/?project_key=${fixture.projectKey}`);
    const body = await res.json();
    const titles = body.results.map((a: { title: string }) => a.title);
    expect(titles).not.toContain(title);
  });

  test('publishing an article makes it visible to the public API', async ({ page }) => {
    const title = uniqueText('E2E-KB-منتشرشده');
    publishArticle({ title });
    const res = await page.request.get(`http://localhost:8080/api/v1/kb/public/articles/?project_key=${fixture.projectKey}`);
    const body = await res.json();
    const titles = body.results.map((a: { title: string }) => a.title);
    expect(titles).toContain(title);
  });

  test('visitor searches the Knowledge Base and opens an article', async ({ page }) => {
    const title = uniqueText('E2E-KB-جستجو راهنما');
    publishArticle({ title, body: 'این یک راهنمای جامع است' });
    const res = await page.request.get(`http://localhost:8080/api/v1/kb/public/articles/search/?project_key=${fixture.projectKey}&q=${encodeURIComponent(title)}`);
    const body = await res.json();
    expect(body.results.some((a: { title: string }) => a.title === title)).toBe(true);
    const slug = body.results.find((a: { title: string }) => a.title === title).slug;
    const detail = await page.request.get(`http://localhost:8080/api/v1/kb/public/articles/${slug}/?project_key=${fixture.projectKey}`);
    expect(detail.ok()).toBe(true);
    const detailBody = await detail.json();
    expect(detailBody.title).toBe(title);
  });

  test('operator shares an article card, visitor receives it and it survives a reload', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const title = uniqueText('E2E-KB-اشتراک‌گذاری');
    publishArticle({ title, excerpt: 'خلاصه مقاله' });

    const marker = uniqueText('شروع گفتگو برای اشتراک مقاله');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator);
    await openConversationByPreview(operator, marker);
    await expect(operator.getByText(marker).last()).toBeVisible();

    await operator.getByTitle('جستجوی پایگاه دانش').click();
    await operator.getByPlaceholder('جستجو در عنوان، خلاصه و متن…').fill(title);
    await operator.getByText('ارسال کارت مقاله').click();

    await expect(operator.getByText(title).last()).toBeVisible({ timeout: 10000 });
    await expect(customer.locator('.rasti-bubble', { hasText: title })).toBeVisible({ timeout: 10000 });

    await customer.reload();
    await customer.locator('#rasti-launcher').click();
    await expect(customer.locator('.rasti-bubble', { hasText: title })).toBeVisible({ timeout: 10000 });

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('editing an article creates a revision, and an admin can restore an old one', async ({ page }) => {
    await loginOperator(page, 'admin@ws.com');
    const title = uniqueText('E2E-KB-بازبینی');
    runDjangoScript(`
from workspaces.models import Workspace
from knowledge_base import services
ws = Workspace.objects.get(name='Sample Workspace')
services.create_article(ws, None, title=${JSON.stringify(title)}, body='نسخه اول متن')
`);
    await page.goto(`${DASHBOARD_URL}/knowledge-base`);
    await page.getByText(title).click();
    const bodyField = page.locator('textarea');
    await bodyField.fill('نسخه دوم متن — ویرایش شده');
    await page.getByText('ذخیره').click();
    // Saving returns to the article list — this IS the list view again.
    await expect(page.getByText(title)).toBeVisible({ timeout: 10000 });

    const row = page.locator('tr', { hasText: title });
    await row.getByText('تاریخچه').click();
    await expect(page.getByText(/نسخه 2 —/)).toBeVisible();
    await expect(page.getByText(/نسخه 1 —/)).toBeVisible();
    // Playwright must have the dialog handler armed BEFORE the click that
    // triggers the synchronous confirm() — registering it after risks a
    // race against the blocking native dialog.
    page.once('dialog', d => d.accept());
    await page.getByText('بازگردانی این نسخه').click();
    await expect(page.getByText('نسخه بازگردانی شد', { exact: false })).toBeVisible({ timeout: 10000 });
  });

  test('an internal article never reaches a visitor even by direct URL guess', async ({ page }) => {
    const title = uniqueText('E2E-KB-فقط داخلی');
    runDjangoScript(`
from workspaces.models import Workspace
from knowledge_base import services
ws = Workspace.objects.get(name='Sample Workspace')
article = services.create_article(ws, None, title=${JSON.stringify(title)}, body='متن محرمانه داخلی', visibility='INTERNAL')
services.publish_article(article, None)
print('SLUG=' + article.slug)
`);
    const listRes = await page.request.get(`http://localhost:8080/api/v1/kb/public/articles/?project_key=${fixture.projectKey}`);
    const listBody = await listRes.json();
    expect(listBody.results.map((a: { title: string }) => a.title)).not.toContain(title);

    // Slug is deterministic from the title (slugify) — guess it directly.
    const guessedSlug = title.toLowerCase().replace(/\s+/g, '-');
    const detailRes = await page.request.get(`http://localhost:8080/api/v1/kb/public/articles/${guessedSlug}/?project_key=${fixture.projectKey}`);
    expect(detailRes.status()).toBe(404);
  });

  test('workspace isolation: an article from workspace A is invisible under workspace B\'s project_key', async ({ page }) => {
    const title = uniqueText('E2E-KB-انزوای فضای‌کار');
    publishArticle({ title });

    const out = runDjangoScript(`
from platforms.models import Platform
from workspaces.models import Workspace
from projects.models import Project
platform, _ = Platform.objects.get_or_create(name='E2E-KB-Platform-B')
ws, _ = Workspace.objects.get_or_create(name='E2E-KB-Workspace-B', defaults={'platform': platform})
project, _ = Project.objects.get_or_create(name='E2E-KB-Project-B', defaults={'workspace': ws})
print('PROJECT_KEY_B=' + str(project.public_key))
`);
    const projectKeyB = out.split('\n').find(l => l.startsWith('PROJECT_KEY_B='))!.replace('PROJECT_KEY_B=', '').trim();

    const res = await page.request.get(`http://localhost:8080/api/v1/kb/public/articles/?project_key=${projectKeyB}`);
    const body = await res.json();
    expect(body.results.map((a: { title: string }) => a.title)).not.toContain(title);
  });
});
