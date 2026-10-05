import { test, expect } from '@playwright/test';
import { ORIGINS, loginApi, openSocket, seed, staffTicket, widgetTicket, withTimeout, type Sock } from './wsclient';

// Two ASGI workers behind a balancer, one shared Redis channel layer: a message handled by one worker must reach sockets held by
// the other. The worker base URLs come from DJANGO52_WORKER_URLS="http://127.0.0.1:8101,http://127.0.0.1:8102" (plain HTTP on the
// stack's loopback; the spec sends X-Forwarded-Proto/Host exactly like nginx does).
test.beforeEach(({}, testInfo) => { test.skip(testInfo.project.name !== 'desktop-chromium', 'protocol-level spec: runs once'); });

const workers = (process.env.DJANGO52_WORKER_URLS || '').split(',').map((u) => u.trim()).filter(Boolean);
const HOST = new URL(process.env.SMOKE_BACKEND_URL || 'https://chat-stg.example.test').host;

async function viaWorker(base: string, path: string, init: RequestInit & { json?: unknown } = {}) {
  const headers: Record<string, string> = { Host: HOST, 'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '127.0.0.1', ...(init.headers as Record<string, string> || {}) };
  if (init.json !== undefined) { headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(init.json); }
  return fetch(`${base}${path}`, { ...init, headers });
}
const wsBase = (base: string) => base.replace(/^http/, 'ws') + '/ws';

async function operatorOn(base: string, other: string, conv: string): Promise<Sock> {
  const u = seed().users['agent-a1'];
  const jwt = await loginApi(u.email, u.password);
  // the ticket is minted by the OTHER worker: tickets live in Redis, not in a worker's memory
  const res = await viaWorker(other, '/api/v1/ws/ticket/', { method: 'POST', headers: { Authorization: `Bearer ${jwt}` }, json: { kind: 'dashboard_chat', conversation_id: conv } });
  expect(res.status).toBe(201);
  const sock = openSocket(`${wsBase(base)}/v2/dashboard/${conv}/`, { origin: ORIGINS.operator, ticket: (await res.json()).ticket });
  await withTimeout(sock.authed, 15000, 'operator auth.ok');
  return sock;
}
async function customerOn(base: string, other: string, conv: string, token: string): Promise<Sock> {
  const res = await viaWorker(other, '/api/v1/widget/ws-ticket/', { method: 'POST', headers: { Origin: ORIGINS.allowedEmbed, 'X-Widget-Session': token }, json: { conversation_id: conv } });
  expect(res.status).toBe(201);
  const sock = openSocket(`${wsBase(base)}/v2/widget/${conv}/`, { origin: ORIGINS.allowedEmbed, ticket: (await res.json()).ticket });
  await withTimeout(sock.authed, 15000, 'customer auth.ok');
  return sock;
}
const waitFor = async (sock: Sock, pred: (f: Record<string, unknown>) => boolean, what: string) => {
  await expect.poll(() => sock.frames.some(pred), { timeout: 15000, message: what }).toBe(true);
};

test.describe('multi-worker Daphne with a shared Redis channel layer', () => {
  test.skip(workers.length < 2, 'DJANGO52_WORKER_URLS needs two worker URLs');

  for (const [wOp, wCu] of [[0, 1], [1, 0]]) {
    test(`customer on worker ${wCu + 1} -> operator on worker ${wOp + 1} and back`, async () => {
      const { conversation, session_token } = seed().A;
      const operator = await operatorOn(workers[wOp], workers[wCu], conversation);
      const customer = await customerOn(workers[wCu], workers[wOp], conversation, session_token);
      try {
        const up = `fanout-up-${Date.now()}`;
        customer.ws.send(JSON.stringify({ message: up, client_message_id: `fo-${up}` }));
        await waitFor(operator, (f) => f.content === up, 'operator did not receive the customer message across workers');
        const down = `fanout-down-${Date.now()}`;
        operator.ws.send(JSON.stringify({ message: down, client_message_id: `fo-${down}` }));
        await waitFor(customer, (f) => f.content === down, 'customer did not receive the operator reply across workers');
      } finally { operator.ws.close(); customer.ws.close(); }
    });
  }

  test('a broadcast reaches every socket when sockets are spread over both workers (and through the balancer)', async () => {
    const { conversation, session_token } = seed().A;
    const sockets: Sock[] = [];
    for (let i = 0; i < 6; i++) sockets.push(await operatorOn(workers[i % 2], workers[(i + 1) % 2], conversation));
    const customer = await customerOn(workers[0], workers[1], conversation, session_token);
    try {
      const text = `fanout-all-${Date.now()}`;
      customer.ws.send(JSON.stringify({ message: text, client_message_id: `fo-${text}` }));
      for (const [i, sock] of sockets.entries()) await waitFor(sock, (f) => f.content === text, `operator socket ${i} (worker ${(i % 2) + 1}) missed the broadcast`);
    } finally { sockets.forEach((s) => s.ws.close()); customer.ws.close(); }
  });
});
