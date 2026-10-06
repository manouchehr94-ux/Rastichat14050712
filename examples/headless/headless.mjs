// Headless RastiChat integration: NO widget, NO RastiChat code — only the documented Integration Contract v1 + public
// widget REST/WebSocket protocol (docs/integrations/HEADLESS.md). Node >= 22 (global fetch + WebSocket), zero dependencies.
//
// It plays three roles to prove a custom UI could be built the same way:
//   host backend    : provisions a tenant, pushes customer context, signs identity assertions   (server-to-server)
//   customer client : config -> identity exchange -> start -> realtime ticket -> WebSocket -> send/receive -> reconnect -> history
//   operator        : staff SSO exchange -> reply through the normal dashboard API
// Exits non-zero on the first failed expectation.
//
// env: RASTICHAT_URL KEY_ID INTEGRATION_SLUG PRIVATE_KEY_FILE [HEADLESS_ORIGIN] [RASTICHAT_WS]
import { readFileSync } from 'node:fs';
import { RastiChatClient } from '../reference-host/lib/rastichat.mjs';

const BASE = (process.env.RASTICHAT_URL || 'http://localhost:8080').replace(/\/$/, '');
const API = `${BASE}/api/v1`;
const WS = (process.env.RASTICHAT_WS || BASE.replace(/^http/, 'ws') + '/ws').replace(/\/$/, '');
const ORIGIN = process.env.HEADLESS_ORIGIN || 'http://headless.example.test';
const TENANT = 'headless-demo';
const host = new RastiChatClient({
  baseUrl: BASE, slug: process.env.INTEGRATION_SLUG, kid: process.env.KEY_ID,
  privateKeyPem: readFileSync(process.env.PRIVATE_KEY_FILE, 'utf8'),
});

const ok = (cond, msg) => { if (!cond) { console.error('FAIL:', msg); process.exit(1); } console.log('ok  -', msg); };
const json = async (res) => ({ status: res.status, body: await res.json().catch(() => ({})) });
const jfetch = (url, opts = {}) => fetch(url, { ...opts, headers: { 'Content-Type': 'application/json', Origin: ORIGIN, ...(opts.headers || {}) } }).then(json);

// ---- host backend: provision (idempotent), staff member, context
const tenant = await host.ensureTenant(TENANT, {
  display_name: 'Headless Demo', verified_domains: [new URL(ORIGIN).hostname],
  defaults: { widget: { launcher: { mode: 'icon' }, pre_chat: { enabled: false } } },
});
ok(tenant.project_public_key, `tenant provisioned (project key ${tenant.project_public_key.slice(0, 8)}…)`);
const again = await host.ensureTenant(TENANT, { display_name: 'Headless Demo' });
ok(again.workspace_id === tenant.workspace_id, 'provisioning is idempotent (same workspace)');
await host.setStaffMember(TENANT, 'headless-op', 'operator', 'Headless Operator');
await host.request('PUT', `/api/v1/integrations/tenants/${TENANT}/contexts/headless-cust/`,
  { profile: { tier: 'gold' }, context: { page: '/pricing' } });

// ---- customer client
const cfg = await jfetch(`${API}/widget/config/?project_key=${tenant.project_public_key}`);
ok(cfg.status === 200 && cfg.body.launcher, 'widget config is served (versioned JSON: launcher, pre_chat, identity…)');
const assertion = host.assertion({ actor: 'customer', sub: 'headless-cust', tenant: TENANT, name: 'Headless Customer', origin: ORIGIN });
const exchange = await jfetch(`${API}/identity/customer/`, { method: 'POST', body: JSON.stringify({ project_key: tenant.project_public_key, assertion }) });
ok(exchange.status === 200 && exchange.body.identity.verified, 'trusted identity exchange -> verified visitor session');
const replay = await jfetch(`${API}/identity/customer/`, { method: 'POST', body: JSON.stringify({ project_key: tenant.project_public_key, assertion }) });
ok(replay.status === 401, 'the same assertion cannot be used twice (replay refused)');
const session = { 'X-Widget-Session': exchange.body.session_token };
const start = await jfetch(`${API}/widget/start/`, { method: 'POST', headers: session, body: JSON.stringify({}) });
ok(start.status === 200 && start.body.id, 'conversation started');
const convId = start.body.id;

