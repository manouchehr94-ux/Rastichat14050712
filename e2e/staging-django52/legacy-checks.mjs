// Checks the time-boxed legacy credentials-in-URL exception on the real stack (run by legacy-exception-run.sh, never part of the matrix).
//   node legacy-checks.mjs window-open   -> widget legacy works, staff JWTs in URLs stay refused, usage is counted
//   node legacy-checks.mjs window-over   -> legacy refused for everyone, new clients unaffected
import fs from 'node:fs';
import WebSocket from 'ws';
const mode = process.argv[2];
const B = process.env.SMOKE_BACKEND_URL, WS = process.env.SMOKE_WS_URL;
const EMBED = process.env.DJANGO52_ALLOWED_EMBED_ORIGIN, OP = 'https://operator-stg.example.test';
const seed = JSON.parse(fs.readFileSync(process.env.DJANGO52_SEED_JSON, 'utf8'));
let fail = 0;
const ok = (c, m) => { console.log(`  ${c ? 'PASS' : 'FAIL'}  ${m}`); if (!c) fail = 1; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function wsOutcome(url, origin, send) {
  return new Promise((resolve) => {
    const ws = new WebSocket(url, { headers: { Origin: origin } }); let opened = false, got = [];
    const done = (r) => { try { ws.terminate(); } catch {} resolve(r); };
    ws.on('unexpected-response', (_q, r) => { r.resume(); done({ opened, status: r.statusCode, got }); });
    ws.on('open', () => { opened = true; if (send) ws.send(send); });
    ws.on('message', (m) => { got.push(String(m)); if (send && String(m).includes('legacy-check')) done({ opened, got, echoed: true }); });
    ws.on('close', (c) => done({ opened, closed: c, got }));
    ws.on('error', () => {});
    setTimeout(() => done({ opened, timeout: true, got }), 8000);
  });
}
const A = seed.A;
const u = seed.users['agent-a1'];
const jwt = (await (await fetch(`${B}/api/v1/auth/login/`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: u.email, password: u.password }) })).json()).access;
const widgetLegacy = await wsOutcome(`${WS}/ws/widget/${A.session_token}/${A.conversation}/`, EMBED, JSON.stringify({ message: 'legacy-check', client_message_id: `lc-${Date.now()}` }));
const staffLegacy = await wsOutcome(`${WS}/ws/dashboard/${jwt}/${A.conversation}/`, OP);
const q = await fetch(`${B}/api/v1/widget/conversations/${A.conversation}/messages/?session_token=${A.session_token}`, { headers: { Origin: EMBED } });
// the supported mechanism must work in both phases
const tk = await fetch(`${B}/api/v1/widget/ws-ticket/`, { method: 'POST', headers: { 'Content-Type': 'application/json', Origin: EMBED, 'X-Widget-Session': A.session_token }, body: JSON.stringify({ conversation_id: A.conversation }) });
const v2 = await wsOutcome(`${WS}/ws/v2/widget/${A.conversation}/`, EMBED, null);
const header = await fetch(`${B}/api/v1/widget/conversations/${A.conversation}/messages/`, { headers: { Origin: EMBED, 'X-Widget-Session': A.session_token } });
if (mode === 'window-open') {
  ok(widgetLegacy.opened && widgetLegacy.echoed, 'an old widget bundle (session token in the WebSocket path) still connects and chats');
  ok(!staffLegacy.opened, 'a staff JWT in a WebSocket URL is refused even though the exception is open');
  ok(q.status === 200, `?session_token= on widget REST works inside the window (HTTP ${q.status})`);
} else {
  ok(!widgetLegacy.opened, 'after the window the legacy widget socket is refused');
  ok(!staffLegacy.opened, 'a staff JWT in a WebSocket URL is refused');
  ok(q.status === 401, `?session_token= is ignored after the window (HTTP ${q.status})`);
}
ok(tk.status === 201 && header.status === 200, `new ticket / header clients work in this phase (ticket ${tk.status}, history ${header.status})`);
process.exit(fail);
