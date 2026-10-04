#!/usr/bin/env node
// Staging-scale load baseline: simulates concurrent visitors (and,
// optionally, operators) against a REAL deployed environment and reports
// factual latency/error/duplicate numbers. Deliberately does not attempt
// to simulate production-scale traffic, and its output must never be
// read as a production capacity guarantee — see the printed disclaimer
// and docs/runbooks/MONITORING_RUNBOOK.md "Load baseline" section.
//
// Pure Node.js (18+, uses global fetch/WebSocket) — no dependencies to
// install, so it can run directly on the VPS or from a laptop against it.
//
// Usage:
//   BASE_URL=https://chat-staging.rastisi.ir \
//   WS_URL=wss://chat-staging.rastisi.ir/ws \
//   PROJECT_KEY=<Project.public_key> \
//   VISITOR_COUNT=50 MESSAGES_PER_VISITOR=3 \
//   OPERATOR_CREDENTIALS="email1:pass1,email2:pass2" \
//   node scripts/staging/load-baseline.mjs

const BASE_URL = requireEnv('BASE_URL');
const WS_URL = requireEnv('WS_URL');
const PROJECT_KEY = requireEnv('PROJECT_KEY');
const VISITOR_COUNT = parseInt(process.env.VISITOR_COUNT || '50', 10);
const MESSAGES_PER_VISITOR = parseInt(process.env.MESSAGES_PER_VISITOR || '3', 10);
const UPLOAD_FRACTION = parseFloat(process.env.UPLOAD_FRACTION || '0.2'); // 20% of visitors also upload an image
const OPERATOR_CREDENTIALS = (process.env.OPERATOR_CREDENTIALS || '')
  .split(',').map(s => s.trim()).filter(Boolean)
  .map(pair => { const [email, password] = pair.split(':'); return { email, password }; });
// Optional — /api/v1/health/monitoring/ requires this header in staging/
// production (see common.views.MonitoringView); without it pollMonitoring()
// below just gets a 401 and silently skips the disk/scheduler/backup
// numbers it reports, same as before this existed.
const MONITORING_TOKEN = process.env.MONITORING_TOKEN || '';

function requireEnv(name) {
  const v = process.env[name];
  if (!v) {
    console.error(`${name} is required. See the usage comment at the top of this script.`);
    process.exit(1);
  }
  return v;
}

// A 1x1 transparent PNG — same fixture the E2E suites use, kept tiny so
// upload load doesn't itself become the bottleneck being measured.
const TINY_PNG_BASE64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

const stats = {
  visitors: { attempted: 0, initOk: 0, startOk: 0, wsConnected: 0, wsFailed: 0 },
  messages: { sent: 0, echoed: 0, duplicates: 0, timedOut: 0, latenciesMs: [] },
  uploads: { attempted: 0, ok: 0, failed: 0, latenciesMs: [] },
  operators: { attempted: 0, loginOk: 0, wsConnected: 0, wsFailed: 0 },
  errors: [],
};

function percentile(arr, p) {
  if (arr.length === 0) return null;
  const sorted = [...arr].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
  return sorted[idx];
}

