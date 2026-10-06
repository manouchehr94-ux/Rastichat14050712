// Integration Contract v1 verified end-to-end through nginx TLS + 2 Daphne workers on the sandbox staging stack.
// Synthetic data only; every tenant id is unique per run, so repeated matrix runs never collide.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import crypto, { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { signJwt } from '../../examples/reference-host/lib/rastichat.mjs';

const WebSocket = createRequire(import.meta.url)('ws');
const E = process.env;
const BASE = E.SMOKE_BACKEND_URL, API = `${BASE}/api/v1`, WS = E.SMOKE_WS_URL;
const ORIGIN = E.DJANGO52_ALLOWED_EMBED_ORIGIN;
const OPERATOR_ORIGIN = new URL(E.SMOKE_OPERATOR_URL).origin;       // dashboards open their sockets from the operator dashboard origin                       // an allowed embedding origin of every tenant below
const INT = JSON.parse(readFileSync(E.STG_INTEGRATION_FILE, 'utf8'));
const HOST = { ...INT['stg-host'], pem: readFileSync(INT['stg-host'].private_key_file, 'utf8') };
const MAL = { ...INT['stg-mallory'], pem: readFileSync(INT['stg-mallory'].private_key_file, 'utf8') };
const RUN = Date.now().toString(36);
const TA = `ta-${RUN}`, TB = `tb-${RUN}`;
// staff ids are unique per run too (a staff account is global to the integration, so reusing one across runs would accumulate memberships)
const P_OP_A = `op-a-${RUN}`, P_ADMIN_A = `admin-a-${RUN}`, P_ADMIN_B = `admin-b-${RUN}`, P_OP_B = `op-b-${RUN}`, P_SHARED_PERSON = `shared-${RUN}`;
const b64 = (b) => Buffer.from(b).toString('base64url');
const now = () => Math.floor(Date.now() / 1000);

async function http(method, url, { body, headers = {} } = {}) {
  const res = await fetch(url, { method, body: body === undefined ? undefined : JSON.stringify(body),
    headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers } });
  const text = await res.text(); let json = null; try { json = JSON.parse(text); } catch { /* not json */ }
  return { status: res.status, json, headers: res.headers };
}
// server-to-server call as an integration (a NEW token per request, bound to method + path + body)
async function s2s(who, method, path, body, extra = {}) {
  const raw = body === undefined ? '' : JSON.stringify(body);
  const t = now();
  const token = signJwt({ iss: who.slug, sub: who.slug, aud: 'rastichat:api', iat: t, exp: t + 30, jti: randomUUID(), htm: method, htu: path,
    bh: b64(crypto.createHash('sha256').update(raw).digest()), ...extra }, who.pem, who.kid);
  return http(method, BASE + path, { body, headers: { Authorization: `Bearer ${token}`, ...(extra.headers || {}) } });
}
const claims = (over = {}) => ({ iss: HOST.slug, aud: 'rastichat:identity', sub: `c-${randomUUID().slice(0, 8)}`, actor: 'customer', tenant: TA,
  iat: now(), exp: now() + 60, jti: randomUUID(), ...over });
const mint = (over = {}, who = HOST) => signJwt(claims(over), who.pem, who.kid);
const customerExchange = (projectKey, assertion) => http('POST', `${API}/identity/customer/`,
  { body: { project_key: projectKey, assertion }, headers: { Origin: ORIGIN } });
const staffExchange = (assertion) => http('POST', `${API}/identity/staff/`, { body: { assertion } });
const dj = (code) => execFileSync(E.STG_DJ, { input: code, encoding: 'utf8' });
const bearer = (token) => ({ Authorization: `Bearer ${token}` });

