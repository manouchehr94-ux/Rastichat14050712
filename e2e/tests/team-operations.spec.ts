import { test, expect } from '@playwright/test';
import {
  openWidget, sendWidgetText, loginOperator, loginPlatformSupport, openConversationByPreview, fixture,
  runDjangoScript, runEvaluateSla, findConversationIdByMessageContent, forceSlaFirstResponseState,
  DASHBOARD_URL, PLATFORM_DASHBOARD_URL,
} from './helpers';

function uniqueText(label: string) {
  return `${label} ${Date.now()}-${Math.floor(Math.random() * 10000)}`;
}

test.describe('Team operations, queues and SLA', () => {
  test.beforeAll(() => {
    // Give both seeded operators generous headroom before this suite starts.
    // Conversations created by this spec (and by other spec files that may
    // run first, e.g. customer-operator-flow.spec.ts) are never closed, so
    // active_conversation_count accumulates across a full test run; without
    // this, claim/auto-assign tests can spuriously hit the default
    // max_capacity=10 ceiling well before the suite finishes.
    // get_or_create, not `u.presence` — on a genuinely fresh database
    // (drop/recreate/migrate/seed, no prior login/WS activity for these
    // users) OperatorPresence rows don't exist yet, and the direct reverse
    // accessor raises RelatedObjectDoesNotExist. Real seeded users on a
    // real fresh DB must not depend on incidental prior activity.
    runDjangoScript(`
from accounts.models import User, OperatorPresence
for email in ['operator@ws.com', 'operator2@ws.com']:
    u = User.objects.get(email=email)
    p, _ = OperatorPresence.objects.get_or_create(user=u, defaults={'status': OperatorPresence.Status.OFFLINE})
    p.max_capacity = 50
    p.save(update_fields=['max_capacity'])
`);
  });

  test('new customer conversation enters the workspace queue unassigned', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('گفتگوی صف جدید');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator);
    await openConversationByPreview(operator, marker);
    await expect(operator.getByText('واگذارنشده').first()).toBeVisible();

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('an agent claims an unassigned conversation from the queue', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('گفتگوی برداشت');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator);
    await openConversationByPreview(operator, marker);
    await operator.getByText('برداشتن').click();
    await expect(operator.getByText('برداشتن')).toHaveCount(0);

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('a second agent cannot claim a conversation the first agent already claimed', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const op1Ctx = await browser.newContext();
    const op2Ctx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const op1 = await op1Ctx.newPage();
    const op2 = await op2Ctx.newPage();

    const marker = uniqueText('گفتگوی رقابت برداشت');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(op1, 'operator@ws.com');
    await loginOperator(op2, 'operator2@ws.com');
    await openConversationByPreview(op1, marker);
    await openConversationByPreview(op2, marker);

    // Both agents see the same still-unassigned conversation with a claim
    // button rendered; firing both clicks together exercises the real
    // concurrency path (each click is its own HTTP request against the
    // select_for_update()-guarded claim endpoint).
    await Promise.all([
      op1.getByText('برداشتن').click(),
      op2.getByText('برداشتن').click(),
    ]);

    // Exactly one of the two agents ends up owning it; the loser sees an
    // error toast rather than silently overwriting the winner.
    const op1Lost = await op1.getByText(/already assigned/i).isVisible().catch(() => false);
    const op2Lost = await op2.getByText(/already assigned/i).isVisible().catch(() => false);
    expect(op1Lost !== op2Lost).toBe(true);

    await customerCtx.close();
    await op1Ctx.close();
    await op2Ctx.close();
  });

  test('a supervisor transfers a conversation to a different team', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('گفتگوی انتقال');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator, 'operator2@ws.com'); // seeded as team SUPERVISOR
    await openConversationByPreview(operator, marker);
    await operator.getByText('انتقال تیم').click();
    await operator.getByText('فنی', { exact: true }).click();
    await expect(operator.getByText('• تیم فنی')).toBeVisible({ timeout: 10000 });

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('an internal note is visible to the operator but never reaches the customer', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('گفتگوی یادداشت داخلی');
    const noteText = uniqueText('یادداشت محرمانه');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator);
    await openConversationByPreview(operator, marker);
    await operator.getByText('💬 پاسخ به مشتری (کلیک برای یادداشت داخلی)').click();
    await operator.getByPlaceholder('یادداشت داخلی (فقط برای همکاران)…').fill(noteText);
    await operator.getByText('➤').click();

    await expect(operator.getByText(noteText)).toBeVisible({ timeout: 10000 });
    await expect(operator.getByText('🔒 یادداشت داخلی')).toBeVisible();

    // Give any (incorrect) realtime delivery a moment to arrive, then assert
    // the customer widget never rendered it.
    await customer.waitForTimeout(1500);
    await expect(customer.getByText(noteText)).toHaveCount(0);

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('mentioning a colleague in an internal note creates a notification for them', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const op1Ctx = await browser.newContext();
    const op2Ctx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const op1 = await op1Ctx.newPage();
    const op2 = await op2Ctx.newPage();

    const marker = uniqueText('گفتگوی اشاره');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(op1, 'operator@ws.com');
    await loginOperator(op2, 'operator2@ws.com'); // establishes op2's notification WS connection

    await openConversationByPreview(op1, marker);
    await op1.getByText('💬 پاسخ به مشتری (کلیک برای یادداشت داخلی)').click();
    await op1.getByTitle('اشاره به همکار').click();
    await op1.getByText('@همکار دو').click();
    await op1.getByPlaceholder('یادداشت داخلی (فقط برای همکاران)…').fill('لطفا بررسی کن');
    await op1.getByText('➤').click();

    await expect(op2.getByTitle('اعلان‌ها').locator('span')).toBeVisible({ timeout: 15000 });
    await op2.getByTitle('اعلان‌ها').click();
    await expect(op2.getByText('در یک یادداشت داخلی به شما اشاره شد').first()).toBeVisible();

    await customerCtx.close();
    await op1Ctx.close();
    await op2Ctx.close();
  });

  test('operator raises the conversation priority to urgent', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('گفتگوی اولویت');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator);
    await openConversationByPreview(operator, marker);
    await operator.getByTitle('اولویت').selectOption('URGENT');
    await expect(operator.getByTitle('اولویت')).toHaveValue('URGENT');

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('an approaching SLA state appears on the conversation', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('گفتگوی نزدیک به موعد');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    const convId = findConversationIdByMessageContent(marker);
    forceSlaFirstResponseState(convId, 'approaching');
    runEvaluateSla();

    await loginOperator(operator);
    await openConversationByPreview(operator, marker);
    await expect(operator.getByText(/تا موعد/)).toBeVisible({ timeout: 10000 });

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('an SLA breach appears after the deterministic fixture and evaluator run', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('گفتگوی نقض SLA');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    const convId = findConversationIdByMessageContent(marker);
    forceSlaFirstResponseState(convId, 'breached');
    runEvaluateSla();

    await loginOperator(operator);
    await openConversationByPreview(operator, marker);
    await expect(operator.getByText('• نقض SLA')).toBeVisible({ timeout: 10000 });

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('an agent at capacity is skipped by automatic assignment', async ({ browser }) => {
    // A dedicated, high-priority-routing LEAST_ACTIVE queue for this test only —
    // created here (not in seed data) so it never affects the MANUAL-queue
    // routing the earlier tests depend on.
    runDjangoScript(`
from workspaces.models import Workspace
from teams.models import Team
from queues.models import Queue
from accounts.models import User
from accounts.presence import touch_presence
ws = Workspace.objects.get(name='Sample Workspace')
team = Team.objects.get(workspace=ws, name='فروش')
Queue.objects.get_or_create(
    workspace=ws, name='صف خودکار تست', defaults={
        'team': team, 'assignment_strategy': Queue.Strategy.LEAST_ACTIVE, 'routing_priority': 1,
    },
)
op1 = User.objects.get(email='operator@ws.com')
presence = touch_presence(op1, explicit_status='ONLINE')
presence.max_capacity = 0
presence.save(update_fields=['max_capacity'])
op2 = User.objects.get(email='operator2@ws.com')
touch_presence(op2, explicit_status='ONLINE')
`);

    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('گفتگوی ظرفیت کامل');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator, 'operator2@ws.com');
    await openConversationByPreview(operator, marker);
    await expect(operator.getByTitle('واگذاری به همکار')).toHaveValue(/.+/, { timeout: 10000 });
    const assignedLabel = await operator.getByTitle('واگذاری به همکار').inputValue();
    expect(assignedLabel).not.toBe('');

    // Restore operator1's capacity (back to this suite's baseline, not the
    // model default) and remove the throwaway high-priority queue so
    // neither leaks into later tests' routing/claim assumptions.
    runDjangoScript(`
from accounts.models import User, OperatorPresence
from workspaces.models import Workspace
from queues.models import Queue
op1 = User.objects.get(email='operator@ws.com')
presence, _ = OperatorPresence.objects.get_or_create(user=op1, defaults={'status': OperatorPresence.Status.OFFLINE})
presence.max_capacity = 50
presence.save(update_fields=['max_capacity'])
ws = Workspace.objects.get(name='Sample Workspace')
Queue.objects.filter(workspace=ws, name='صف خودکار تست').delete()
`);

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('operator selects a managed quick reply and sends it', async ({ browser }) => {
    runDjangoScript(`
from workspaces.models import Workspace
from collaboration.models import QuickReply
ws = Workspace.objects.get(name='Sample Workspace')
QuickReply.objects.get_or_create(
    workspace=ws, scope=QuickReply.Scope.WORKSPACE, title='خوش‌آمدگویی E2E',
    defaults={'body': 'سلام، چطور می‌تونم کمکتون کنم؟'},
)
`);

    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('گفتگوی پاسخ آماده');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator);
    await openConversationByPreview(operator, marker);
    await operator.getByText('خوش‌آمدگویی E2E').click();
    const input = operator.getByPlaceholder('پاسخ به مشتری…');
    await expect(input).toHaveValue(/سلام/);
    await operator.getByText('➤').click();

    await expect(customer.locator('.rasti-bubble', { hasText: 'سلام، چطور می‌تونم کمکتون کنم؟' })).toBeVisible({ timeout: 10000 });

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('tenant isolation: workspace A cannot see workspace B teams, queues, or notifications', async ({ browser }) => {
    runDjangoScript(`
from platforms.models import Platform
from workspaces.models import Workspace, WorkspaceMembership
from accounts.models import User
from teams.models import Team
from queues.models import Queue
from notifications.models import Notification
platform_b, _ = Platform.objects.get_or_create(name='Other Platform E2E')
ws_b, _ = Workspace.objects.get_or_create(name='Other Workspace E2E', defaults={'platform': platform_b})
user_b, created = User.objects.get_or_create(email='outsider-e2e@ws.com', defaults={'is_staff': True})
if created:
    user_b.set_password('pass1234'); user_b.save()
WorkspaceMembership.objects.get_or_create(user=user_b, workspace=ws_b, defaults={'role': 'WORKSPACE_OWNER'})
Team.objects.get_or_create(workspace=ws_b, name='تیم مخفی')
Queue.objects.get_or_create(workspace=ws_b, name='صف مخفی', defaults={'team': Team.objects.get(workspace=ws_b, name='تیم مخفی')})
Notification.objects.create(recipient=user_b, workspace=ws_b, event_type='MENTIONED', title='اعلان مخفی برای B')
`);

    const operatorCtx = await browser.newContext();
    const operator = await operatorCtx.newPage();
    await loginOperator(operator);

    await operator.reload();
    await expect(operator.getByText('تیم مخفی')).toHaveCount(0);
    await expect(operator.getByText('صف مخفی')).toHaveCount(0);

    await operator.getByTitle('فیلترها').click();
    const teamOptions = await operator.locator('select').nth(1).locator('option').allTextContents();
    expect(teamOptions).not.toContain('تیم مخفی');
    const queueOptions = await operator.locator('select').nth(0).locator('option').allTextContents();
    expect(queueOptions).not.toContain('صف مخفی');

    await operator.getByTitle('اعلان‌ها').click();
    await expect(operator.getByText('اعلان مخفی برای B')).toHaveCount(0);

    await operatorCtx.close();
  });

  test('mobile operator claims and transfers a conversation', async ({ browser }) => {
    const customerCtx = await browser.newContext();
    const operatorCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const customer = await customerCtx.newPage();
    const operator = await operatorCtx.newPage();

    const marker = uniqueText('گفتگوی موبایل سرپرستی');
    await openWidget(customer);
    await sendWidgetText(customer, marker);

    await loginOperator(operator);
    await openConversationByPreview(operator, marker);

    await operator.getByText('برداشتن').click();
    await expect(operator.getByText('برداشتن')).toHaveCount(0);

    await operator.getByText('انتقال تیم').click();
    await operator.getByText('فنی', { exact: true }).click();
    await expect(operator.getByText('• تیم فنی')).toBeVisible({ timeout: 10000 });

    // Mobile-only back control still works after these actions.
    await operator.getByText('›').click();
    await expect(operator.getByText('گفتگوهای مشتریان')).toBeVisible();

    await customerCtx.close();
    await operatorCtx.close();
  });

  test('platform support: workspace admin opens a ticket, platform agent replies', async ({ browser }) => {
    const adminCtx = await browser.newContext();
    const supportCtx = await browser.newContext();
    const admin = await adminCtx.newPage();
    const support = await supportCtx.newPage();

    const subject = uniqueText('تیکت پلتفرم E2E');
    await loginOperator(admin, 'admin@ws.com');
    await admin.goto(`${DASHBOARD_URL}/support`);
    await admin.getByRole('button', { name: 'تیکت جدید' }).click();
    await admin.getByPlaceholder('موضوع').fill(subject);
    await admin.getByPlaceholder('پیام اولیه').fill('لطفا کمک کنید');
    await admin.getByText('ارسال درخواست').click();
    await expect(admin.getByText(subject)).toBeVisible({ timeout: 10000 });

    await loginPlatformSupport(support);
    await support.reload();
    // The Next.js dev-mode tooling overlay is a fixed bottom-left badge; in
    // this RTL layout the reply "ارسال" button also renders on the visual
    // left, so the overlay sits on top of it and swallows real clicks. Hide
    // it outright rather than forcing clicks through to whatever is on top.
    await support.addStyleTag({ content: 'nextjs-portal { display: none !important; }' });
    const ticketRow = support.getByText(subject, { exact: true }).first();
    await ticketRow.waitFor({ timeout: 15000 });
    await ticketRow.click();

    // handleAssign() awaits the assign API call before firing a blocking
    // native alert(); wait for the dialog itself (not just the click) so a
    // later interaction can't race the alert while it's still blocking the
    // page's JS thread.
    const dialogPromise = support.waitForEvent('dialog');
    await support.getByText('تخصیص به من').click();
    (await dialogPromise).accept();

    const replyText = uniqueText('پاسخ پشتیبانی پلتفرم');
    await support.locator('input[placeholder="پاسخ..."]').fill(replyText);
    await support.getByText('ارسال', { exact: true }).click();
    await expect(support.getByText(replyText)).toBeVisible({ timeout: 10000 });

    await adminCtx.close();
    await supportCtx.close();
  });
});
