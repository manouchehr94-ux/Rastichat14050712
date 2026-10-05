// Soak: synthetic visitors + operators over real TLS WebSockets through nginx, spread over many loopback source addresses so the
// real per-IP nginx limits stay in force. Measures delivery, latency, reconnect churn and the server's resource trend.
//   node soak.mjs   (env: see test-env.sh; SOAK_MINUTES, SOAK_VISITORS, SOAK_OPERATORS, SOAK_OUT)
import https from 'node:https';
import fs from 'node:fs';
import { execSync } from 'node:child_process';
import WebSocket from 'ws';

const env = (k, d) => process.env[k] ?? d;
const BACKEND = new URL(env('SMOKE_BACKEND_URL', 'https://chat-stg.example.test'));
const WS = env('SMOKE_WS_URL', 'wss://chat-stg.example.test');
const PROJECT_KEY = env('SMOKE_PROJECT_KEY');
const MINUTES = +env('SOAK_MINUTES', 30), VISITORS = +env('SOAK_VISITORS', 50), OPERATORS = +env('SOAK_OPERATORS', 5);
const OUT = env('SOAK_OUT', '/tmp/soak'); fs.mkdirSync(OUT, { recursive: true });
const EMBED = env('DJANGO52_ALLOWED_EMBED_ORIGIN', 'https://embed-allowed.example.test');
const OPERATOR_ORIGIN = 'https://operator-stg.example.test';
const IPS = Array.from({ length: 12 }, (_, i) => `127.0.0.${i + 2}`);
const ADMIN_IP = '127.0.0.30';
const seed = JSON.parse(fs.readFileSync(env('DJANGO52_SEED_JSON'), 'utf8'));
const stats = { rateLimited: 0, visitors: 0, sentUp: 0, deliveredUp: 0, sentDown: 0, deliveredDown: 0, reconnects: 0, unexpectedCloses: 0, authFailures: 0, errors: {}, latencyUp: [], latencyDown: [] };
const bump = (k) => { stats.errors[k] = (stats.errors[k] || 0) + 1; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const rnd = (a, b) => a + Math.random() * (b - a);
const deadline = Date.now() + MINUTES * 60000;

function api(method, path, { body, headers = {}, ip = ADMIN_IP } = {}) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = https.request({ host: BACKEND.hostname, port: 443, path, method, localAddress: ip, headers: { 'Content-Type': 'application/json', ...headers, ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {}) } }, (res) => {
      let buf = ''; res.on('data', (c) => (buf += c)); res.on('end', () => { try { resolve({ status: res.statusCode, json: buf ? JSON.parse(buf) : null }); } catch { resolve({ status: res.statusCode, json: null }); } });
    });
    req.on('error', reject); if (data) req.write(data); req.end();
  });
}

