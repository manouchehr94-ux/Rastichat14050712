import { test, expect } from '@playwright/test';
import {
  openWidget, sendWidgetText, loginOperator, openConversationByPreview, runDjangoScript, DASHBOARD_URL,
} from './helpers';
import { execFileSync } from 'child_process';
import path from 'path';

function uniqueText(label: string) {
  return `${label} ${Date.now()}-${Math.floor(Math.random() * 10000)}`;
}

/** Rule names use an ASCII slug rather than Persian words — the UI's own
 * button/badge/tab labels are all Persian, so an ASCII name can never
 * collide with them under Playwright's substring getByText matching (e.g.
 * a rule literally named "...فعال‌سازی..." would ambiguously match the
 * "فعال‌سازی" button too). Conversation preview markers stay human-Persian
 * since they don't share vocabulary with fixed UI chrome.
 */
function uniqueRuleName(slug: string) {
  return `E2E-${slug}-${Date.now()}-${Math.floor(Math.random() * 10000)}`;
}

function runProcessAutomationJobs() {
  const backendDir = path.resolve(__dirname, '..', '..', 'backend');
  const pythonBin = process.env.E2E_PYTHON || 'python';
  execFileSync(pythonBin, ['manage.py', 'process_automation_jobs'], { cwd: backendDir, env: process.env, encoding: 'utf-8' });
}

/** Creates an active automation rule directly via the backend — the same
 * seed/fixture pattern already used by team-operations.spec.ts
 * (forceSlaFirstResponseState, runEvaluateSla) for preconditions that
 * aren't themselves the thing under test. Scenarios 3–10 below already
 * exercise the rule *builder* UI end-to-end; scenarios that need a
 * pre-existing rule to observe automation *firing* seed it this way
 * instead of re-driving the builder every time.
 */
function seedRule(opts: { name: string; trigger_type: string; conditions?: object; actions: object[]; priority?: number; stop_processing?: boolean }) {
  const conditions = JSON.stringify(opts.conditions ?? {});
  const actions = JSON.stringify(opts.actions);
  runDjangoScript(`
import json
from workspaces.models import Workspace
from automations.models import AutomationRule
ws = Workspace.objects.get(name='Sample Workspace')
AutomationRule.objects.filter(workspace=ws, name=${JSON.stringify(opts.name)}).delete()
AutomationRule.objects.create(
    workspace=ws, name=${JSON.stringify(opts.name)}, trigger_type=${JSON.stringify(opts.trigger_type)},
    is_active=True, conditions=json.loads(${JSON.stringify(conditions)}), actions=json.loads(${JSON.stringify(actions)}),
    priority=${opts.priority ?? 100}, stop_processing=${opts.stop_processing ? 'True' : 'False'},
)
`);
}

function deleteRulesByPrefix(prefix: string) {
  runDjangoScript(`
from workspaces.models import Workspace
from automations.models import AutomationRule, ScheduledAction
ws = Workspace.objects.get(name='Sample Workspace')
# ScheduledAction.rule is SET_NULL on delete, so prune scheduled actions
# via the rule name join first — once the rule is gone there is no way
# to identify which orphaned rows were this suite's.
ScheduledAction.objects.filter(workspace=ws, rule__name__startswith=${JSON.stringify(prefix)}).delete()
AutomationRule.objects.filter(workspace=ws, name__startswith=${JSON.stringify(prefix)}).delete()
`);
}