async function simulateVisitor(i) {
  stats.visitors.attempted++;
  try {
    const initRes = await fetch(`${BASE_URL}/api/v1/widget/init/`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_key: PROJECT_KEY }),
    });
    if (!initRes.ok) throw new Error(`init ${initRes.status}`);
    const init = await initRes.json();
    stats.visitors.initOk++;
    const sessionToken = init.session_token;

    const startRes = await fetch(`${BASE_URL}/api/v1/widget/start/`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_token: sessionToken }),
    });
    if (!startRes.ok) throw new Error(`start ${startRes.status}`);
    const conv = await startRes.json();
    stats.visitors.startOk++;

    const ws = new WebSocket(`${WS_URL}/widget/${sessionToken}/${conv.id}/`);
    const pending = new Map(); // client_message_id -> send timestamp
    const seen = new Set();

    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('ws connect timeout')), 10000);
      ws.addEventListener('open', () => { clearTimeout(timer); resolve(); });
      ws.addEventListener('error', () => { clearTimeout(timer); reject(new Error('ws error')); });
    });
    stats.visitors.wsConnected++;

    ws.addEventListener('message', (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type !== 'chat.message') return;
        const cmid = data.client_message_id;
        if (!cmid || !pending.has(cmid)) return;
        if (seen.has(cmid)) { stats.messages.duplicates++; return; }
        seen.add(cmid);
        stats.messages.latenciesMs.push(Date.now() - pending.get(cmid));
        stats.messages.echoed++;
      } catch { /* ignore malformed frames */ }
    });

    for (let m = 0; m < MESSAGES_PER_VISITOR; m++) {
      const cmid = `load-${i}-${m}-${Math.random().toString(36).slice(2)}`;
      pending.set(cmid, Date.now());
      ws.send(JSON.stringify({ message: `load test message ${m} from visitor ${i}`, client_message_id: cmid }));
      stats.messages.sent++;
      await sleep(200 + Math.random() * 300); // stagger, not a tight loop
    }

    if (Math.random() < UPLOAD_FRACTION) {
      stats.uploads.attempted++;
      const uploadStart = Date.now();
      try {
        const form = new FormData();
        const bytes = Buffer.from(TINY_PNG_BASE64, 'base64');
        form.append('file', new Blob([bytes], { type: 'image/png' }), 'load-test.png');
        form.append('session_token', sessionToken);
        form.append('client_message_id', `load-upload-${i}-${Math.random().toString(36).slice(2)}`);
        form.append('message_type', 'IMAGE');
        const uploadRes = await fetch(`${BASE_URL}/api/v1/widget/conversations/${conv.id}/upload/`, {
          method: 'POST', body: form,
        });
        if (uploadRes.ok) { stats.uploads.ok++; stats.uploads.latenciesMs.push(Date.now() - uploadStart); }
        else stats.uploads.failed++;
      } catch (e) {
        stats.uploads.failed++;
        stats.errors.push(`visitor ${i} upload: ${e.message}`);
      }
    }

    await sleep(2000); // give the last echoes a chance to arrive
    ws.close();
  } catch (e) {
    stats.visitors.wsFailed++;
    stats.errors.push(`visitor ${i}: ${e.message}`);
  }
}

async function simulateOperator(cred, i) {
  stats.operators.attempted++;
  try {
    const loginRes = await fetch(`${BASE_URL}/api/v1/auth/login/`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: cred.email, password: cred.password }),
    });
    if (!loginRes.ok) throw new Error(`login ${loginRes.status}`);
    const { access } = await loginRes.json();
    stats.operators.loginOk++;

    // Connects and holds the socket open for the duration of the run —
    // exercising concurrent operator-side connection load without
    // depending on knowing which specific conversation each one should
    // watch (the visitors above create their own independently).
    const summaryRes = await fetch(`${BASE_URL}/api/v1/supervisor/summary/`, {
      headers: { Authorization: `Bearer ${access}` },
    });
    if (!summaryRes.ok) throw new Error(`supervisor summary ${summaryRes.status}`);
    stats.operators.wsConnected++; // counts as a successful authenticated session for this baseline
  } catch (e) {
    stats.operators.wsFailed++;
    stats.errors.push(`operator ${i} (${cred.email}): ${e.message}`);
  }
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function pollMonitoring() {
  try {
    const res = await fetch(`${BASE_URL}/api/v1/health/monitoring/`, {
      headers: MONITORING_TOKEN ? { 'X-Monitoring-Token': MONITORING_TOKEN } : {},
    });
    if (res.ok) return res.json();
  } catch { /* best-effort only */ }
  return null;
}