class Participant {
  constructor(kind, ip, name) { Object.assign(this, { kind, ip, name, ws: null, pending: new Map(), alive: true, conv: null }); }
  async ticket() {
    if (this.kind === 'visitor') return api('POST', '/api/v1/widget/ws-ticket/', { ip: this.ip, headers: { Origin: EMBED, 'X-Widget-Session': this.token }, body: { conversation_id: this.conv } });
    return api('POST', '/api/v1/ws/ticket/', { ip: this.ip, headers: { Authorization: `Bearer ${this.jwt}` }, body: { kind: 'dashboard_chat', conversation_id: this.conv } });
  }
  async connect() {
    const t = await this.ticket();
    if (t.status !== 201) { bump(`ticket_${t.status}`); await sleep(2000); return; }
    const path = this.kind === 'visitor' ? `/v2/widget/${this.conv}/` : `/v2/dashboard/${this.conv}/`;
    const ws = new WebSocket(`${WS}/ws${path}`, { headers: { Origin: this.kind === 'visitor' ? EMBED : OPERATOR_ORIGIN }, localAddress: this.ip });
    this.ws = ws; let authed = false;
    ws.on('open', () => ws.send(JSON.stringify({ type: 'auth', ticket: t.json.ticket })));
    ws.on('message', (raw) => this.onFrame(JSON.parse(String(raw)), () => { authed = true; }));
    ws.on('error', (e) => bump(`ws_error_${e.code || e.message}`.slice(0, 40)));
    ws.on('close', (code) => { if (!authed) stats.authFailures++; if (this.alive && !this.expectedClose) { stats.unexpectedCloses++; bump(`close_${code}`); } this.expectedClose = false; if (this.alive) setTimeout(() => this.reconnect(), rnd(500, 3000)); });
  }
  async reconnect() { stats.reconnects++; await this.connect(); }
  onFrame(f, markAuth) {
    if (f.type === 'auth.ok') { markAuth(); return; }
    if (f.type === 'rate_limited') { stats.rateLimited++; return; }
    if (f.type !== 'chat.message' || !f.content) return;
    const sent = this.pending.get(f.content);
    if (sent) { // my own echo
      this.pending.delete(f.content);
      (this.kind === 'visitor' ? stats.latencyUp : stats.latencyDown).push(Date.now() - sent);
      return;
    }
    const m = /^soak-(up|down)-(\d+)$/.exec(f.content); if (!m) return;
    if (m[1] === 'up' && this.kind === 'operator') { stats.deliveredUp++; this.onVisitorMessage?.(f); }
    if (m[1] === 'down' && this.kind === 'visitor') stats.deliveredDown++;
  }
  send(text) { if (this.ws?.readyState === WebSocket.OPEN) { this.pending.set(text, Date.now()); this.ws.send(JSON.stringify({ message: text, client_message_id: text })); return true; } return false; }
}

let counter = 0;
const visitors = [], operators = [];

async function operatorJwts() {
  // staff WebSocket writes are rate-limited PER USER (60/min across all of a user's sockets): a realistic soak uses several staff
  // identities, like a real team, instead of one account answering every customer
  const creds = Object.entries(seed.users).filter(([k]) => k.startsWith('soak-op-')).map(([, u]) => [u.email, u.password]);
  if (!creds.length) throw new Error('seed has no soak-op-* users (run stack.sh seed)');
  const jwts = [];
  for (const [email, password] of creds.slice(0, OPERATORS)) {
    const login = await api('POST', '/api/v1/auth/login/', { body: { email, password } });
    if (login.status !== 200) throw new Error(`operator login ${email} -> ${login.status}`);
    jwts.push(login.json.access); await sleep(7000); // nginx allows 10 logins/min per address
  }
  return jwts;
}

async function setup() {
  const jwts = await operatorJwts();
  for (let i = 0; i < VISITORS; i++) {
    const ip = IPS[i % IPS.length];
    const init = await api('POST', '/api/v1/widget/init/', { ip, headers: { Origin: EMBED }, body: { project_key: PROJECT_KEY } });
    if (init.status !== 200) throw new Error(`init ${init.status} ${JSON.stringify(init.json)}`);
    const start = await api('POST', '/api/v1/widget/start/', { ip, headers: { Origin: EMBED, 'X-Widget-Session': init.json.session_token }, body: { session_token: init.json.session_token } });
    if (start.status !== 200) throw new Error(`start ${start.status}`);
    const v = new Participant('visitor', ip, `v${i}`); v.token = init.json.session_token; v.conv = start.json.id; visitors.push(v);
    await sleep(120);
  }
  stats.visitors = visitors.length;
  // each operator watches a slice of the conversations (several sockets per operator, like a busy dashboard)
  visitors.forEach((v, i) => {
    const o = new Participant('operator', IPS[(i + 3) % IPS.length], `op${i % OPERATORS}`); o.jwt = jwts[i % jwts.length]; o.conv = v.conv; o.visitor = v;
    o.onVisitorMessage = () => setTimeout(() => { const t = `soak-down-${++counter}`; if (o.send(t)) stats.sentDown++; }, rnd(500, 2500));
    operators.push(o);
  });
}

