import { test, expect, type Page } from '@playwright/test';
import { BACKEND_URL } from './env';
import { loginOperator, openWidgetAt, uniqueText } from './helpers';
import { ORIGINS, admin, loginApi, seed } from './wsclient';

// P1-6 on the real stack (nginx + TLS + Daphne): private chat attachments are signed, authorized on every fetch and streamed by nginx.
const ORIGIN = process.env.DJANGO52_ALLOWED_EMBED_ORIGIN || 'https://embed-allowed.example.test';
const CHAT_MEDIA_CLOSED = process.env.DJANGO52_CHAT_MEDIA_CLOSED === '1'; // the install ran without CHAT_ATTACHMENTS_PUBLIC=1

const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');

/** A real, playable 2-second mono WAV (8 kHz sine): the validator accepts audio/wav and every browser engine can decode it. */
function wavBytes(): Buffer {
  const rate = 8000, seconds = 2, n = rate * seconds;
  const data = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++) data.writeInt16LE(Math.round(Math.sin((2 * Math.PI * 440 * i) / rate) * 8000), i * 2);
  const header = Buffer.alloc(44);
  header.write('RIFF', 0); header.writeUInt32LE(36 + data.length, 4); header.write('WAVE', 8); header.write('fmt ', 12);
  header.writeUInt32LE(16, 16); header.writeUInt16LE(1, 20); header.writeUInt16LE(1, 22); header.writeUInt32LE(rate, 24);
  header.writeUInt32LE(rate * 2, 28); header.writeUInt16LE(2, 32); header.writeUInt16LE(16, 34); header.write('data', 36); header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

async function uploadAsCustomer(conv: string, session: string, bytes: Buffer, mime: string, type: 'IMAGE' | 'VOICE') {
  const form = new FormData();
  form.append('file', new Blob([new Uint8Array(bytes)], { type: mime }), type === 'IMAGE' ? 'p.png' : 'v.wav');
  form.append('message_type', type);
  form.append('client_message_id', `att-${Date.now()}-${Math.floor(Math.random() * 1e6)}`);
  if (type === 'VOICE') form.append('duration', '2');
  const res = await fetch(`${BACKEND_URL}/api/v1/widget/conversations/${conv}/upload/`, { method: 'POST', headers: { 'X-Widget-Session': session, Origin: ORIGIN }, body: form });
  expect(res.status, 'customer upload').toBe(201);
  return (await res.json()) as { id: string; attachment_url: string };
}

async function openSeededConversationAsOperator(page: Page) {
  await loginOperator(page);
  const row = page.getByText('سلام، سفارش').first();
  await expect(row).toBeVisible({ timeout: 30000 });
  await row.click();
  await expect(page.locator('input[placeholder="پاسخ به مشتری…"]')).toBeVisible({ timeout: 15000 });
}