async function connect() {
  const t = await jfetch(`${API}/widget/ws-ticket/`, { method: 'POST', headers: session, body: JSON.stringify({ conversation_id: convId }) });
  ok(t.status === 201 && t.body.ticket, 'single-use realtime ticket issued over REST (never a credential in the URL)');
  const ws = new WebSocket(`${WS}/v2/widget/${convId}/`, { headers: { Origin: ORIGIN } });
  const frames = [];
  const waiters = [];
  ws.addEventListener('message', (ev) => { const f = JSON.parse(ev.data); frames.push(f); waiters.splice(0).forEach((w) => w()); });
  await new Promise((res, rej) => { ws.addEventListener('open', res); ws.addEventListener('error', () => rej(new Error('ws error'))); });
  ws.send(JSON.stringify({ type: 'auth', ticket: t.body.ticket }));
  const until = async (pred, label, ms = 15000) => {
    const end = Date.now() + ms;
    while (Date.now() < end) { const hit = frames.find(pred); if (hit) return hit; await new Promise((r) => { waiters.push(r); setTimeout(r, 250); }); }
    ok(false, `timed out waiting for ${label}`);
  };
  await until((f) => f.type === 'auth.ok', 'auth.ok');
  return { ws, frames, until };
}

let rt = await connect();
rt.ws.send(JSON.stringify({ message: 'Hello from a headless client', client_message_id: 'h-1' }));
ok(true, 'message sent over the WebSocket');

// ---- operator: normal dashboard API with a staff SSO session
const staff = await jfetch(`${API}/identity/staff/`, { method: 'POST', body: JSON.stringify({
  assertion: host.assertion({ actor: 'tenant_staff', sub: 'headless-op', tenant: TENANT, role: 'operator', name: 'Headless Operator' }) }) });
ok(staff.status === 200 && staff.body.access, 'staff SSO exchange -> dashboard token (no password)');
const auth = { Authorization: `Bearer ${staff.body.access}` };
const panel = await jfetch(`${API}/conversations/customer/${convId}/customer-context/`, { headers: auth });
ok(panel.body.identity_verified === true && panel.body.host_context?.profile?.tier === 'gold', 'operator sees verified identity + host-pushed context');
const reply = await jfetch(`${API}/conversations/${convId}/send/`, { method: 'POST', headers: auth, body: JSON.stringify({ content: 'Hi! How can we help?', client_message_id: 'op-1' }) });
ok(reply.status === 201 || reply.status === 200, 'operator reply accepted');
const got = await rt.until((f) => f.sender_type === 'USER' && /How can we help/.test(f.content || ''), 'operator reply over the socket');
ok(!!got, 'customer receives the operator reply in real time');

// ---- reconnect + history resync (what a UI must do after any network loss)
rt.ws.close();
rt = await connect();
const hist = await jfetch(`${API}/widget/conversations/${convId}/messages/`, { headers: session });
const texts = hist.body.map((m) => m.content);
ok(texts.includes('Hello from a headless client') && texts.includes('Hi! How can we help?'), 'history resync after reconnect returns both sides');
rt.ws.close();

// ---- isolation: another tenant's project key cannot use this session
const other = await host.ensureTenant('headless-other', { display_name: 'Other', verified_domains: [new URL(ORIGIN).hostname] });
const cross = await jfetch(`${API}/widget/conversations/${convId}/messages/`, { headers: { 'X-Widget-Session': (await jfetch(`${API}/identity/customer/`, { method: 'POST', body: JSON.stringify({ project_key: other.project_public_key, assertion: host.assertion({ actor: 'customer', sub: 'headless-cust', tenant: 'headless-other', origin: ORIGIN }) }) })).body.session_token } });
ok(cross.status === 404 || cross.status === 403, "a customer of another tenant cannot read this tenant's conversation");
console.log('\nHEADLESS INTEGRATION: ALL OK');
