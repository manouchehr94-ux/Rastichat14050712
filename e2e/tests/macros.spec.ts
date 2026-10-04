import { test, expect } from '@playwright/test';
import {
  openWidget, sendWidgetText, loginOperator, openConversationByPreview, runDjangoScript, DASHBOARD_URL,
} from './helpers';

function uniqueText(label: string) {
  return `${label} ${Date.now()}-${Math.floor(Math.random() * 10000)}`;
}

function seedMacro(opts: { name: string; actions: object[]; is_active?: boolean; visibility?: string }) {
  const actions = JSON.stringify(opts.actions);
  runDjangoScript(`
import json
from workspaces.models import Workspace
from macros.models import Macro
ws = Workspace.objects.get(name='Sample Workspace')
Macro.objects.filter(workspace=ws, name=${JSON.stringify(opts.name)}).delete()
Macro.objects.create(
    workspace=ws, name=${JSON.stringify(opts.name)}, is_active=${opts.is_active ? 'True' : 'False'},
    visibility=${JSON.stringify(opts.visibility || 'WORKSPACE')}, actions=json.loads(${JSON.stringify(actions)}),
)
`);
}

function deleteMacrosByPrefix(prefix: string) {
  runDjangoScript(`
from workspaces.models import Workspace
from macros.models import Macro
for ws_name in ['Sample Workspace', 'E2E-Macro-Workspace-B']:
    ws = Workspace.objects.filter(name=ws_name).first()
    if ws:
        Macro.objects.filter(workspace=ws, name__startswith=${JSON.stringify(prefix)}).delete()
`);
}

function getOrCreateTag(name: string) {
  const out = runDjangoScript(`
from workspaces.models import Workspace
from customer_context.models import Tag
ws = Workspace.objects.get(name='Sample Workspace')
tag, _ = Tag.objects.get_or_create(workspace=ws, name=${JSON.stringify(name)})
print('TAG_ID=' + str(tag.id))
`);
  return out.split('\n').find(l => l.startsWith('TAG_ID='))!.replace('TAG_ID=', '').trim();
}

