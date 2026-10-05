// 30-minute soak: 40 visitor sockets + 10 dashboard sockets (50) through nginx/TLS/balancer/2 Daphne workers.
// Every 30 s: operator -> visitors and visitors -> operator traffic; every sample: Daphne RSS, open sockets, Redis wsticket:* keys.
import { createRequire } from 'node:module';
const WebSocket = createRequire(new URL('../../../e2e/package.json', import.meta.url))('ws'); // ws is an e2e devDependency
import { readFileSync, writeFileSync } from 'node:fs';
import { execSync } from 'node:child_process';

const E = process.env, API = E.SMOKE_BACKEND_URL + '/api/v1', WS = E.SMOKE_WS_URL, ORIGIN = E.DJANGO52_ALLOWED_EMBED_ORIGIN;
const MIN = +(E.SOAK_MINUTES || 30), NV = +(E.SOAK_VISITORS || 40), ND = +(E.SOAK_DASH || 10), OUT = E.SOAK_OUT || '/dev/stdout';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const J = async (url, opt = {}) => { const r = await fetch(url, { ...opt, headers: { 'Content-Type': 'application/json', ...(opt.headers || {}) }, body: opt.body && JSON.stringify(opt.body) }); const t = await r.text(); let b; try { b = JSON.parse(t); } catch { b = t; } return { s: r.status, b }; };
const log = []; const L = (m) => { const l = `[${new Date().toISOString()}] ${m}`; log.push(l); console.log(l); };

const stats = { sent: 0, recv: 0, unexpectedClose: [], errors: [], frames: 0 };
const socks = [];
let ipN = 0;
// nginx caps concurrent WebSockets at 20 per client address (limit_conn rastichat_ws_conn): 50 sockets from one address would be refused by design,
// real users come from many addresses, so the soak spreads its sockets over 5 loopback source addresses (10 each)
function connect(kind, url, ticket, origin, label) {
  return new Promise((res, rej) => {
    const ws = new WebSocket(url, { headers: origin ? { Origin: origin } : {}, localAddress: `127.0.0.${10 + (ipN++ % 5)}` });
    const s = { ws, label, kind, got: new Set(), alive: false };
    ws.on('open', () => ws.send(JSON.stringify({ type: 'auth', ticket })));
    ws.on('message', (d) => { stats.frames++; let f; try { f = JSON.parse(String(d)); } catch { return; } if (f.type === 'auth.ok') { s.alive = true; res(s); } else if (f.content) { s.got.add(f.content); stats.recv++; } });
    ws.on('error', (e) => { stats.errors.push(`${label}: ${e.message}`); rej(e); });
    ws.on('close', (c) => { if (s.alive && !s.closing) stats.unexpectedClose.push(`${label} code=${c} at ${new Date().toISOString()}`); s.alive = false; });
    socks.push(s);
  });
}
const rss = (port) => { try { const pid = /pid=(\d+)/.exec(execSync(`ss -ltnp 'sport = :${port}'`, { encoding: 'utf8' }))[1]; return +/VmRSS:\s+(\d+)/.exec(readFileSync(`/proc/${pid}/status`, 'utf8'))[1] / 1024; } catch { return NaN; } };
const redisKeys = () => { try { return +execSync('redis-cli -n 5 --scan --pattern "*wsticket*" | wc -l', { encoding: 'utf8' }).trim(); } catch { return NaN; } };

const login = await J(API + '/auth/login/', { method: 'POST', body: { email: E.SMOKE_OPERATOR_EMAIL, password: E.SMOKE_OPERATOR_PASSWORD } });
if (login.s !== 200) throw new Error('login ' + login.s);
const jwt = login.b.access, auth = { Authorization: `Bearer ${jwt}` };

