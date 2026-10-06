// "Acme Learn" — a tiny, deliberately NON-RastiSi host application used to prove that RastiChat is reusable.
// It has its own users, organisations and login; it knows nothing about RastiChat's internals — only the public
// Integration Contract v1 (docs/integrations/INTEGRATION_CONTRACT_V1.md) through lib/rastichat.mjs.
//
//   RASTICHAT_URL=http://localhost:8080 INTEGRATION_SLUG=acme-learn KEY_ID=ick_… PRIVATE_KEY_FILE=./host.private.pem \
//   WIDGET_URL=http://localhost:8081/widget.iife.js DASHBOARD_URL=http://localhost:3000/admin \
//   TENANTS_FILE=./tenants.json node server.mjs
import http from 'node:http';
import fs from 'node:fs';
import { randomUUID } from 'node:crypto';
import { RastiChatClient } from './lib/rastichat.mjs';

const env = (k, d) => process.env[k] ?? d;
const PORT = Number(env('PORT', 4000));
const client = new RastiChatClient({
  baseUrl: env('RASTICHAT_URL', 'http://localhost:8080'), slug: env('INTEGRATION_SLUG', 'acme-learn'),
  kid: env('KEY_ID'), privateKeyPem: fs.readFileSync(env('PRIVATE_KEY_FILE', './host.private.pem'), 'utf8'),
});
const WIDGET_URL = env('WIDGET_URL', 'http://localhost:8081/widget.iife.js');
const API_BASE = env('RASTICHAT_URL', 'http://localhost:8080').replace(/\/$/, '') + '/api/v1';
const WS_BASE = env('RASTICHAT_URL', 'http://localhost:8080').replace(/^http/, 'ws').replace(/\/$/, '') + '/ws';
// the RastiChat operator dashboard (deployed under the /admin base path)
const DASHBOARD_URL = env('DASHBOARD_URL', 'http://localhost:3000/admin');

// The host's own data. Organisation -> RastiChat tenant is the host's mapping; project keys come from provisioning.
const tenants = JSON.parse(fs.readFileSync(env('TENANTS_FILE', './tenants.json'), 'utf8')); // {orgId: {name, project_public_key}}
const users = {
  alice: { name: 'Alice (student)', org: 'org-a', kind: 'customer' },
  dave:  { name: 'Dave (student)',  org: 'org-b', kind: 'customer' },
  bob:   { name: 'Bob (teacher)',   org: 'org-a', kind: 'staff', role: 'admin' },
  erin:  { name: 'Erin (teacher)',  org: 'org-b', kind: 'staff', role: 'operator' },
};
const sessions = new Map(); // sid -> user id

const cookie = (req) => Object.fromEntries((req.headers.cookie ?? '').split(/;\s*/).filter(Boolean).map((c) => c.split('=')));
const currentUser = (req) => users[sessions.get(cookie(req).sid)] ? { id: sessions.get(cookie(req).sid), ...users[sessions.get(cookie(req).sid)] } : null;
const send = (res, status, body, type = 'text/html; charset=utf-8', headers = {}) => { res.writeHead(status, { 'Content-Type': type, 'Cache-Control': 'no-store', ...headers }); res.end(body); };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function page(org, user) {
  const t = tenants[org];
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${esc(t.name)} — Acme Learn</title></head>
<body><h1 id="org">${esc(t.name)}</h1>
<p id="who">${user ? `Signed in as ${esc(user.name)}` : 'Browsing as a guest'}</p>
<p><a href="/login?as=alice">log in as alice</a> · <a href="/login?as=dave">dave</a> · <a href="/login?as=bob">bob (staff)</a> · <a href="/login?as=erin">erin (staff)</a> · <a href="/logout">log out</a></p>
<script src="${esc(WIDGET_URL)}"></script>
<script>
  RastiChat.init({
    projectKey: ${JSON.stringify(t.project_public_key)}, apiBase: ${JSON.stringify(API_BASE)}, wsBase: ${JSON.stringify(WS_BASE)},
    // The ONLY identity input: an assertion minted by THIS host's backend from ITS OWN session.
    bootstrap: async () => { const r = await fetch('/chat/identity?org=${org}', { credentials: 'include' }); return r.ok ? r.text() : null; },
    context: { page: location.pathname },
  });
</script></body></html>`;
}

http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  try {
    if (url.pathname === '/login') {
      const as = url.searchParams.get('as');
      if (!users[as]) return send(res, 404, 'unknown user');
      const sid = randomUUID(); sessions.set(sid, as);
      return send(res, 302, '', 'text/plain', { Location: `/o/${users[as].org}`, 'Set-Cookie': `sid=${sid}; Path=/; HttpOnly; SameSite=Lax` });
    }
    if (url.pathname === '/logout') {
      sessions.delete(cookie(req).sid);
      return send(res, 302, '', 'text/plain', { Location: '/o/org-a', 'Set-Cookie': 'sid=; Path=/; Max-Age=0' });
    }
    const m = url.pathname.match(/^\/o\/([\w-]+)$/);
    if (m && tenants[m[1]]) return send(res, 200, page(m[1], currentUser(req)));

    // Identity bootstrap for the widget. Tenant, user and role come from the host's OWN server-side state — never
    // from request parameters (the `org` param only says which page asked; it must match the user's own org).
    if (url.pathname === '/chat/identity') {
      const user = currentUser(req);
      if (!user || user.kind !== 'customer' || user.org !== url.searchParams.get('org')) return send(res, 401, 'not signed in', 'text/plain');
      return send(res, 200, client.assertion({ actor: 'customer', sub: user.id, tenant: user.org, name: user.name, origin: `http://${req.headers.host}` }), 'text/plain');
    }
    // Staff single sign-on into the RastiChat operator dashboard: the assertion travels in the URL FRAGMENT.
    if (url.pathname === '/staff/chat') {
      const user = currentUser(req);
      if (!user || user.kind !== 'staff') return send(res, 403, 'staff only', 'text/plain');
      const a = client.assertion({ actor: 'tenant_staff', sub: user.id, tenant: user.org, role: user.role, name: user.name });
      return send(res, 302, '', 'text/plain', { Location: `${DASHBOARD_URL}/sso#assertion=${a}&next=/` });
    }
    if (url.pathname === '/health') return send(res, 200, 'ok', 'text/plain');
    send(res, 404, 'not found', 'text/plain');
  } catch (e) { send(res, 500, 'error', 'text/plain'); console.error(e.message); }
}).listen(PORT, () => console.log(`reference host on http://localhost:${PORT}`));