async function openSocket(path, ticketRes, origin) {
  const ws = new WebSocket(`${WS}${path}`, { headers: origin ? { Origin: origin } : {} });
  const s = { ws, frames: [], closed: null };
  ws.on('message', (d) => { try { s.frames.push(JSON.parse(String(d))); } catch { /* ignore */ } });
  ws.on('close', (code) => { s.closed = code; });
  await new Promise((res, rej) => { ws.on('open', res); ws.on('error', (e) => rej(new Error(`${path}: ${e.message}`))); });
  ws.send(JSON.stringify({ type: 'auth', ticket: ticketRes }));
  s.until = async (pred, label, ms = 15000) => {
    const end = Date.now() + ms;
    while (Date.now() < end) { const f = s.frames.find(pred); if (f) return f; await new Promise((r) => setTimeout(r, 100)); }
    throw new Error(`timed out waiting for ${label}; frames=${JSON.stringify(s.frames).slice(0, 300)}`);
  };
  await s.until((f) => f.type === 'auth.ok', 'auth.ok');
  return s;
}
async function expectRefusedSocket(path, ticket, origin) {
  const ws = new WebSocket(`${WS}${path}`, { headers: origin ? { Origin: origin } : {} });
  const frames = []; let closed = false;
  ws.on('message', (d) => frames.push(String(d))); ws.on('close', () => { closed = true; }); ws.on('error', () => { closed = true; });
  await new Promise((res) => { ws.on('open', res); ws.on('error', res); });
  ws.send(JSON.stringify({ type: 'auth', ticket }));
  const end = Date.now() + 6000;
  while (!closed && Date.now() < end) await new Promise((r) => setTimeout(r, 100));
  expect(frames.some((f) => f.includes('auth.ok')), 'a used ticket must not authenticate').toBe(false);
  ws.close();
}
const closedWithin = async (s, ms) => { const end = Date.now() + ms; while (Date.now() < end) { if (s.closed !== null) return true; await new Promise((r) => setTimeout(r, 100)); } return false; };