function sample() {
  const row = { t: new Date().toISOString() };
  for (const n of [1, 2]) { try { const pid = fs.readFileSync(`${env('STG_DIR')}/run/daphne${n}.pid`, 'utf8').trim(); row[`daphne${n}_rss_mb`] = +(/VmRSS:\s+(\d+)/.exec(fs.readFileSync(`/proc/${pid}/status`, 'utf8'))[1] / 1024).toFixed(1); row[`daphne${n}_fds`] = fs.readdirSync(`/proc/${pid}/fd`).length; } catch { row[`daphne${n}_rss_mb`] = null; } }
  try {
    const redis = (c) => execSync(`redis-cli -p ${env('REDISPORT', 56379)} -a '${env('REDIS_PASSWORD_PLAIN')}' --no-auth-warning ${c}`).toString().trim();
    row.redis_used_mb = +(/used_memory:(\d+)/.exec(redis('info memory'))[1] / 1048576).toFixed(2);
    row.redis_keys = +redis('dbsize'); row.ticket_keys = redis("--scan --pattern 'wsticket:*'").split('\n').filter(Boolean).length;
  } catch { /* sampling must never stop the soak */ }
  try { row.pg_conns = +execSync(`PGPASSWORD='${env('DB_PASSWORD_PLAIN')}' psql -h 127.0.0.1 -p ${env('PGPORT', 55432)} -U rastichat_stg -d rastichat_stg -Atc "select count(*) from pg_stat_activity where datname='rastichat_stg'"`).toString().trim(); } catch { /* */ }
  row.sockets_open = [...visitors, ...operators].filter((p) => p.ws?.readyState === WebSocket.OPEN).length;
  row.sent_up = stats.sentUp; row.deliv_up = stats.deliveredUp; row.sent_down = stats.sentDown; row.deliv_down = stats.deliveredDown;
  row.rate_limited = stats.rateLimited; row.unexpected_closes = stats.unexpectedCloses; row.reconnects = stats.reconnects;
  fs.appendFileSync(`${OUT}/samples.jsonl`, JSON.stringify(row) + '\n');
  console.log(JSON.stringify(row));
}
const pct = (a, p) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(s.length * p))]; };

await setup();
console.log(`setup done: ${visitors.length} visitors, ${operators.length} operator sockets`);
for (const p of [...visitors, ...operators]) { await p.connect(); await sleep(60); }
sample();
const sampler = setInterval(sample, 60000);
// visitors chat every 20-40 s; ~1 in 10 socket drops itself every 3-6 minutes (reconnect churn with fresh tickets)
for (const v of visitors) (async () => {
  await sleep(rnd(1000, 20000));
  let nextDrop = Date.now() + rnd(180000, 360000);
  while (Date.now() < deadline) {
    const t = `soak-up-${++counter}`; if (v.send(t)) stats.sentUp++;
    if (Date.now() > nextDrop && Math.random() < 0.1) { v.expectedClose = true; v.ws?.close(); nextDrop = Date.now() + rnd(180000, 360000); }
    await sleep(rnd(20000, 40000));
  }
})();
await sleep(Math.max(0, deadline - Date.now()));
clearInterval(sampler); sample();
await sleep(15000); // let the last deliveries land
[...visitors, ...operators].forEach((p) => { p.alive = false; p.ws?.close(); });
const summary = {
  minutes: MINUTES, visitors: stats.visitors, operatorSockets: operators.length,
  upstream: { sent: stats.sentUp, delivered: stats.deliveredUp, p50ms: pct(stats.latencyUp, .5), p95ms: pct(stats.latencyUp, .95), max: pct(stats.latencyUp, 1) },
  downstream: { sent: stats.sentDown, delivered: stats.deliveredDown, p50ms: pct(stats.latencyDown, .5), p95ms: pct(stats.latencyDown, .95), max: pct(stats.latencyDown, 1) },
  rateLimitedFrames: stats.rateLimited, operatorIdentities: OPERATORS, reconnects: stats.reconnects, unexpectedCloses: stats.unexpectedCloses, authFailures: stats.authFailures, errors: stats.errors,
};
fs.writeFileSync(`${OUT}/summary.json`, JSON.stringify(summary, null, 2)); console.log('SUMMARY', JSON.stringify(summary));
process.exit(0);