async function main() {
  console.log(`RastiChat staging load baseline`);
  console.log(`  ${VISITOR_COUNT} visitors x ${MESSAGES_PER_VISITOR} messages, ~${Math.round(UPLOAD_FRACTION * 100)}% also upload an image`);
  console.log(`  ${OPERATOR_CREDENTIALS.length} operator sessions (set OPERATOR_CREDENTIALS to include this dimension)`);
  console.log(`  Target: ${BASE_URL}`);
  console.log();
  console.log('NOTE: every simulated visitor shares this machine\'s single source IP, unlike');
  console.log('real visitors who each have their own — the per-IP widget_start/login/etc.');
  console.log('rate limits (see backend/config/settings.py) will trigger here at a traffic');
  console.log('level real, IP-diverse traffic would not hit. A burst of `start 429`s below is');
  console.log('very likely THIS artifact working as designed, not evidence the app can\'t');
  console.log('handle this many concurrent visitors — cross-check against');
  console.log('WIDGET_START_THROTTLE_RATE before concluding otherwise.');
  console.log();
  console.log('While this runs, ALSO capture server-side resource usage by hand (this script');
  console.log('cannot see the VPS from the outside):');
  console.log('  docker stats --no-stream   # CPU/RAM per container');
  console.log('  free -h                    # host memory');
  console.log('  docker compose exec db psql -U rastichat -c "SELECT count(*) FROM pg_stat_activity;"');
  console.log('  docker compose exec redis redis-cli info memory | grep used_memory_human');
  console.log();

  const before = await pollMonitoring();
  const startedAt = Date.now();

  const visitorPromises = Array.from({ length: VISITOR_COUNT }, (_, i) => simulateVisitor(i));
  const operatorPromises = OPERATOR_CREDENTIALS.map((cred, i) => simulateOperator(cred, i));
  await Promise.all([...visitorPromises, ...operatorPromises]);

  const durationSec = ((Date.now() - startedAt) / 1000).toFixed(1);
  const after = await pollMonitoring();

  console.log('=== Results ===');
  console.log(`Duration: ${durationSec}s`);
  console.log();
  console.log('Visitors:');
  console.log(`  attempted=${stats.visitors.attempted} init_ok=${stats.visitors.initOk} start_ok=${stats.visitors.startOk} ws_connected=${stats.visitors.wsConnected} ws_failed=${stats.visitors.wsFailed}`);
  console.log('Messages:');
  console.log(`  sent=${stats.messages.sent} echoed=${stats.messages.echoed} duplicates=${stats.messages.duplicates} missing=${stats.messages.sent - stats.messages.echoed - stats.messages.duplicates}`);
  console.log(`  latency p50=${percentile(stats.messages.latenciesMs, 50)}ms p95=${percentile(stats.messages.latenciesMs, 95)}ms p99=${percentile(stats.messages.latenciesMs, 99)}ms`);
  console.log('Uploads:');
  console.log(`  attempted=${stats.uploads.attempted} ok=${stats.uploads.ok} failed=${stats.uploads.failed}`);
  console.log(`  latency p50=${percentile(stats.uploads.latenciesMs, 50)}ms p95=${percentile(stats.uploads.latenciesMs, 95)}ms`);
  if (OPERATOR_CREDENTIALS.length) {
    console.log('Operators:');
    console.log(`  attempted=${stats.operators.attempted} login_ok=${stats.operators.loginOk} session_ok=${stats.operators.wsConnected} failed=${stats.operators.wsFailed}`);
  }
  const errorRate = ((stats.visitors.wsFailed + stats.uploads.failed + stats.operators.wsFailed) /
    Math.max(1, stats.visitors.attempted + stats.uploads.attempted + stats.operators.attempted) * 100).toFixed(1);
  console.log(`Error rate: ${errorRate}%`);
  if (before && after) {
    console.log('Disk usage (monitoring endpoint): before='
      + JSON.stringify(before.disk) + ' after=' + JSON.stringify(after.disk));
  }
  if (stats.errors.length) {
    console.log();
    console.log(`First ${Math.min(10, stats.errors.length)} errors of ${stats.errors.length}:`);
    stats.errors.slice(0, 10).forEach(e => console.log('  ' + e));
  }
  console.log();
  console.log('DISCLAIMER: this is a factual baseline from THIS run against THIS VPS at THIS');
  console.log('traffic level only. It is not a production capacity guarantee, does not account');
  console.log('for sustained/growing load, and a small VPS result here says nothing about');
  console.log('behavior at real production scale — see docs/runbooks/MONITORING_RUNBOOK.md.');
}

main();