const S = {};   // shared state of the serial run
test.describe.serial('Integration Contract v1 on the sandbox staging stack', () => {
  test('provisioning is idempotent through nginx and tenants are separate', async () => {
    const put = (id, name) => s2s(HOST, 'PUT', `/api/v1/integrations/tenants/${id}/`, { display_name: name, verified_domains: [new URL(ORIGIN).hostname] });
    const a1 = await put(TA, 'Tenant A'); const a2 = await put(TA, 'Tenant A'); const b = await put(TB, 'Tenant B');
    expect([a1.status, a2.status, b.status]).toEqual([201, 200, 201]);
    expect(a2.json.workspace_id).toBe(a1.json.workspace_id);
    expect(b.json.workspace_id).not.toBe(a1.json.workspace_id);
    S.keyA = a1.json.project_public_key; S.keyB = b.json.project_public_key; S.wsA = a1.json.workspace_id; S.wsB = b.json.workspace_id;
    for (const [t, who, role] of [[TA, P_OP_A, 'operator'], [TA, P_ADMIN_A, 'admin'], [TB, P_ADMIN_B, 'admin'], [TB, P_OP_B, 'operator']]) {
      expect((await s2s(HOST, 'PUT', `/api/v1/integrations/tenants/${t}/members/${who}/`, { role, display_name: who })).status).toBe(200);
    }
    expect((await s2s(HOST, 'PUT', '/api/v1/integrations/platform/members/plat-1/', { role: 'owner', display_name: 'Platform Owner' })).status).toBe(200);
  });

  test('assertion negatives: expired, replayed, wrong audience/issuer/integration, forged, tampered, alg confusion', async () => {
    const control = await customerExchange(S.keyA, mint({ sub: 'ctl' }));
    expect(control.status).toBe(200);
    expect(control.json.identity.verified).toBe(true);
    const refused = async (label, assertion, project = S.keyA, allowed = [401, 403]) => {
      const r = await customerExchange(project, assertion);
      expect(allowed, `${label} -> ${r.status} ${JSON.stringify(r.json)}`).toContain(r.status);
      expect(r.json?.session_token, label).toBeUndefined();
    };
    await refused('expired', mint({ iat: now() - 3600, exp: now() - 3000 }));
    await refused('lifetime beyond the cap', mint({ exp: now() + 3600 }));
    await refused('wrong audience (api token as identity)', mint({ aud: 'rastichat:api' }));
    await refused('wrong issuer', mint({ iss: 'stg-mallory' }));
    await refused('unknown kid', signJwt(claims(), HOST.pem, 'ick_doesnotexist'));
    await refused('mallory key claiming the host issuer', mint({}, { ...MAL, slug: HOST.slug }));
    const good = mint({ sub: 'tamper' }); await refused('tampered signature', good.slice(0, -4) + (good.endsWith('AAAA') ? 'BBBB' : 'AAAA'));
    const hs = [b64(JSON.stringify({ alg: 'HS256', typ: 'JWT', kid: HOST.kid })), b64(JSON.stringify(claims()))].join('.');
    await refused('HS256 forged with the public key as secret', `${hs}.${b64(crypto.createHmac('sha256', 'public-key-as-secret').update(hs).digest())}`);
    const none = [b64(JSON.stringify({ alg: 'none', kid: HOST.kid })), b64(JSON.stringify(claims()))].join('.') + '.';
    await refused('alg=none', none);
    const replay = mint({ sub: 'replay' });
    expect((await customerExchange(S.keyA, replay)).status).toBe(200);
    await refused('replayed (single use)', replay);
    await refused('forged tenant claim (tenant B asserted on project A)', mint({ tenant: TB }));
    await refused('another integration for this host\'s tenant', mint({ iss: MAL.slug }, MAL));
    await refused('unknown tenant', mint({ tenant: 'does-not-exist' }));
    await refused('unknown actor', mint({ actor: 'superuser' }), S.keyA, [400, 401, 403]);
    await refused('project that is not a tenant of this integration (bogus project key)', mint({}), randomUUID(), [400, 404]);
    // a staff assertion must carry a valid generic role; a customer assertion's "role" claim grants nothing
    const badRole = await staffExchange(mint({ actor: 'tenant_staff', role: 'superadmin', sub: 'x1' }));
    expect(badRole.status).toBe(400);
    const sneaky = await customerExchange(S.keyA, mint({ role: 'owner', sub: 'sneaky' }));
    expect(sneaky.status).toBe(200);
    const asDash = await http('GET', `${API}/conversations/customer/`, { headers: { 'X-Widget-Session': sneaky.json.session_token, ...bearer(sneaky.json.session_token) } });
    expect([401, 403]).toContain(asDash.status);                 // a visitor session is never a dashboard credential
    // browser-claimed external ids prove nothing: a guest who claims the verified customer's id is NOT that customer
    const guest = await http('POST', `${API}/widget/init/`, { body: { project_key: S.keyA, external_id: 'int:stg-host:ctl' }, headers: { Origin: ORIGIN } });
    expect(guest.status).toBe(200);
    expect(guest.json.visitor_id).not.toBe(control.json.visitor_id);
  });

  test('disabled integration: every endpoint refuses, then recovers when re-enabled', async () => {
    dj("from integrations.models import Integration; Integration.objects.filter(slug='stg-host').update(is_active=False)");
    try {
      const r1 = await customerExchange(S.keyA, mint({ sub: 'disabled-1' }));
      const r2 = await s2s(HOST, 'GET', '/api/v1/integrations/me/');
      const r3 = await staffExchange(mint({ actor: 'tenant_staff', role: 'operator', sub: P_OP_A }));
      for (const r of [r1, r2, r3]) expect([401, 403], JSON.stringify(r.json)).toContain(r.status);
    } finally {
      dj("from integrations.models import Integration; Integration.objects.filter(slug='stg-host').update(is_active=True)");
    }
    expect((await s2s(HOST, 'GET', '/api/v1/integrations/me/')).status).toBe(200);
    expect((await customerExchange(S.keyA, mint({ sub: 'disabled-2' }))).status).toBe(200);
  });

  test('customer -> own tenant only; realtime across both workers; operator reply; history; isolation from tenant B', async () => {
    const ctx = await s2s(HOST, 'PUT', `/api/v1/integrations/tenants/${TA}/contexts/cust-1/`, { profile: { tier: 'gold' }, context: { page: '/pricing' } });
    expect(ctx.status).toBe(200);
    const login = await customerExchange(S.keyA, mint({ sub: 'cust-1', name: 'Customer One' }));
    expect(login.status).toBe(200);
    const session = { 'X-Widget-Session': login.json.session_token, Origin: ORIGIN };
    const start = await http('POST', `${API}/widget/start/`, { body: {}, headers: session });
    expect(start.status).toBe(200);
    S.convA = start.json.id; S.custSession = session;
    const ticket = await http('POST', `${API}/widget/ws-ticket/`, { body: { conversation_id: S.convA }, headers: session });
    expect(ticket.status).toBe(201);
    const cust = await openSocket(`/v2/widget/${S.convA}/`, ticket.json.ticket, ORIGIN);
    await expectRefusedSocket(`/v2/widget/${S.convA}/`, ticket.json.ticket, ORIGIN);   // a ticket is single use: replaying it never authenticates

    const opLogin = await staffExchange(mint({ actor: 'tenant_staff', tenant: TA, role: 'operator', sub: P_OP_A, name: 'Op A' }));
    expect(opLogin.status).toBe(200);
    S.opA = opLogin.json.access;
    const list = await http('GET', `${API}/conversations/customer/`, { headers: bearer(S.opA) });
    expect(list.status).toBe(200);
    const rows = Array.isArray(list.json) ? list.json : list.json.results;
    expect(rows.map((c) => c.id)).toContain(S.convA);
    const panel = await http('GET', `${API}/conversations/customer/${S.convA}/customer-context/`, { headers: bearer(S.opA) });
    expect(panel.json.identity_verified).toBe(true);
    expect(panel.json.host_context.profile.tier).toBe('gold');

    const opTicket = await http('POST', `${API}/ws/ticket/`, { body: { kind: 'dashboard_chat', conversation_id: S.convA }, headers: bearer(S.opA) });
    expect(opTicket.status).toBe(201);
    const op = await openSocket(`/v2/dashboard/${S.convA}/`, opTicket.json.ticket, OPERATOR_ORIGIN);
    S.opSocket = op;
    cust.ws.send(JSON.stringify({ message: 'hello tenant A', client_message_id: `m-${RUN}` }));
    await op.until((f) => /hello tenant A/.test(f.content || ''), 'customer message on the operator socket (other worker)');
    const reply = await http('POST', `${API}/conversations/${S.convA}/send/`, { body: { content: 'reply from A', client_message_id: `r-${RUN}` }, headers: bearer(S.opA) });
    expect([200, 201]).toContain(reply.status);
    await cust.until((f) => f.sender_type === 'USER' && /reply from A/.test(f.content || ''), 'operator reply on the customer socket');

    // reconnect with a fresh ticket + history resync
    cust.ws.close();
    const t2 = await http('POST', `${API}/widget/ws-ticket/`, { body: { conversation_id: S.convA }, headers: session });
    const cust2 = await openSocket(`/v2/widget/${S.convA}/`, t2.json.ticket, ORIGIN);
    const hist = await http('GET', `${API}/widget/conversations/${S.convA}/messages/`, { headers: session });
    expect(hist.json.map((m) => m.content)).toEqual(expect.arrayContaining(['hello tenant A', 'reply from A']));
    S.custSocket = cust2;

    // tenant B: its customers and staff never see tenant A's conversation
    const bCust = await customerExchange(S.keyB, mint({ sub: 'cust-1', tenant: TB }));          // SAME external id, other tenant
    expect(bCust.status).toBe(200);
    expect(bCust.json.visitor_id).not.toBe(login.json.visitor_id);
    const cross = await http('GET', `${API}/widget/conversations/${S.convA}/messages/`, { headers: { 'X-Widget-Session': bCust.json.session_token, Origin: ORIGIN } });
    expect([401, 403, 404]).toContain(cross.status);
    const opB = await staffExchange(mint({ actor: 'tenant_staff', tenant: TB, role: 'operator', sub: P_OP_B }));
    const listB = await http('GET', `${API}/conversations/customer/`, { headers: bearer(opB.json.access) });
    expect((Array.isArray(listB.json) ? listB.json : listB.json.results).map((c) => c.id)).not.toContain(S.convA);
    for (const path of [`/conversations/${S.convA}/messages/`, `/conversations/customer/${S.convA}/customer-context/`]) {
      expect((await http('GET', `${API}${path}`, { headers: bearer(opB.json.access) })).status).toBe(404);
    }
    expect((await http('POST', `${API}/conversations/${S.convA}/send/`, { body: { content: 'x', client_message_id: 'z1' }, headers: bearer(opB.json.access) })).status).toBe(404);
  });

  test('multi-store person: per-tenant identities cannot cross; a shared global identity would (documented)', async () => {
    // namespaced subs (what the RastiSi adapter does): two accounts, each confined to its own tenant
    const a = await staffExchange(mint({ actor: 'tenant_staff', tenant: TA, role: 'admin', sub: 'u42.' + TA }));
    const b = await staffExchange(mint({ actor: 'tenant_staff', tenant: TB, role: 'admin', sub: 'u42.' + TB }));
    expect([a.status, b.status]).toEqual([200, 200]);
    const idsOf = async (tok) => { const r = await http('GET', `${API}/conversations/customer/`, { headers: bearer(tok) }); return (Array.isArray(r.json) ? r.json : r.json.results).map((c) => c.id); };
    expect(await idsOf(a.json.access)).toContain(S.convA);
    expect(await idsOf(b.json.access)).not.toContain(S.convA);
    expect(a.json.user.id).not.toBe(b.json.user.id);
    // contrast: ONE global sub holding memberships in both tenants sees the union (Contract §7 explains why hosts namespace the sub)
    await staffExchange(mint({ actor: 'tenant_staff', tenant: TB, role: 'admin', sub: P_SHARED_PERSON }));
    const shared = await staffExchange(mint({ actor: 'tenant_staff', tenant: TA, role: 'admin', sub: P_SHARED_PERSON }));
    expect(shared.json.memberships.length).toBe(2);
    expect(await idsOf(shared.json.access)).toContain(S.convA);
  });

  test('tenant admin -> platform, platform -> tenant (first message), idempotency, close/reopen, wrong tenant impossible', async () => {
    const adminA = (await staffExchange(mint({ actor: 'tenant_staff', tenant: TA, role: 'admin', sub: P_ADMIN_A }))).json.access;
    const adminB = (await staffExchange(mint({ actor: 'tenant_staff', tenant: TB, role: 'admin', sub: P_ADMIN_B }))).json.access;
    const plat = (await staffExchange(mint({ actor: 'platform_staff', role: 'owner', sub: 'plat-1', tenant: undefined }))).json;
    expect(plat.access).toBeTruthy();

    // B -> platform: tenant admin writes first
    const started = await http('POST', `${API}/support/start/`, { body: { subject: 'Invoice question', message: 'Need help with billing', client_message_id: `b1-${RUN}` }, headers: bearer(adminB) });
    expect([200, 201], JSON.stringify(started.json)).toContain(started.status);
    const convB = started.json.id;
    const inbox = await http('GET', `${API}/platform/support/`, { headers: bearer(plat.access) });
    const inboxRows = Array.isArray(inbox.json) ? inbox.json : inbox.json.results;
    expect(inboxRows.map((c) => c.id)).toContain(convB);
    expect((await http('POST', `${API}/platform/support/${convB}/reply/`, { body: { content: 'Platform here', client_message_id: `p1-${RUN}` }, headers: bearer(plat.access) })).status).toBe(201);
    const seenByB = await http('GET', `${API}/support/${convB}/messages/`, { headers: bearer(adminB) });
    expect(seenByB.json.map((m) => m.content)).toEqual(expect.arrayContaining(['Need help with billing', 'Platform here']));
    // tenant A's admin can neither list nor read B's thread
    const listA = await http('GET', `${API}/support/`, { headers: bearer(adminA) });
    expect((Array.isArray(listA.json) ? listA.json : listA.json.results).map((c) => c.id)).not.toContain(convB);
    expect([403, 404]).toContain((await http('GET', `${API}/support/${convB}/messages/`, { headers: bearer(adminA) })).status);
    // close / reopen from both sides
    const closed = await http('POST', `${API}/platform/support/${convB}/close/`, { headers: bearer(plat.access) });
    expect(closed.json.status).toBe('CLOSED');
    const reopened = await http('POST', `${API}/support/${convB}/reopen/`, { headers: bearer(adminB) });
    expect(reopened.json.status).not.toBe('CLOSED');

    // platform -> A with NO prior contact, through the host's trusted backend (scope conversations:initiate + Idempotency-Key)
    const initiate = (key, tenant = TA, body = {}) => s2s(HOST, 'POST', `/api/v1/integrations/tenants/${tenant}/support-conversations/`,
      { initiator_user_id: 'plat-1', subject: 'Heads up', message: 'Please update your contact details', ...body }, { headers: { 'Idempotency-Key': key } });
    const key = `init-${RUN}`;
    const first = await initiate(key);
    expect(first.status, JSON.stringify(first.json)).toBe(201);
    expect(first.json.created).toBe(true);
    const replay = await initiate(key);
    expect(replay.json.conversation_id).toBe(first.json.conversation_id);
    expect(replay.headers.get('idempotent-replayed')).toBe('true');
    const resume = await initiate(`other-${RUN}`, TA, { message: 'one more thing' });
    expect(resume.json.conversation_id).toBe(first.json.conversation_id);          // one active thread per (tenant, subject_key)
    const seenByA = await http('GET', `${API}/support/${first.json.conversation_id}/messages/`, { headers: bearer(adminA) });
    expect(seenByA.json.map((m) => m.content)).toEqual(expect.arrayContaining(['Please update your contact details', 'one more thing']));
    expect((await http('POST', `${API}/support/${first.json.conversation_id}/send_message/`, { body: { content: 'Done, thanks', client_message_id: `a-${RUN}` }, headers: bearer(adminA) })).status).toBe(201);
    const platSeen = await http('GET', `${API}/platform/support/${first.json.conversation_id}/messages/`, { headers: bearer(plat.access) });
    expect(platSeen.json.map((m) => m.content)).toContain('Done, thanks');
    // B cannot see the thread opened to A; a missing key / unknown tenant / other integration are refused
    expect([403, 404]).toContain((await http('GET', `${API}/support/${first.json.conversation_id}/messages/`, { headers: bearer(adminB) })).status);
    expect((await s2s(HOST, 'POST', `/api/v1/integrations/tenants/${TA}/support-conversations/`, { initiator_user_id: 'plat-1', message: 'x' })).json.error.code).toBe('idempotency_key_required');
    expect((await s2s(MAL, 'POST', `/api/v1/integrations/tenants/${TA}/support-conversations/`, { initiator_user_id: 'plat-1', message: 'x' }, { headers: { 'Idempotency-Key': `m-${RUN}` } })).status).toBe(403);
    expect((await initiate(`ghost-${RUN}`, 'no-such-tenant')).status).toBe(403);
    expect((await initiate(`op-${RUN}`, TA, { initiator_user_id: P_OP_A })).status).toBeGreaterThanOrEqual(400);   // an operator is not a platform initiator
  });

  test('revocation: removing staff / disabling a customer cuts live sockets (idle-socket re-check, <= WS_REVALIDATE_INTERVAL_SECONDS + jitter) and API access', async () => {
    expect(S.opSocket.closed).toBeNull();
    expect((await s2s(HOST, 'DELETE', `/api/v1/integrations/tenants/${TA}/members/${P_OP_A}/`)).status).toBeLessThan(300);
    expect(await closedWithin(S.opSocket, 45000), 'operator socket closed after membership removal').toBe(true);
    expect([401, 403, 404]).toContain((await http('GET', `${API}/conversations/${S.convA}/messages/`, { headers: bearer(S.opA) })).status);
    // a removed member's next SSO is refused for that tenant as an operator... and re-adding restores access
    expect((await s2s(HOST, 'PUT', `/api/v1/integrations/tenants/${TA}/members/${P_OP_A}/`, { role: 'operator' })).status).toBe(200);

    expect(S.custSocket.closed).toBeNull();
    expect((await s2s(HOST, 'POST', `/api/v1/integrations/tenants/${TA}/customers/cust-1/disable/`)).status).toBeLessThan(300);
    expect(await closedWithin(S.custSocket, 45000), 'customer socket closed after the identity was disabled').toBe(true);
    const after = await http('GET', `${API}/widget/conversations/${S.convA}/messages/`, { headers: S.custSession });
    expect([401, 403]).toContain(after.status);
    const relogin = await customerExchange(S.keyA, mint({ sub: 'cust-1' }));
    expect([401, 403]).toContain(relogin.status);
    expect((await s2s(HOST, 'POST', `/api/v1/integrations/tenants/${TA}/customers/cust-1/enable/`)).status).toBeLessThan(300);
    expect((await customerExchange(S.keyA, mint({ sub: 'cust-1' }))).status).toBe(200);
  });

  test('suspended and archived tenants stop serving; history is retained; unarchive restores', async () => {
    const suspend = await s2s(HOST, 'PUT', `/api/v1/integrations/tenants/${TB}/`, { status: 'suspended' });
    expect(suspend.status).toBe(200);
    expect([400, 401, 403, 404, 409]).toContain((await customerExchange(S.keyB, mint({ sub: 'late', tenant: TB }))).status);
    expect((await http('GET', `${API}/widget/config/?project_key=${S.keyB}`, { headers: { Origin: ORIGIN } })).status).toBe(404);
    expect((await s2s(HOST, 'PUT', `/api/v1/integrations/tenants/${TB}/`, { status: 'active' })).status).toBe(200);
    expect((await customerExchange(S.keyB, mint({ sub: 'late', tenant: TB }))).status).toBe(200);
    const arch = await s2s(HOST, 'DELETE', `/api/v1/integrations/tenants/${TB}/`);
    expect(arch.status).toBeLessThan(300);
    const still = await s2s(HOST, 'GET', `/api/v1/integrations/tenants/${TB}/`);
    expect(still.json.status).toBe('archived');                                      // retained, not deleted
  });
});