const visitors = [];
L(`connecting ${NV} visitor sockets (staggered: widget_start is throttled to 20/min per address)`);
for (let i = 0; i < NV; i++) {
  const init = await J(API + '/widget/init/', { method: 'POST', body: { project_key: E.SMOKE_PROJECT_KEY }, headers: { Origin: ORIGIN } });
  const token = init.b.session_token, h = { 'X-Widget-Session': token, Origin: ORIGIN };
  const start = await J(API + '/widget/start/', { method: 'POST', body: { session_token: token }, headers: h });
  const conv = start.b.id;
  const tk = await J(API + '/widget/ws-ticket/', { method: 'POST', body: { conversation_id: conv }, headers: h });
  const s = await connect('visitor', `${WS}/v2/widget/${conv}/`, tk.b.ticket, ORIGIN, `visitor-${i}`);
  visitors.push({ conv, token, s, i });
  await sleep(3300);
}
const dash = [];
for (let i = 0; i < ND; i++) {
  const v = visitors[i];
  const tk = await J(API + '/ws/ticket/', { method: 'POST', body: { kind: 'dashboard_chat', conversation_id: v.conv }, headers: auth });
  const s = await connect('dashboard', `${WS}/v2/dashboard/${v.conv}/`, tk.b.ticket, 'https://operator-stg.example.test', `dash-${i}`);
  dash.push({ v, s });
}
L(`all ${socks.length} sockets authenticated; soak ${MIN} min starts`);

const samples = [];
const t0 = Date.now(); let round = 0;
const sample = () => { const o = { t: Math.round((Date.now() - t0) / 1000), r8101: rss(8101), r8102: rss(8102), open: socks.filter((s) => s.alive).length, tickets: redisKeys() }; samples.push(o); return o; };
sample();
while (Date.now() - t0 < MIN * 60000) {
  round++;
  // operator -> 5 visitors (fan-out through Redis, across workers); 5 visitors -> operator (dashboard sockets of the first 10 conversations)
  for (let k = 0; k < 5; k++) {
    const v = visitors[(round * 5 + k) % NV]; const txt = `soak-op-${round}-${k}`; v.expect = (v.expect || []).concat(txt);
    const r = await J(`${API}/conversations/${v.conv}/send/`, { method: 'POST', body: { content: txt, client_message_id: `soak-${round}-${k}-${Date.now()}` }, headers: auth });
    if (r.s >= 300) stats.errors.push(`operator send ${r.s}`); else stats.sent++;
  }
  for (let k = 0; k < 5; k++) {
    const d = dash[(round + k) % ND]; const txt = `soak-vis-${round}-${k}`; d.expect = (d.expect || []).concat(txt);
    d.v.s.ws.send(JSON.stringify({ message: txt, client_message_id: `sv-${round}-${k}-${Date.now()}` })); stats.sent++;
  }
  await sleep(30000);
  const o = sample();
  L(`t=${o.t}s round=${round} open=${o.open}/${socks.length} rss8101=${o.r8101.toFixed(1)}MB rss8102=${o.r8102.toFixed(1)}MB wsticket-keys=${o.tickets} sent=${stats.sent} recv=${stats.recv} unexpectedClose=${stats.unexpectedClose.length} errors=${stats.errors.length}`);
}
// delivery check: every expected text must have arrived at its socket
let missing = 0;
for (const v of visitors) for (const t of v.expect || []) if (!v.s.got.has(t)) missing++;
for (const d of dash) for (const t of d.expect || []) if (!d.s.got.has(t)) missing++;
await sleep(40000); const fin = sample();
const first = samples.find((s) => s.t >= 300) || samples[0], last = samples[samples.length - 1];
const growth = (a, b) => ((b - a) / a * 100);
const result = {
  minutes: MIN, sockets: socks.length, openAtEnd: last.open, unexpectedClose: stats.unexpectedClose, errors: stats.errors.slice(0, 20), sent: stats.sent, recv: stats.recv, missing,
  rss: { w8101: { after5min: first.r8101, end: last.r8101, growthPct: growth(first.r8101, last.r8101) }, w8102: { after5min: first.r8102, end: last.r8102, growthPct: growth(first.r8102, last.r8102) } },
  wsticketKeysEnd: last.tickets, samples,
};
result.pass = result.openAtEnd === socks.length && !stats.unexpectedClose.length && !stats.errors.length && missing === 0 && result.rss.w8101.growthPct < 25 && result.rss.w8102.growthPct < 25 && last.tickets === 0;
socks.forEach((s) => { s.closing = true; s.ws.close(); });
writeFileSync(OUT, JSON.stringify(result, null, 2));
L(`SOAK ${result.pass ? 'PASS' : 'FAIL'}: open ${result.openAtEnd}/${socks.length}, missing ${missing}, unexpectedClose ${stats.unexpectedClose.length}, rss growth ${result.rss.w8101.growthPct.toFixed(1)}% / ${result.rss.w8102.growthPct.toFixed(1)}%, wsticket keys ${last.tickets}`);
process.exit(result.pass ? 0 : 1);