test.describe('private attachments in real browsers (Chromium engine; desktop, Pixel 7 and iPhone 13 emulation)', () => {
  test('an image sent by the customer shows in the widget and in the operator dashboard through signed URLs only', async ({ browser }) => {
    const customerCtx = await browser.newContext(); const operatorCtx = await browser.newContext();
    const customer = await customerCtx.newPage(); const operator = await operatorCtx.newPage();
    const requested: string[] = [];
    for (const p of [customer, operator]) p.on('request', (r) => requested.push(r.url()));
    await openWidgetAt(customer, ORIGIN);
    await customer.setInputFiles('#rasti-file', { name: 'receipt.png', mimeType: 'image/png', buffer: PNG });
    const own = customer.locator('.rasti-img img');
    await expect(own).toBeVisible({ timeout: 20000 });
    await expect.poll(() => own.evaluate((el: HTMLImageElement) => el.naturalWidth), { timeout: 20000 }).toBeGreaterThan(0);
    expect(await own.getAttribute('src')).toMatch(/\/api\/v1\/attachments\/[0-9a-f-]{36}\/\?sig=/);

    await loginOperator(operator);
    // the customer's conversation is listed with the image as its last message; open it
    const row = operator.locator('div,li,button').filter({ hasText: /تصویر|عکس|📷|🖼/ }).first();
    await expect(row).toBeVisible({ timeout: 30000 });
    await row.click();
    const shown = operator.locator('img[src*="/api/v1/attachments/"]').first();
    await expect(shown).toBeVisible({ timeout: 20000 });
    await expect.poll(() => shown.evaluate((el: HTMLImageElement) => el.naturalWidth), { timeout: 20000 }).toBeGreaterThan(0);
    expect(requested.filter((u) => u.includes('/media/attachments/')), 'no page may ever request the public media path').toEqual([]);
    await customerCtx.close(); await operatorCtx.close();
  });

  test('a voice note plays in the operator dashboard: nginx answers Range requests (206) behind the authorization check', async ({ browser }) => {
    const A = seed().A;
    await uploadAsCustomer(A.conversation, A.session_token, wavBytes(), 'audio/wav', 'VOICE');
    const ctx = await browser.newContext(); const operator = await ctx.newPage();
    const statuses: number[] = [];
    operator.on('response', (r) => { if (r.url().includes('/api/v1/attachments/') && !r.url().includes('/refresh/')) statuses.push(r.status()); });
    await openSeededConversationAsOperator(operator);
    const audio = operator.locator('audio[src*="/api/v1/attachments/"]').last();
    await expect(audio).toBeAttached({ timeout: 20000 });
    await operator.locator('button:has-text("▶")').last().click();
    await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.currentTime), { timeout: 20000, message: 'the voice note never started playing' }).toBeGreaterThan(0.3);
    expect(await audio.evaluate((a: HTMLAudioElement) => a.error)).toBeNull();
    expect(statuses.some((s) => s === 206 || s === 200), `attachment responses: ${statuses}`).toBe(true);
    await ctx.close();
  });

  test('an expired or damaged signature is recovered once with a fresh URL (dashboard and widget)', async ({ browser }) => {
    const A = seed().A;
    const msg = await uploadAsCustomer(A.conversation, A.session_token, PNG, 'image/png', 'IMAGE');
    // dashboard
    const ctx = await browser.newContext(); const operator = await ctx.newPage();
    await openSeededConversationAsOperator(operator);
    const img = operator.locator(`img[src*="${msg.id}"]`).first();
    await expect(img).toBeVisible({ timeout: 20000 });
    await img.evaluate((el: HTMLImageElement) => { el.setAttribute('src', el.getAttribute('src')!.replace(/sig=.*/, 'sig=expired-or-tampered')); });
    await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.getAttribute('src')), { timeout: 20000 }).not.toContain('expired-or-tampered');
    await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth), { timeout: 20000 }).toBeGreaterThan(0);
    await ctx.close();
    // widget
    const wctx = await browser.newContext(); const customer = await wctx.newPage();
    await wctx.addInitScript((token) => localStorage.setItem('rasti_session', token), A.session_token);
    await openWidgetAt(customer, ORIGIN);
    const wimg = customer.locator(`.rasti-img img[src*="${msg.id}"]`).first();
    await expect(wimg).toBeVisible({ timeout: 20000 });
    await wimg.evaluate((el: HTMLImageElement) => { el.setAttribute('src', el.getAttribute('src')!.replace(/sig=.*/, 'sig=expired-or-tampered')); });
    await expect.poll(() => wimg.evaluate((el: HTMLImageElement) => el.getAttribute('src')), { timeout: 20000 }).not.toContain('expired-or-tampered');
    await expect.poll(() => wimg.evaluate((el: HTMLImageElement) => el.naturalWidth), { timeout: 20000 }).toBeGreaterThan(0);
    await wctx.close();
  });
});

