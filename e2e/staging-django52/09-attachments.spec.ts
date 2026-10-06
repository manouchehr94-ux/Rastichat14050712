import { test, expect, type APIRequestContext } from '@playwright/test';
import { BACKEND_URL, ALLOWED_EMBED_ORIGIN, OPERATOR_EMAIL, OPERATOR_PASSWORD, PROJECT_KEY } from './env';

// P1-6 private attachments, through the real nginx/TLS/Daphne stack: signed URL, per-fetch authorization, Range, closed public paths.
const ORIGIN = ALLOWED_EMBED_ORIGIN;
// Node global; this branch does not depend on @types/node, so declare just what is used (module scope: no clash when it is present)
declare const Buffer: any;
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');

function wav(): any {
  const n = 16000; const data = Buffer.alloc(n * 2); for (let i = 0; i < n; i++) data.writeInt16LE(Math.round(8000 * Math.sin(i / 20)), i * 2);
  const h = Buffer.alloc(44); h.write('RIFF', 0); h.writeUInt32LE(36 + data.length, 4); h.write('WAVEfmt ', 8); h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22);
  h.writeUInt32LE(16000, 24); h.writeUInt32LE(32000, 28); h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34); h.write('data', 36); h.writeUInt32LE(data.length, 40);
  return Buffer.concat([h, data]);
}

async function visitor(request: APIRequestContext) {
  const init = await request.post(`${BACKEND_URL}/api/v1/widget/init/`, { data: { project_key: PROJECT_KEY }, headers: { Origin: ORIGIN } });
  expect(init.status()).toBe(200 === init.status() ? 200 : 201);
  const token = (await init.json()).session_token as string;
  const start = await request.post(`${BACKEND_URL}/api/v1/widget/start/`, { data: { session_token: token }, headers: { Origin: ORIGIN, 'X-Widget-Session': token } });
  expect(start.status()).toBe(200);
  return { token, convId: (await start.json()).id as string, headers: { 'X-Widget-Session': token, Origin: ORIGIN } };
}

async function upload(request: APIRequestContext, v: { convId: string; headers: Record<string, string> }, file: { name: string; mimeType: string; buffer: any }, type: 'IMAGE' | 'VOICE') {
  const res = await request.post(`${BACKEND_URL}/api/v1/widget/conversations/${v.convId}/upload/`, {
    headers: v.headers, multipart: { file, message_type: type, client_message_id: `att-${Date.now()}-${Math.floor(Math.random() * 1e6)}` },
  });
  expect(res.status(), await res.text()).toBe(201);
  return res.json() as Promise<{ id: string; attachment_url: string }>;
}

// nginx limits /auth/login/ to 10 requests/minute per address (burst 5): log each staff user in once per worker, not once per test
const tokens = new Map<string, string>();
async function staffToken(request: APIRequestContext, email = OPERATOR_EMAIL) {
  const cached = tokens.get(email);
  if (cached) return cached;
  const r = await request.post(`${BACKEND_URL}/api/v1/auth/login/`, { data: { email, password: OPERATOR_PASSWORD } });
  expect(r.status()).toBe(200);
  const access = (await r.json()).access as string;
  tokens.set(email, access);
  return access;
}

const abs = (u: string) => (u.startsWith('http') ? u : `${BACKEND_URL}${u}`);