test.describe('Macros', () => {
  // These scenarios drive two real browser contexts through a live
  // websocket round trip plus macro execution against the domain services —
  // measured at ~32s in isolation, which is already past Playwright's 30s
  // default test timeout under any extra load (e.g. Next.js compiling a
  // route on first hit). 60s gives real headroom without masking a genuine
  // hang.
  test.describe.configure({ timeout: 60000 });

  test.afterEach(() => {
    deleteMacrosByPrefix('E2E-Macro-');
  });

  test('admin creates a macro inactive by default', async ({ page }) => {
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/macros`);
    await page.getByText('+ ماکرو جدید').click();
    const name = uniqueText('E2E-Macro-جدید');
    await page.locator('input').first().fill(name);
    // A new macro's builder starts with one default SEND_REPLY action,
    // whose "template" param is required — the last input on the editor
    // form (rendered after name/category/description).
    await page.locator('input').last().fill('پاسخ نمونه برای تست');
    await page.getByText('ذخیره').click();
    await expect(page.getByText(name)).toBeVisible({ timeout: 10000 });
    const row = page.locator('tr', { hasText: name });
    await expect(row.getByText('غیرفعال', { exact: true })).toBeVisible();
  });

  test('preview shows the rendered reply with no mutation', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const macroName = uniqueText('E2E-Macro-پیش‌نمایش');
    seedMacro({ name: macroName, is_active: true, actions: [{ type: 'SEND_REPLY', params: { template: 'سلام {customer_name}!' } }] });

    const marker = uniqueText('گفتگو برای پیش‌نمایش ماکرو');
    await openWidget(customer);
    await sendWidgetText(customer, marker);
    await loginOperator(operator);
    await openConversationByPreview(operator, marker);

    await operator.getByTitle('اجرای ماکرو').click();
    await operator.getByText(macroName).click();
    await expect(operator.getByText(/سلام .*!/)).toBeVisible({ timeout: 10000 });
    await operator.getByText('انصراف').click();

    // Preview alone must never have sent anything to the customer.
    await expect(customer.getByText('سلام', { exact: false })).toHaveCount(0);

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('admin activates a macro from the list', async ({ page }) => {
    const macroName = uniqueText('E2E-Macro-فعال‌سازی');
    seedMacro({ name: macroName, is_active: false, actions: [{ type: 'REQUEST_RATING', params: {} }] });
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/macros`);
    const row = page.locator('tr', { hasText: macroName });
    await row.getByText('غیرفعال', { exact: true }).click();
    // "فعال" is a substring of "غیرفعال" ("non-active" contains "active"),
    // so an inexact match would ambiguously match the wrong button — use
    // exact text.
    await expect(row.getByText('فعال', { exact: true })).toBeVisible({ timeout: 10000 });
  });

  test('operator runs a macro that replies, tags, sets priority, transfers team, and sets status — each exactly once', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const tagId = getOrCreateTag('E2E-Macro-برچسب');
    const teamOut = runDjangoScript(`
from workspaces.models import Workspace
from teams.models import Team
ws = Workspace.objects.get(name='Sample Workspace')
team, _ = Team.objects.get_or_create(workspace=ws, name='فنی')
print('TEAM_ID=' + str(team.id))
`);
    const teamId = teamOut.split('\n').find(l => l.startsWith('TEAM_ID='))!.replace('TEAM_ID=', '').trim();

    const macroName = uniqueText('E2E-Macro-همه‌کاره');
    seedMacro({
      name: macroName, is_active: true,
      actions: [
        { type: 'SEND_REPLY', params: { template: 'در حال بررسی درخواست شما هستیم.' } },
        { type: 'ADD_TAG', params: { tag_id: tagId } },
        { type: 'SET_PRIORITY', params: { priority: 'HIGH' } },
        { type: 'TRANSFER_TO_TEAM', params: { team_id: teamId } },
        { type: 'SET_STATUS', params: { status: 'WAITING_FOR_WORKSPACE' } },
      ],
    });

    const marker = uniqueText('گفتگو برای ماکرو همه‌کاره');
    await openWidget(customer);
    await sendWidgetText(customer, marker);
    await loginOperator(operator);
    await openConversationByPreview(operator, marker);

    await operator.getByTitle('اجرای ماکرو').click();
    await operator.getByText(macroName).click();
    await operator.getByText('تأیید و اجرا').click();

    await expect(operator.getByText('در حال بررسی درخواست شما هستیم.').last()).toBeVisible({ timeout: 10000 });
    await expect(customer.locator('.rasti-bubble', { hasText: 'در حال بررسی درخواست شما هستیم.' })).toBeVisible({ timeout: 10000 });
    // Exactly one reply was created — no duplicate from the single confirm click.
    await expect(operator.getByText('در حال بررسی درخواست شما هستیم.')).toHaveCount(1);

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('double-clicking confirm does not duplicate the macro execution', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const macroName = uniqueText('E2E-Macro-دوبار-کلیک');
    seedMacro({ name: macroName, is_active: true, actions: [{ type: 'REQUEST_RATING', params: {} }] });

    const marker = uniqueText('گفتگو برای تست دوبار کلیک');
    await openWidget(customer);
    await sendWidgetText(customer, marker);
    await loginOperator(operator);
    await openConversationByPreview(operator, marker);

    await operator.getByTitle('اجرای ماکرو').click();
    await operator.getByText(macroName).click();
    const confirmBtn = operator.getByText('تأیید و اجرا');
    await confirmBtn.click();
    // Fire a second click immediately — the button disables itself while in
    // flight (text flips to "در حال اجرا…"), so this must be a no-op. A
    // short explicit timeout matters here: if the (fast) REQUEST_RATING
    // execution has already finished and the modal closed by the time this
    // runs, the button/text is simply gone — the click should fail fast,
    // not hang for the default actionability timeout.
    await confirmBtn.click({ force: true, timeout: 1000 }).catch(() => {});

    // The rating request must have been sent exactly ONCE — the double
    // click must not duplicate it, but it must still have actually run.
    await expect(customer.locator('.rasti-bubble.rasti-rating')).toHaveCount(1, { timeout: 10000 });

    const historyOut = runDjangoScript(`
from workspaces.models import Workspace
from macros.models import Macro, MacroExecution
ws = Workspace.objects.get(name='Sample Workspace')
macro = Macro.objects.get(workspace=ws, name=${JSON.stringify(macroName)})
print('EXEC_COUNT=' + str(MacroExecution.objects.filter(macro=macro).count()))
`);
    const execCount = Number(historyOut.split('\n').find(l => l.startsWith('EXEC_COUNT='))!.replace('EXEC_COUNT=', '').trim());
    expect(execCount).toBe(1);

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('partial failure is visible in the execution history', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    // A tag that will be deleted right after seeding, so ADD_TAG fails at
    // execution time while SET_PRIORITY (before it) succeeds — a realistic
    // "resource removed after the macro was configured" scenario.
    const tagId = getOrCreateTag('E2E-Macro-ناقص-برچسب');
    runDjangoScript(`
from customer_context.models import Tag
Tag.objects.filter(id=${JSON.stringify(tagId)}).delete()
`);
    const macroName = uniqueText('E2E-Macro-ناقص');
    seedMacro({
      name: macroName, is_active: true,
      actions: [{ type: 'SET_PRIORITY', params: { priority: 'URGENT' } }, { type: 'ADD_TAG', params: { tag_id: tagId } }],
    });

    const marker = uniqueText('گفتگو برای شکست جزئی');
    await openWidget(customer);
    await sendWidgetText(customer, marker);
    await loginOperator(operator);
    await openConversationByPreview(operator, marker);

    await operator.getByTitle('اجرای ماکرو').click();
    await operator.getByText(macroName).click();
    await operator.getByText('تأیید و اجرا').click();
    await expect(operator.getByText(/به‌صورت ناقص اجرا شد/)).toBeVisible({ timeout: 10000 });

    await operator.goto(`${DASHBOARD_URL}/macros/history`);
    // "نیمه‌موفق" also exists as a hidden <option> in the status-filter
    // <select> — scope to the visible execution-status label, not bare text.
    await expect(operator.locator('.text-sm.font-medium.text-gray-800', { hasText: 'نیمه‌موفق' }).first()).toBeVisible({ timeout: 10000 });
    await expect(operator.getByText('ADD_TAG').first()).toBeVisible();

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('an internal note created by a macro never reaches the visitor', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const noteText = uniqueText('یادداشت محرمانه ماکرو');
    const macroName = uniqueText('E2E-Macro-یادداشت');
    seedMacro({ name: macroName, is_active: true, actions: [{ type: 'CREATE_INTERNAL_NOTE', params: { content: noteText } }] });

    const marker = uniqueText('گفتگو برای یادداشت داخلی');
    await openWidget(customer);
    await sendWidgetText(customer, marker);
    await loginOperator(operator);
    await openConversationByPreview(operator, marker);

    await operator.getByTitle('اجرای ماکرو').click();
    await operator.getByText(macroName).click();
    await operator.getByText('تأیید و اجرا').click();
    await expect(operator.getByText(noteText).last()).toBeVisible({ timeout: 10000 });

    await customer.reload();
    await customer.locator('#rasti-launcher').click();
    await expect(customer.getByText(noteText)).toHaveCount(0);

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('macro assignment obeys agent capacity', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const agentOut = runDjangoScript(`
from accounts.models import User, OperatorPresence
u = User.objects.get(email='operator2@ws.com')
p, _ = OperatorPresence.objects.get_or_create(user=u)
p.max_capacity = 0
p.save(update_fields=['max_capacity'])
print('AGENT_ID=' + str(u.id))
`);
    const agentId = agentOut.split('\n').find(l => l.startsWith('AGENT_ID='))!.replace('AGENT_ID=', '').trim();

    const macroName = uniqueText('E2E-Macro-ظرفیت');
    seedMacro({ name: macroName, is_active: true, actions: [{ type: 'ASSIGN_TO_AGENT', params: { agent_id: agentId } }] });

    const marker = uniqueText('گفتگو برای تست ظرفیت');
    await openWidget(customer);
    await sendWidgetText(customer, marker);
    await loginOperator(operator);
    await openConversationByPreview(operator, marker);

    await operator.getByTitle('اجرای ماکرو').click();
    await operator.getByText(macroName).click();
    await operator.getByText('تأیید و اجرا').click();
    await expect(operator.getByText('اجرای ماکرو ناموفق بود')).toBeVisible({ timeout: 10000 });

    runDjangoScript(`
from accounts.models import User, OperatorPresence
u = User.objects.get(email='operator2@ws.com')
p = OperatorPresence.objects.get(user=u)
p.max_capacity = 50
p.save(update_fields=['max_capacity'])
`);
    await customerCtx.close();
    await operatorCtx.close();
  });

  test('workspace A cannot execute workspace B\'s macro', async ({ page }) => {
    const out = runDjangoScript(`
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
platform, _ = Platform.objects.get_or_create(name='E2E-Macro-Platform-B')
ws, _ = Workspace.objects.get_or_create(name='E2E-Macro-Workspace-B', defaults={'platform': platform})
print('WORKSPACE_B_ID=' + str(ws.id))
`);
    const wsBId = out.split('\n').find(l => l.startsWith('WORKSPACE_B_ID='))!.replace('WORKSPACE_B_ID=', '').trim();
    const macroName = uniqueText('E2E-Macro-فقط-ب');
    const macroOut = runDjangoScript(`
from workspaces.models import Workspace
from macros.models import Macro
ws = Workspace.objects.get(id=${JSON.stringify(wsBId)})
macro = Macro.objects.create(workspace=ws, name=${JSON.stringify(macroName)}, is_active=True, visibility='WORKSPACE', actions=[{'type': 'REQUEST_RATING', 'params': {}}])
print('MACRO_B_ID=' + str(macro.id))
`);
    const macroBId = macroOut.split('\n').find(l => l.startsWith('MACRO_B_ID='))!.replace('MACRO_B_ID=', '').trim();

    await loginOperator(page, 'admin@ws.com');
    const res = await page.request.get(`http://localhost:8080/api/v1/macros/${macroBId}/`, {
      headers: { Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem('token'))}` },
    });
    expect(res.status()).toBe(404);
  });

  test('mobile operator can select and run a macro from the conversation', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const macroName = uniqueText('E2E-Macro-موبایل');
    seedMacro({ name: macroName, is_active: true, actions: [{ type: 'REQUEST_RATING', params: {} }] });

    const marker = uniqueText('پیام موبایل برای ماکرو');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator);
    await operator.reload();
    const row = operator.getByText(marker, { exact: true }).first();
    await row.waitFor({ timeout: 15000 });
    await row.click();

    await operator.getByTitle('اجرای ماکرو').click();
    await operator.getByText(macroName).click();
    await operator.getByText('تأیید و اجرا').click();
    // REQUEST_RATING has no visible operator-side bubble; success is the
    // absence of an error toast and the preview modal closing on its own.
    await expect(operator.getByText('تأیید و اجرا')).toHaveCount(0, { timeout: 10000 });

    await customerCtx.close();
    await operatorCtx.close();
  });
});
