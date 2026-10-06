import { readFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';
import { BACKEND_URL, ALLOWED_EMBED_ORIGIN, OPERATOR_PASSWORD } from './env';
import { dj, netDown, netUp, staffToken, visitor, widgetSocket, dashboardSocket, ORIGIN } from './harness';
import { authFrames, captureSockets, loginOperator, openWidgetAt, sendWidgetText, serveEmbedAt, uniqueText } from './helpers';

// Rows 9 (revocation while connected), 10 (multi-worker fan-out) and the dashboards' reconnect (PR #18) on the real stack.
test.skip(!process.env.STG_DJ || !process.env.STG_BALANCER_PIDFILE, 'needs the sandbox hooks STG_DJ (Django shell) and STG_BALANCER_PIDFILE');

test.describe('live sockets on the real stack', () => {
  test.skip(!ALLOWED_EMBED_ORIGIN, 'needs DJANGO52_ALLOWED_EMBED_ORIGIN');

  for (const mode of ['deactivate', 'membership'] as const) {
    test(`revocation while connected (${mode}): idle dashboard socket (and notifications on deactivation) close with 4403, nothing more is delivered, new tickets are refused`, async ({ request }) => {
      test.setTimeout(150000);
      const email = 'agent-a2@example.test';
      const jwt = await staffToken(request, email);
      const v = await visitor(request);
      const chat = await dashboardSocket(request, jwt, 'dashboard_chat', v.convId);
      const notif = await dashboardSocket(request, jwt, 'notifications');
      const t0 = Date.now();
      try {
        dj(mode === 'deactivate'
          ? `from accounts.models import User\nUser.objects.filter(email="${email}").update(is_active=False)`
          : `from workspaces.models import WorkspaceMembership as M\nM.objects.filter(user__email="${email}").delete()`);
        // the notifications socket is authorized by "user is active" only (per-user stream, by design), so removing a workspace
        // membership must close the conversation socket and leave the notifications socket open; deactivation closes both
        const waits = mode === 'deactivate' ? [chat.closed, notif.closed] : [chat.closed];
        const codes = await Promise.race([Promise.all(waits), new Promise<never>((_, rej) => setTimeout(() => rej(new Error('sockets not closed within 75s')), 75000))]);
        console.log(`[revocation ${mode}] closed ${codes} after ${(Math.max(...waits.map(() => 0), chat.closedAt!, mode === 'deactivate' ? notif.closedAt! : 0) - t0) / 1000}s`);
        expect(codes).toEqual(mode === 'deactivate' ? [4403, 4403] : [4403]);
        if (mode === 'membership') expect(notif.ws.readyState).toBe(1);
        // a new ticket for a user without access is refused (the still-valid JWT is not enough)
        const t = await request.post(`${BACKEND_URL}/api/v1/ws/ticket/`, { data: { kind: 'dashboard_chat', conversation_id: v.convId }, headers: { Authorization: `Bearer ${jwt}` } });
        expect([401, 403, 404]).toContain(t.status());
      } finally {
        dj(`from accounts.models import User\nfrom workspaces.models import Workspace, WorkspaceMembership as M\nu=User.objects.get(email="${email}")\nu.is_active=True\nu.save()\nM.objects.get_or_create(user=u, workspace=Workspace.objects.get(name="ws-a"), defaults={"role":"WORKSPACE_OPERATOR"})`);
        chat.ws.terminate(); notif.ws.terminate();
      }
    });
  }

  test('multi-worker: sockets spread over both Daphne workers and an operator reply reaches every one of them', async ({ request }) => {
    const jwt = await staffToken(request, 'agent-a1@example.test');
    const count = (port: number) => (readFileSync(`${process.env.STG_LOGS}/daphne-${port}.log`, 'utf8').match(/WSCONNECT/g) || []).length;
    const before = [count(8101), count(8102)];
    const visitors = [] as Awaited<ReturnType<typeof visitor>>[];
    const socks = [] as Awaited<ReturnType<typeof widgetSocket>>[];
    // the layer-4 balancer picks a worker at random per connection: open 8 sockets, then keep adding (bounded) until BOTH workers have
    // taken at least one — "all eight landed on one worker" (0.8 %) must not fail a run that is about fan-out across workers
    const spread = () => { const a = [count(8101), count(8102)]; return a[0] - before[0] > 0 && a[1] - before[1] > 0; };
    for (let i = 0; i < 8 || (!spread() && i < 40); i++) { const v = await visitor(request); visitors.push(v); socks.push(await widgetSocket(request, v)); }
    const after = [count(8101), count(8102)];
    console.log(`[multiworker] new WS connections per worker: 8101 +${after[0] - before[0]}, 8102 +${after[1] - before[1]}`);
    expect(after[0] - before[0]).toBeGreaterThan(0);
    expect(after[1] - before[1]).toBeGreaterThan(0);
    for (let i = 0; i < visitors.length; i++) {
      const text = `fanout-${i}-${Date.now()}`;
      const r = await request.post(`${BACKEND_URL}/api/v1/conversations/${visitors[i].convId}/send/`, { data: { content: text, client_message_id: `op-${i}-${Date.now()}` }, headers: { Authorization: `Bearer ${jwt}` } });
      expect(r.status(), await r.text()).toBeLessThan(300);
      await expect.poll(() => socks[i].frames.some((f) => f && f.content === text), { timeout: 15000, message: `visitor ${i} did not receive the reply` }).toBe(true);
    }
    socks.forEach((s) => s.ws.close());
  });

  test('dashboard reconnect: after a network drop the operator dashboard reconnects with a fresh ticket and resyncs the message it missed', async ({ browser }) => {
    test.setTimeout(120000);
    const cctx = await browser.newContext(); const octx = await browser.newContext();
    await serveEmbedAt(cctx, ORIGIN);
    const customer = await cctx.newPage(); const operator = await octx.newPage();
    const opSockets = captureSockets(operator);
    await openWidgetAt(customer, ORIGIN);
    const first = uniqueText('DJ52-dash-before');
    await sendWidgetText(customer, first);
    await loginOperator(operator);
    await operator.getByText(first).last().click();
    await expect(operator.getByText(first).last()).toBeVisible({ timeout: 20000 });
    await expect.poll(() => opSockets.filter((s) => s.url.includes('/v2/dashboard/') && s.received.some((f) => f.includes('auth.ok'))).length, { timeout: 20000 }).toBeGreaterThanOrEqual(1);
    const ticketsBefore = new Set(opSockets.flatMap((s) => authFrames(s).map((f) => f.ticket)));

    netDown();
    await expect.poll(() => opSockets.filter((s) => s.closedAt).length, { timeout: 20000 }).toBeGreaterThanOrEqual(1);
    await new Promise((r) => setTimeout(r, 3000));
    netUp();
    // the customer's widget reconnects on its own too; wait for it, then write while the operator is still backing off or just back
    await expect(customer.locator('#rasti-offline-banner.show')).toBeHidden({ timeout: 40000 });
    const second = uniqueText('DJ52-dash-after');
    await sendWidgetText(customer, second);
    await expect(operator.getByText(second).last()).toBeVisible({ timeout: 60000 });
    const ticketsAfter = opSockets.flatMap((s) => authFrames(s).map((f) => f.ticket));
    const fresh = ticketsAfter.filter((t) => !ticketsBefore.has(t));
    expect(fresh.length).toBeGreaterThan(0); // single-use tickets: every reconnect minted a new one
    await cctx.close(); await octx.close();
  });
});
