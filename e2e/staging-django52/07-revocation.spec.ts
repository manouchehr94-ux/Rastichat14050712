import { test, expect } from '@playwright/test';
import { ORIGINS, admin, loginApi, openSocket, seed, staffTicket, widgetHistory, widgetTicket, wsUrl, withTimeout } from './wsclient';

// Protocol-level checks (real nginx, real WebSockets, real server-side state changes): they do not depend on the browser, so
// they run once, under the desktop project.
test.describe.configure({ mode: 'serial' });
test.beforeEach(({}, testInfo) => { test.skip(testInfo.project.name !== 'desktop-chromium', 'protocol-level spec: runs once'); });

const REVALIDATE_SLACK_MS = 20000; // WS_REVALIDATE_SECONDS=5 on the stack, plus scheduling slack
const s = () => seed();

async function staffSocket(jwt: string, conv: string) {
  const { status, ticket } = await staffTicket(jwt, 'dashboard_chat', conv);
  expect(status, 'ticket for an authorized staff member').toBe(201);
  const sock = openSocket(wsUrl(`/v2/dashboard/${conv}/`), { origin: ORIGINS.operator, ticket });
  await withTimeout(sock.authed, 15000, 'auth.ok');
  return sock;
}

test.describe('revocation while connected, roles, tenants, expiry (real WebSockets through nginx)', () => {
  test('a user who is inactive cannot log in', async () => {
    const u = s().users['inactive-a'];
    await expect(loginApi(u.email, u.password, true)).rejects.toThrow(/401/);
  });

  test('deactivating a connected user closes the socket with 4403 within the revalidation window; the old JWT then gets no ticket', async () => {
    const u = s().users['victim-a']; const conv = s().A.conversation;
    const jwt = await loginApi(u.email, u.password);
    const a = await staffSocket(jwt, conv);
    const b = await staffSocket(jwt, conv); // two concurrent connections of the same user: both must be cut
    try {
      admin('deactivate-user', u.email);
      expect(await withTimeout(a.closed, REVALIDATE_SLACK_MS, 'socket A closed')).toBe(4403);
      expect(await withTimeout(b.closed, REVALIDATE_SLACK_MS, 'socket B closed')).toBe(4403);
      const again = await staffTicket(jwt, 'dashboard_chat', conv); // the JWT itself has not expired
      expect([401, 403, 404]).toContain(again.status);
    } finally { admin('activate-user', u.email); }
    // after reactivation a new ticket works again (reconnect with a fresh ticket)
    const back = await staffSocket(await loginApi(u.email, u.password), conv);
    back.ws.close();
  });

  test('removing a membership closes the socket and cuts the ticket (uniform 404); nothing is delivered afterwards', async () => {
    const u = s().users['victim-a']; const conv = s().A.conversation;
    const jwt = await loginApi(u.email, u.password);
    const sock = await staffSocket(jwt, conv);
    try {
      admin('remove-membership', u.email);
      expect(await withTimeout(sock.closed, REVALIDATE_SLACK_MS, 'socket closed')).toBe(4403);
      const framesAtClose = sock.frames.length;
      expect((await staffTicket(jwt, 'dashboard_chat', conv)).status).toBe(404);
      expect(sock.frames.length).toBe(framesAtClose);
    } finally { admin('restore-membership', u.email); }
  });

  test('another store cannot reach this conversation (uniform 404), its own conversation works; wrong kind is refused', async () => {
    const adminB = s().users['admin-b']; const jwtB = await loginApi(adminB.email, adminB.password);
    expect((await staffTicket(jwtB, 'dashboard_chat', s().A.conversation)).status).toBe(404);
    expect((await staffTicket(jwtB, 'dashboard_chat', s().B.conversation)).status).toBe(201);
    expect((await staffTicket(jwtB, 'support', s().support_conversation_a)).status).toBe(404);
    expect((await staffTicket(jwtB, 'not-a-kind', s().B.conversation)).status).toBeGreaterThanOrEqual(400);
    const unauth = await fetch(`${process.env.SMOKE_BACKEND_URL}/api/v1/ws/ticket/`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    expect(unauth.status).toBe(401);
  });

  test('a ticket is single-use and bound to its conversation; a replay or a different conversation is refused (4401)', async () => {
    const u = s().users['agent-a1']; const jwt = await loginApi(u.email, u.password);
    const { ticket } = await staffTicket(jwt, 'dashboard_chat', s().A.conversation);
    const first = openSocket(wsUrl(`/v2/dashboard/${s().A.conversation}/`), { origin: ORIGINS.operator, ticket });
    await withTimeout(first.authed, 15000, 'first use');
    const replay = openSocket(wsUrl(`/v2/dashboard/${s().A.conversation}/`), { origin: ORIGINS.operator, ticket });
    expect(await withTimeout(replay.closed, 15000, 'replay closed')).toBe(4401);
    first.ws.close();
    const other = (await staffTicket(jwt, 'dashboard_chat', s().A.conversation)).ticket;
    const wrongRoute = openSocket(wsUrl(`/v2/dashboard/${s().B.conversation}/`), { origin: ORIGINS.operator, ticket: other });
    expect(await withTimeout(wrongRoute.closed, 15000, 'cross-conversation closed')).toBe(4401);
  });

  test('an expired ticket is refused (4401); a socket that never authenticates is closed by the auth timeout', async () => {
    const u = s().users['agent-a1']; const jwt = await loginApi(u.email, u.password);
    const { ticket } = await staffTicket(jwt, 'dashboard_chat', s().A.conversation);
    await new Promise((r) => setTimeout(r, 33000)); // WS_TICKET_TTL_SECONDS=30
    const late = openSocket(wsUrl(`/v2/dashboard/${s().A.conversation}/`), { origin: ORIGINS.operator, ticket });
    expect(await withTimeout(late.closed, 15000, 'expired ticket closed')).toBe(4401);
    const silent = openSocket(wsUrl(`/v2/dashboard/${s().A.conversation}/`), { origin: ORIGINS.operator });
    expect(await withTimeout(silent.closed, 20000, 'silent socket closed')).toBe(4401); // WS_AUTH_TIMEOUT_SECONDS=10
    expect(silent.frames).toEqual([]); // and it received nothing in between
  });

  test('credentials in the URL are refused on every legacy route (staging default: legacy off)', async () => {
    const u = s().users['agent-a1']; const jwt = await loginApi(u.email, u.password);
    for (const path of [
      `/dashboard/${jwt}/${s().A.conversation}/`, `/notifications/${jwt}/`, `/dashboard/support/${jwt}/${s().support_conversation_a}/`,
      `/widget/${s().A.session_token}/${s().A.conversation}/`,
    ]) {
      const sock = openSocket(wsUrl(path), { origin: path.startsWith('/widget') ? ORIGINS.allowedEmbed : ORIGINS.operator });
      expect(await withTimeout(sock.closed, 15000, path.split('/')[1]), path.split('/')[1]).toBeGreaterThanOrEqual(1000);
      expect(sock.frames).toEqual([]);
    }
    const q = await fetch(`${process.env.SMOKE_BACKEND_URL}/api/v1/widget/conversations/${s().A.conversation}/messages/?session_token=${s().A.session_token}`, { headers: { Origin: ORIGINS.allowedEmbed } });
    expect(q.status).toBe(401); // a query-string credential is ignored
  });

  test('customer session: revoke / expiry / project deactivation cut the live socket and the history (REST) at once; restoring works', async () => {
    const A = s().A; const token = A.session_token;
    const connect = async () => {
      const { status, ticket } = await widgetTicket(token, A.conversation);
      expect(status).toBe(201);
      const sock = openSocket(wsUrl(`/v2/widget/${A.conversation}/`), { origin: ORIGINS.allowedEmbed, ticket });
      await withTimeout(sock.authed, 15000, 'widget auth.ok');
      return sock;
    };
    expect(await widgetHistory(token, A.conversation)).toBe(200);
    // 1) revoked
    let sock = await connect();
    admin('revoke-visitor-session', token);
    expect(await withTimeout(sock.closed, REVALIDATE_SLACK_MS, 'revoked socket closed')).toBe(4403);
    expect(await widgetHistory(token, A.conversation)).toBe(401);
    expect((await widgetTicket(token, A.conversation)).status).toBeGreaterThanOrEqual(400);
    admin('restore-visitor-session', token);
    // 2) expired
    sock = await connect();
    admin('expire-visitor-session', token);
    expect(await withTimeout(sock.closed, REVALIDATE_SLACK_MS, 'expired socket closed')).toBe(4403);
    expect(await widgetHistory(token, A.conversation)).toBe(401);
    admin('restore-visitor-session', token);
    // 3) project deactivated
    sock = await connect();
    admin('deactivate-project', A.project_key);
    try {
      expect(await withTimeout(sock.closed, REVALIDATE_SLACK_MS, 'inactive-project socket closed')).toBe(4403);
      expect(await widgetHistory(token, A.conversation)).toBeGreaterThanOrEqual(400);
    } finally { admin('activate-project', A.project_key); }
    expect(await widgetHistory(token, A.conversation)).toBe(200);
    // 4) another store's (valid) session cannot read this conversation: indistinguishable from a conversation that does not exist
    expect(await widgetHistory(s().B.session_token, A.conversation)).toBe(404);
  });
});