test.describe('private attachments at the protocol level (real nginx; runs once)', () => {
  test.describe.configure({ mode: 'serial' });
  test.beforeEach(({}, testInfo) => { test.skip(testInfo.project.name !== 'desktop-chromium', 'protocol-level spec: runs once'); });

  const get = (url: string, headers: Record<string, string> = {}) => fetch(url, { headers, redirect: 'manual' });
  const refresh = async (id: string, headers: Record<string, string>) => {
    const res = await fetch(`${BACKEND_URL}/api/v1/attachments/${id}/refresh/`, { headers });
    return { status: res.status, url: res.status === 200 ? ((await res.json()).attachment_url as string) : '' };
  };

  test('headers, content type, Range, tampering, the internal location and the old public path', async () => {
    const A = seed().A;
    const up = await uploadAsCustomer(A.conversation, A.session_token, PNG, 'image/png', 'IMAGE');
    const url = up.attachment_url;
    expect(url).toMatch(/\/api\/v1\/attachments\/.+\?sig=/);
    expect(url).not.toContain('/media/');

    const ok = await get(url);
    expect(ok.status).toBe(200);
    expect(ok.headers.get('content-type')).toBe('image/png');
    expect(ok.headers.get('cache-control')).toBe('private, no-store');
    expect(ok.headers.get('x-content-type-options')).toContain('nosniff');
    expect(ok.headers.get('referrer-policy')).toBeTruthy();
    expect((await ok.arrayBuffer()).byteLength).toBe(PNG.length);

    const range = await get(url, { Range: 'bytes=0-9' });
    expect(range.status).toBe(206);
    expect(range.headers.get('content-range')).toBe(`bytes 0-9/${PNG.length}`);
    expect((await range.arrayBuffer()).byteLength).toBe(10);

    expect((await get(url.replace(/sig=.*/, 'sig=tampered'))).status).toBe(404);
    expect((await get(url.split('?')[0])).status).toBe(404); // no signature at all
    const token = url.split('sig=')[1];
    expect((await get(url.replace(up.id, '00000000-0000-0000-0000-000000000000'))).status).toBe(404); // signature for another message
    expect(token.length).toBeGreaterThan(30);

    // the stored file name never appears in anything a client is given, and the internal location is not reachable from outside
    const direct = await get(`${BACKEND_URL}/protected-media/attachments/2026/10/05/anything.png`);
    expect(direct.status).toBe(404);
    if (CHAT_MEDIA_CLOSED) {
      const oldStyle = await get(`${BACKEND_URL}/media/attachments/2026/10/05/anything.png`);
      expect(oldStyle.status).toBe(404);
    }
  });

  test('access is cut at once, per identity: revoked session, removed member, other store, inactive user', async () => {
    const S = seed();
    const A = S.A;
    const up = await uploadAsCustomer(A.conversation, A.session_token, PNG, 'image/png', 'IMAGE');
    const victim = S.users['victim-a'];
    const jwt = await loginApi(victim.email, victim.password);
    const staff = await refresh(up.id, { Authorization: `Bearer ${jwt}` });
    expect(staff.status).toBe(200);
    const visitor = await refresh(up.id, { 'X-Widget-Session': A.session_token, Origin: ORIGIN });
    expect(visitor.status).toBe(200);
    expect((await get(staff.url)).status).toBe(200);
    expect((await get(visitor.url)).status).toBe(200);

    // another store's staff and visitor are refused outright
    const adminB = S.users['admin-b'];
    expect((await refresh(up.id, { Authorization: `Bearer ${await loginApi(adminB.email, adminB.password)}` })).status).toBe(404);
    expect((await refresh(up.id, { 'X-Widget-Session': S.B.session_token, Origin: ORIGIN })).status).toBe(404);
    expect((await refresh(up.id, {})).status).toBe(401);

    try {
      // membership removed: the URL minted for the member is dead on the next request; the customer's is unaffected
      admin('remove-membership', victim.email);
      expect((await get(staff.url)).status).toBe(404);
      expect((await get(visitor.url)).status).toBe(200);
      expect((await refresh(up.id, { Authorization: `Bearer ${jwt}` })).status).toBe(404);
    } finally { admin('restore-membership', victim.email); }
    expect((await get(staff.url)).status).toBe(200); // re-granted: the same (still unexpired) URL works again

    try {
      admin('deactivate-user', victim.email);
      expect((await get(staff.url)).status).toBe(404);
    } finally { admin('activate-user', victim.email); }

    try {
      // customer session revoked: their URL dies, staff keep access
      admin('revoke-visitor-session', A.session_token);
      expect((await get(visitor.url)).status).toBe(404);
      expect((await get(staff.url)).status).toBe(200);
      expect((await refresh(up.id, { 'X-Widget-Session': A.session_token, Origin: ORIGIN })).status).toBe(401);
    } finally { admin('restore-visitor-session', A.session_token); }
    expect((await get(visitor.url)).status).toBe(200);

    try {
      // workspace deactivated: everybody is cut
      admin('deactivate-workspace');
      expect((await get(staff.url)).status).toBe(404);
      expect((await get(visitor.url)).status).toBe(404);
    } finally { admin('activate-workspace'); }
    expect((await get(staff.url)).status).toBe(200);
  });
});