test.describe('P1-6 private attachments (real nginx + Daphne)', () => {
  test.skip(!ORIGIN, 'needs DJANGO52_ALLOWED_EMBED_ORIGIN');

  test('image: signed URL 200 with private headers, tampered/missing/foreign signature all 404, old and direct paths 404', async ({ request }) => {
    const v = await visitor(request);
    const up = await upload(request, v, { name: 'p.png', mimeType: 'image/png', buffer: PNG }, 'IMAGE');
    expect(up.attachment_url).toContain('/api/v1/attachments/');
    expect(up.attachment_url).toContain('sig=');
    const ok = await request.get(abs(up.attachment_url));
    expect(ok.status()).toBe(200);
    expect(ok.headers()['content-type']).toContain('image/png');
    expect(ok.headers()['cache-control']).toBe('private, no-store'); // exactly once, no duplicate
    expect(ok.headers()['x-content-type-options']).toBe('nosniff');
    expect(ok.headers()['referrer-policy']).toBe('no-referrer');
    expect(ok.headers()['x-accel-redirect']).toBeUndefined(); // never leaks to the client
    expect((await ok.body()).equals(PNG)).toBe(true);

    const base = abs(up.attachment_url).split('?')[0];
    const sig = up.attachment_url.split('sig=')[1];
    expect((await request.get(base)).status()).toBe(404);
    expect((await request.get(`${base}?sig=${sig.slice(0, -3)}xyz`)).status()).toBe(404);
    expect((await request.get(`${base}?sig=garbage`)).status()).toBe(404);
    const other = await upload(request, v, { name: 'q.png', mimeType: 'image/png', buffer: PNG }, 'IMAGE');
    expect((await request.get(`${abs(other.attachment_url).split('?')[0]}?sig=${sig}`)).status()).toBe(404); // token of another message

    // the stored file name is never exposed by a public path
    const file = (ok.headers()['content-disposition'] || '');
    expect((await request.get(`${BACKEND_URL}/protected-media/attachments/x.png`)).status()).toBe(404);
    expect((await request.get(`${BACKEND_URL}/media/attachments/x.png`)).status()).toBe(404);
    expect(file).not.toMatch(/protected-media/);
  });

  test('voice: Range request answers 206 with Content-Range, suffix and open-ended ranges work, bad range is 416', async ({ request }) => {
    const v = await visitor(request);
    const body = wav();
    const up = await upload(request, v, { name: 'v.wav', mimeType: 'audio/wav', buffer: body }, 'VOICE');
    const url = abs(up.attachment_url);
    const full = await request.get(url);
    expect(full.status()).toBe(200);
    expect(full.headers()['accept-ranges']).toBe('bytes');
    const r1 = await request.get(url, { headers: { Range: 'bytes=0-99' } });
    expect(r1.status()).toBe(206);
    expect(r1.headers()['content-range']).toBe(`bytes 0-99/${body.length}`);
    expect((await r1.body()).equals(body.subarray(0, 100))).toBe(true);
    const r2 = await request.get(url, { headers: { Range: `bytes=${body.length - 50}-` } });
    expect(r2.status()).toBe(206);
    expect((await r2.body()).length).toBe(50);
    const r3 = await request.get(url, { headers: { Range: 'bytes=-10' } });
    expect(r3.status()).toBe(206);
    expect((await r3.body()).length).toBe(10);
    const bad = await request.get(url, { headers: { Range: `bytes=${body.length + 1000}-` } });
    expect(bad.status()).toBe(416);
  });

  test('staff: own workspace gets the file via history + refresh, another workspace gets 404; visitor refresh works; revoked session is cut off', async ({ request }) => {
    const v = await visitor(request);
    const up = await upload(request, v, { name: 'p.png', mimeType: 'image/png', buffer: PNG }, 'IMAGE');
    const jwt = await staffToken(request);
    const auth = { Authorization: `Bearer ${jwt}` };

    const refresh = await request.get(`${BACKEND_URL}/api/v1/attachments/${up.id}/refresh/`, { headers: auth });
    expect(refresh.status()).toBe(200);
    const fresh = (await refresh.json()).attachment_url as string;
    expect((await request.get(abs(fresh))).status()).toBe(200);

    const hist = await request.get(`${BACKEND_URL}/api/v1/conversations/${v.convId}/messages/`, { headers: auth });
    expect(hist.status()).toBe(200);
    const msg = (await hist.json() as Array<{ id: string; attachment_url?: string }>).find((m) => m.id === up.id);
    expect(msg?.attachment_url).toContain('sig=');
    expect((await request.get(abs(msg!.attachment_url!))).status()).toBe(200);

    const jwtB = await staffToken(request, 'agent-b1@example.test');
    expect((await request.get(`${BACKEND_URL}/api/v1/attachments/${up.id}/refresh/`, { headers: { Authorization: `Bearer ${jwtB}` } })).status()).toBe(404);
    expect((await request.get(`${BACKEND_URL}/api/v1/attachments/${up.id}/refresh/`)).status()).toBe(401);

    const vr = await request.get(`${BACKEND_URL}/api/v1/widget/attachments/${up.id}/refresh/`, { headers: v.headers });
    expect(vr.status()).toBe(200);
    const visitorUrl = (await vr.json()).attachment_url as string;
    expect((await request.get(abs(visitorUrl))).status()).toBe(200);

    // identity cut-off: a revoked visitor session loses access to NEW fetches immediately, even with a still-valid signature
    const rev = await request.post(`${BACKEND_URL}/api/v1/widget/session/revoke/`, { data: { session_token: v.token }, headers: { Origin: ORIGIN } });
    expect([200, 204]).toContain(rev.status());
    expect((await request.get(abs(visitorUrl))).status()).toBe(404);
    expect((await request.get(`${BACKEND_URL}/api/v1/widget/attachments/${up.id}/refresh/`, { headers: v.headers })).status()).toBeGreaterThanOrEqual(401);
  });

  test('browser: an <img> and an <audio> with signed URLs actually load and play-range through nginx', async ({ browser, request }) => {
    const v = await visitor(request);
    const img = await upload(request, v, { name: 'p.png', mimeType: 'image/png', buffer: PNG }, 'IMAGE');
    const aud = await upload(request, v, { name: 'v.wav', mimeType: 'audio/wav', buffer: wav() }, 'VOICE');
    const ctx = await browser.newContext();
    await ctx.route(`${ORIGIN}/**`, (route) => route.fulfill({ status: 200, contentType: 'text/html', body: `<img id=i src="${abs(img.attachment_url)}"><audio id=a controls preload=auto src="${abs(aud.attachment_url)}"></audio>` }));
    const page = await ctx.newPage();
    await page.goto(`${ORIGIN}/x`);
    await expect.poll(() => page.evaluate(() => (document.getElementById('i') as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
    await expect.poll(() => page.evaluate(() => (document.getElementById('a') as HTMLAudioElement).readyState), { timeout: 20000 }).toBeGreaterThanOrEqual(2);
    expect(await page.evaluate(() => (document.getElementById('a') as HTMLAudioElement).duration)).toBeGreaterThan(0.5);
    await ctx.close();
  });
});