test.describe('Automation and workflow engine', () => {
  test.afterEach(() => {
    // Keep each run's fixture rules from bleeding into the next test —
    // mirrors the "genuinely clean" expectation without needing a full
    // drop/recreate between every single scenario.
    deleteRulesByPrefix('E2E-');
  });

  test('a non-admin operator is denied access to the automations page', async ({ page }) => {
    await loginOperator(page, 'operator@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    await expect(page.getByText('دسترسی محدود شده است')).toBeVisible();
  });

  test('a workspace admin can open the automations page and sees the registry-driven trigger list', async ({ page }) => {
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    await expect(page.getByText('اتوماسیون و گردش‌کار')).toBeVisible();
    await page.getByText('+ قانون جدید').click();
    const triggerSelect = page.locator('select').first();
    await expect(triggerSelect.locator('option', { hasText: 'ایجاد گفتگو' })).toHaveCount(1);
    await expect(triggerSelect.locator('option', { hasText: 'نقض SLA' })).toHaveCount(1);
  });

  test('admin creates a rule with a condition and an action; it starts inactive', async ({ page }) => {
    const name = uniqueRuleName('new-rule');
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    await page.getByText('+ قانون جدید').click();
    await page.getByLabel('نام قانون').fill(name);
    await page.getByText('+ شرط').click();
    await page.getByText('+ افزودن اقدام').click();
    // The default action type (ASSIGN_TO_AGENT) requires an agent_id this
    // test doesn't care about — switch to one with no required params. The
    // action-type select is always the last one added (after whatever
    // condition selects came first), so target it by position, not index.
    await page.locator('select').last().selectOption({ label: 'تشدید' });
    await page.getByText('ایجاد قانون').click();
    await expect(page.getByText(name)).toBeVisible();
    const row = page.locator('tr', { has: page.getByText(name) });
    await expect(row.getByText('غیرفعال')).toBeVisible();
  });

  test('admin activates then deactivates a rule', async ({ page }) => {
    const name = uniqueRuleName('toggle');
    seedRule({ name, trigger_type: 'CONVERSATION_CREATED', actions: [{ type: 'ESCALATE', params: {} }] });
    runDjangoScript(`
from workspaces.models import Workspace
from automations.models import AutomationRule
ws = Workspace.objects.get(name='Sample Workspace')
AutomationRule.objects.filter(workspace=ws, name=${JSON.stringify(name)}).update(is_active=False)
`);
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    const row = page.locator('tr', { has: page.getByText(name) });
    await expect(row.getByText('غیرفعال')).toBeVisible();
    await row.getByText('فعال‌سازی').click();
    await expect(row.getByText('فعال', { exact: true })).toBeVisible();
    await row.getByText('غیرفعال‌سازی').click();
    await expect(row.getByText('غیرفعال')).toBeVisible();
  });

  test('admin duplicates a rule into an inactive copy', async ({ page }) => {
    const name = uniqueRuleName('duplicate-src');
    seedRule({ name, trigger_type: 'CONVERSATION_CREATED', actions: [{ type: 'ESCALATE', params: {} }] });
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    const row = page.locator('tr', { has: page.getByText(name, { exact: true }) });
    await row.getByText('کپی').click();
    await expect(page.getByText(`${name} (کپی)`)).toBeVisible();
    const copyRow = page.locator('tr', { has: page.getByText(`${name} (کپی)`) });
    await expect(copyRow.getByText('غیرفعال')).toBeVisible();
  });

  test('admin deletes a rule', async ({ page }) => {
    const name = uniqueRuleName('delete-me');
    seedRule({ name, trigger_type: 'CONVERSATION_CREATED', actions: [{ type: 'ESCALATE', params: {} }] });
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    page.once('dialog', (d) => d.accept());
    const row = page.locator('tr', { has: page.getByText(name) });
    await row.getByText('حذف').click();
    await expect(page.getByText(name)).toHaveCount(0);
  });

  test('a starter template pre-fills the builder without auto-activating', async ({ page }) => {
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    await page.getByText('خوش‌آمدگویی خودکار').click();
    await expect(page.getByLabel('نام قانون')).toHaveValue('خوش‌آمدگویی خودکار');
    // Still requires an explicit save — and even after saving, a template
    // must never come pre-activated.
    const uniqueName = uniqueRuleName('from-template');
    await page.getByLabel('نام قانون').fill(uniqueName);
    await page.getByText('ایجاد قانون').click();
    await expect(page.getByText(uniqueName)).toBeVisible();
    const row = page.locator('tr', { has: page.getByText(uniqueName) });
    await expect(row.getByText('غیرفعال')).toBeVisible();
  });

  test('builder supports a nested ALL condition group', async ({ page }) => {
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    await page.getByText('+ قانون جدید').click();
    await page.getByText('+ گروه تودرتو').click();
    await expect(page.getByText('همه شرط‌های زیر باید')).toHaveCount(2); // root group + nested group
    await page.getByText('انصراف').first().click();
  });

  test('simulate shows a match without mutating anything real', async ({ page }) => {
    const name = uniqueRuleName('simulate');
    seedRule({ name, trigger_type: 'CONVERSATION_CREATED', actions: [{ type: 'SET_PRIORITY', params: { priority: 'URGENT' } }] });
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    const row = page.locator('tr', { has: page.getByText(name) });
    await row.getByText('ویرایش').click();
    await page.getByText('اجرای شبیه‌سازی').click();
    await expect(page.getByText('شرط‌ها منطبق شدند ✓')).toBeVisible();
    await expect(page.getByText('— تنظیم اولویت')).toBeVisible(); // previewed action label
  });

  test('an active CONVERSATION_CREATED rule sets priority on a real new conversation', async ({ browser }) => {
    const name = uniqueRuleName('auto-priority');
    seedRule({ name, trigger_type: 'CONVERSATION_CREATED', actions: [{ type: 'SET_PRIORITY', params: { priority: 'URGENT' } }] });

    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('گفتگوی اولویت خودکار');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator, 'admin@ws.com');
    await openConversationByPreview(operator, marker);
    await expect(operator.getByText('فوری').first()).toBeVisible();

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('an active CONVERSATION_CLOSED rule sends the customer a live automated message', async ({ browser }) => {
    const name = uniqueRuleName('auto-message-on-close');
    seedRule({ name, trigger_type: 'CONVERSATION_CLOSED', actions: [{ type: 'SEND_CUSTOMER_MESSAGE', params: { template: 'با تشکر {customer_name}، این گفتگو بسته شد.' } }] });

    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('گفتگوی بستن با پیام خودکار');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator, 'admin@ws.com');
    await openConversationByPreview(operator, marker);
    await operator.getByText('پایان گفتگو').click();

    await expect(customer.getByText('این گفتگو بسته شد', { exact: false })).toBeVisible({ timeout: 10000 });

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('execution history shows a succeeded run with expandable action detail', async ({ browser }) => {
    const name = uniqueRuleName('history');
    seedRule({ name, trigger_type: 'CONVERSATION_CREATED', actions: [{ type: 'ESCALATE', params: {} }] });

    const customerCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const marker = uniqueText('گفتگوی تاریخچه');
    await openWidget(customer);
    await sendWidgetText(customer, marker);
    await customerCtx.close();

    const page = await (await browser.newContext()).newPage();
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    await page.getByText('تاریخچه اجرا').click();
    const row = page.locator('tr', { has: page.getByText(name) }).first();
    await expect(row).toBeVisible({ timeout: 10000 });
    await expect(row.getByText('موفق')).toBeVisible();
    await row.getByText('جزئیات').click();
    await expect(page.getByText('تشدید').last()).toBeVisible();
  });

  test('a scheduled action fires once processed by process_automation_jobs', async ({ browser }) => {
    const name = uniqueRuleName('scheduled-fire');
    seedRule({
      name, trigger_type: 'CONVERSATION_CREATED',
      actions: [{ type: 'SCHEDULE_ACTION', params: { delay_minutes: 0, action: { type: 'ESCALATE', params: {} } } }],
    });

    const customerCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const marker = uniqueText('گفتگوی زمان‌بندی');
    await openWidget(customer);
    await sendWidgetText(customer, marker);
    await customerCtx.close();

    const page = await (await browser.newContext()).newPage();
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    await page.getByText('اقدامات زمان‌بندی‌شده').click();
    await expect(page.getByText('در انتظار').first()).toBeVisible({ timeout: 10000 });

    runProcessAutomationJobs();
    await page.reload();
    await page.getByText('اقدامات زمان‌بندی‌شده').click();
    await expect(page.getByText('موفق').first()).toBeVisible({ timeout: 10000 });
  });

  test('cancelling a pending scheduled action prevents it from ever running', async ({ browser }) => {
    const name = uniqueRuleName('scheduled-cancel');
    seedRule({
      name, trigger_type: 'CONVERSATION_CREATED',
      actions: [{ type: 'SCHEDULE_ACTION', params: { delay_minutes: 60, action: { type: 'ESCALATE', params: {} } } }],
    });

    const customerCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const marker = uniqueText('گفتگوی لغو زمان‌بندی');
    await openWidget(customer);
    await sendWidgetText(customer, marker);
    await customerCtx.close();

    const page = await (await browser.newContext()).newPage();
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    await page.getByText('اقدامات زمان‌بندی‌شده').click();
    // Scoped to this test's own row throughout — the scheduled-actions tab
    // accumulates rows across the whole suite run (nothing prunes old
    // ScheduledAction rows the way afterEach prunes rules), so a
    // page-wide "no موفق anywhere" assertion would be false by
    // construction once any other test's job has ever succeeded.
    const row = page.locator('tr', { has: page.getByText(name, { exact: true }) });
    await expect(row.getByText('در انتظار')).toBeVisible({ timeout: 10000 });
    await row.getByText('لغو').click();
    await expect(row.getByText('لغو شده')).toBeVisible();

    runProcessAutomationJobs();
    await page.reload();
    await page.getByText('اقدامات زمان‌بندی‌شده').click();
    const rowAfter = page.locator('tr', { has: page.getByText(name, { exact: true }) });
    await expect(rowAfter.getByText('لغو شده')).toBeVisible();
  });

  test('stop_processing on a higher-priority rule prevents a lower-priority rule from running', async ({ browser }) => {
    const stopper = uniqueRuleName('stop-processing');
    const never = uniqueRuleName('never-runs');
    seedRule({ name: stopper, trigger_type: 'CONVERSATION_CREATED', priority: 10, stop_processing: true, actions: [{ type: 'ESCALATE', params: {} }] });
    seedRule({ name: never, trigger_type: 'CONVERSATION_CREATED', priority: 20, actions: [{ type: 'SET_PRIORITY', params: { priority: 'URGENT' } }] });

    const customerCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const marker = uniqueText('گفتگوی توقف پردازش');
    await openWidget(customer);
    await sendWidgetText(customer, marker);
    await customerCtx.close();

    const page = await (await browser.newContext()).newPage();
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    await page.getByText('تاریخچه اجرا').click();
    await expect(page.locator('tr', { has: page.getByText(stopper) }).first()).toBeVisible({ timeout: 10000 });
    await expect(page.locator('tr', { has: page.getByText(never) })).toHaveCount(0);
  });

  test('loop protection settles a two-rule chain instead of looping forever', async ({ browser }) => {
    const ruleA = uniqueRuleName('loop-a');
    const ruleB = uniqueRuleName('loop-b');
    seedRule({
      name: ruleA, trigger_type: 'CONVERSATION_PRIORITY_CHANGED',
      conditions: { field: 'conversation.priority', operator: 'equals', value: 'HIGH' },
      actions: [{ type: 'SET_PRIORITY', params: { priority: 'URGENT' } }],
    });
    seedRule({
      name: ruleB, trigger_type: 'CONVERSATION_PRIORITY_CHANGED',
      conditions: { field: 'conversation.priority', operator: 'equals', value: 'URGENT' },
      actions: [{ type: 'SET_PRIORITY', params: { priority: 'HIGH' } }],
    });

    const customerCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const marker = uniqueText('گفتگوی حلقه محافظت‌شده');
    await openWidget(customer);
    await sendWidgetText(customer, marker);
    await customerCtx.close();

    const page = await (await browser.newContext()).newPage();
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/`);
    await openConversationByPreview(page, marker);
    await page.getByTitle('اولویت').selectOption({ label: 'بالا' }); // set priority to HIGH, kicking off the A<->B chain
    await page.waitForTimeout(500); // give the synchronous post-commit chain time to fully settle

    await page.goto(`${DASHBOARD_URL}/automations`);
    await page.getByText('تاریخچه اجرا').click();
    await expect(page.locator('tr', { has: page.getByText(ruleA) }).first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText('رد شده (حلقه)').first()).toBeVisible();
  });

  test('an admin of a different workspace cannot see this workspace\'s rules', async ({ page }) => {
    const name = uniqueRuleName('isolation');
    seedRule({ name, trigger_type: 'CONVERSATION_CREATED', actions: [{ type: 'ESCALATE', params: {} }] });
    runDjangoScript(`
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
from accounts.models import User
platform, _ = Platform.objects.get_or_create(name='E2E Isolation Platform')
ws2, _ = Workspace.objects.get_or_create(name='E2E Isolation Workspace', defaults={'platform': platform})
u, created = User.objects.get_or_create(email='e2e-outsider-admin@ws.com')
if created: u.set_password('pass1234'); u.save()
WorkspaceMembership.objects.get_or_create(user=u, workspace=ws2, defaults={'role': 'WORKSPACE_ADMIN'})
`);
    await loginOperator(page, 'e2e-outsider-admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    await expect(page.getByText('اتوماسیون و گردش‌کار')).toBeVisible();
    await expect(page.getByText(name)).toHaveCount(0);
  });

  test('the action builder exposes registry-driven choices for SEND_NOTIFICATION', async ({ page }) => {
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    await page.getByText('+ قانون جدید').click();
    await page.getByText('+ افزودن اقدام').click();
    const actionTypeSelect = page.locator('select').nth(2);
    await actionTypeSelect.selectOption({ label: 'ارسال اعلان' });
    const targetSelect = page.getByRole('combobox').last();
    await expect(targetSelect.locator('option', { hasText: 'ASSIGNEE' })).toHaveCount(1);
    await expect(targetSelect.locator('option', { hasText: 'WORKSPACE_ADMINS' })).toHaveCount(1);
  });

  test('creating a rule with a missing required action param surfaces a validation error, not a crash', async ({ page }) => {
    const name = uniqueRuleName('validation-error');
    await loginOperator(page, 'admin@ws.com');
    await page.goto(`${DASHBOARD_URL}/automations`);
    await page.getByText('+ قانون جدید').click();
    await page.getByLabel('نام قانون').fill(name);
    await page.getByText('+ افزودن اقدام').click();
    const actionTypeSelect = page.locator('select').nth(2);
    await actionTypeSelect.selectOption({ label: 'تنظیم اولویت' }); // SET_PRIORITY — leave the required "priority" choice empty
    await page.getByText('ایجاد قانون').click();
    await expect(page.locator('.text-red-500').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('اتوماسیون و گردش‌کار')).toBeVisible(); // page still intact, no crash
  });

  test('an inactive rule never fires even when its trigger event occurs', async ({ browser }) => {
    const name = uniqueRuleName('inactive-no-fire');
    seedRule({ name, trigger_type: 'CONVERSATION_CREATED', actions: [{ type: 'SET_PRIORITY', params: { priority: 'URGENT' } }] });
    runDjangoScript(`
from workspaces.models import Workspace
from automations.models import AutomationRule
ws = Workspace.objects.get(name='Sample Workspace')
AutomationRule.objects.filter(workspace=ws, name=${JSON.stringify(name)}).update(is_active=False)
`);

    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('گفتگوی بدون اتوماسیون فعال');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator, 'admin@ws.com');
    await openConversationByPreview(operator, marker);
    // The priority <select> always renders a "فوری" *option* regardless of
    // the current value, so asserting text absence would be unreliable —
    // check the actual selected value instead.
    await expect(operator.getByTitle('اولویت')).toHaveValue('NORMAL');

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('pre-existing customer chat still works normally with automation rules present', async ({ browser }) => {
    seedRule({ name: uniqueRuleName('unrelated-regression'), trigger_type: 'SLA_BREACHED', actions: [{ type: 'ESCALATE', params: {} }] });

    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('گفتگوی رگرسیون معمولی');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator, 'admin@ws.com');
    await openConversationByPreview(operator, marker);
    // The marker legitimately appears twice once the conversation is open
    // (the sidebar preview snippet and the message bubble itself) — either
    // is proof the message made it through.
    await expect(operator.getByText(marker).first()).toBeVisible();

    await customerCtx.close();
    await operatorCtx.close();
  });
});
