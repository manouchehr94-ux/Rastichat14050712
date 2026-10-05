// minimal real-stack customer flow: init -> start -> upload PNG -> ws-ticket -> websocket auth.ok -> send message -> echo; prints JSON
import { createRequire } from 'node:module';
const WebSocket = createRequire(new URL('../../../e2e/package.json', import.meta.url))('ws'); // ws is an e2e devDependency
const E = process.env, API = E.SMOKE_BACKEND_URL + '/api/v1', ORIGIN = E.DJANGO52_ALLOWED_EMBED_ORIGIN;
const J = async (u, o = {}) => { const r = await fetch(u, { ...o, headers: { ...(o.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...(o.headers || {}) }, body: o.body instanceof FormData ? o.body : o.body && JSON.stringify(o.body) }); const t = await r.text(); try { return { s: r.status, b: JSON.parse(t) }; } catch { return { s: r.status, b: t }; } };
const init = await J(API + '/widget/init/', { method: 'POST', body: { project_key: E.SMOKE_PROJECT_KEY }, headers: { Origin: ORIGIN } });
const tok = init.b.session_token, h = { 'X-Widget-Session': tok, Origin: ORIGIN };
const start = await J(API + '/widget/start/', { method: 'POST', body: { session_token: tok }, headers: h });
const conv = start.b.id;
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
const fd = new FormData(); fd.append('file', new Blob([png], { type: 'image/png' }), 'p.png'); fd.append('message_type', 'IMAGE'); fd.append('client_message_id', 'flow-' + Date.now());
const up = await J(`${API}/widget/conversations/${conv}/upload/`, { method: 'POST', body: fd, headers: h });
const tk = await J(API + '/widget/ws-ticket/', { method: 'POST', body: { conversation_id: conv }, headers: h });
const echo = await new Promise((res) => {
  const ws = new WebSocket(`${E.SMOKE_WS_URL}/v2/widget/${conv}/`, { headers: { Origin: ORIGIN } }); const txt = 'flow-' + Date.now(); let ok = false;
  ws.on('open', () => ws.send(JSON.stringify({ type: 'auth', ticket: tk.b.ticket })));
  ws.on('message', (d) => { const f = JSON.parse(String(d)); if (f.type === 'auth.ok') { ok = true; ws.send(JSON.stringify({ message: txt, client_message_id: 'f' + Date.now() })); } if (f.content === txt) { ws.close(); res({ authOk: ok, echoed: true }); } });
  ws.on('error', (e) => res({ authOk: ok, echoed: false, error: e.message })); setTimeout(() => { ws.close(); res({ authOk: ok, echoed: false, timeout: true }); }, 10000);
});
console.log(JSON.stringify({ init: init.s, start: start.s, upload: up.s, attachment_url: up.b.attachment_url, ticket: tk.s, ...echo }));
