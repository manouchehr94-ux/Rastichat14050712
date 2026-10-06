import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import './main';

// Tests for the remotely configured behaviour: launcher, start modes, pre-chat form, trusted identity bootstrap.

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = FakeWebSocket.OPEN;
  url: string;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onopen: (() => void) | null = null;
  sent: string[] = [];
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
    queueMicrotask(() => this.onopen?.());
  }
  send(data: string) {
    const parsed = (() => { try { return JSON.parse(data); } catch { return null; } })();
    if (parsed?.type === 'auth') { queueMicrotask(() => this.onmessage?.({ data: JSON.stringify({ type: 'auth.ok' }) })); return; }
    this.sent.push(data);
  }
  close() { this.readyState = 3; this.onclose?.(); }
}

const res = (body: unknown, status = 200) => Promise.resolve({
  ok: status < 400, status, json: () => Promise.resolve(body), clone() { return res(body, status); },
} as unknown as Response);

async function flush(times = 6) {
  for (let i = 0; i < times; i++) { await Promise.resolve(); await new Promise((r) => setTimeout(r, 0)); }
}

const baseConfig = () => ({
  version: 1,
  launcher: { enabled: true, mode: 'icon', position: 'bottom-right', offset: { x: 20, y: 20 }, label: '', tooltip: '', icon: 'chat',
    color: '#BC5A38', greeting: '', auto_open: false, mobile: { fullscreen: true } },
  visibility: { hide_on_paths: [], show_on_paths: [] },
  behavior: { start_mode: 'on_first_message' },
  pre_chat: { enabled: false, title: '', submit_label: '', fields: [] },
  identity: { guest_allowed: true, authenticated_only: false },
  locale: 'fa', direction: 'rtl',
  capabilities: { attachments: true, voice: true, emoji: true, rating: true },
});

type Cfg = ReturnType<typeof baseConfig>;
const deepMerge = (a: Record<string, unknown>, b: Record<string, unknown>): Record<string, unknown> => {
  const out = { ...a };
  for (const [k, v] of Object.entries(b)) {
    out[k] = v && typeof v === 'object' && !Array.isArray(v) && a[k] && typeof a[k] === 'object' ? deepMerge(a[k] as Record<string, unknown>, v as Record<string, unknown>) : v;
  }
  return out;
};

describe('RastiChat widget — remote configuration', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let fetchMock: any;
  let config: Cfg;
  let startResponses: Array<() => Promise<Response>>;

  const calls = (needle: string) => fetchMock.mock.calls.filter((c: unknown[]) => String(c[0]).includes(needle));
  const startBodies = () => calls('/widget/start/').map((c: unknown[]) => JSON.parse((c[1] as { body: string }).body));

  const setConfig = (patch: Record<string, unknown>) => { config = deepMerge(baseConfig() as unknown as Record<string, unknown>, patch) as unknown as Cfg; };

  async function init(extra: Record<string, unknown> = {}) {
    window.RastiChat.init({ projectKey: 'proj-1', ...extra });
    await flush();
  }

  beforeEach(() => {
    window.RastiChat?.destroy?.();
    document.body.innerHTML = '';
    localStorage.clear();
    sessionStorage.clear();
    window.history.pushState({}, '', '/');
    FakeWebSocket.instances = [];
    // @ts-expect-error test double
    global.WebSocket = FakeWebSocket;
    config = baseConfig();
    startResponses = [];
    fetchMock = vi.fn((url: string) => {
      if (url.includes('/widget/config/')) return res(config);
      if (url.includes('/identity/customer/')) return res({ session_token: 'verified-1', visitor_id: 'v1' });
      if (url.includes('/widget/init/')) return res({ session_token: 'guest-1' });
      if (url.includes('/widget/start/')) return startResponses.length ? startResponses.shift()!() : res({ id: 'conv-1' });
      if (url.includes('/widget/ws-ticket/')) return res({ ticket: 't1' });
      if (url.includes('/messages/')) return res([]);
      return res({});
    });
    // @ts-expect-error test double
    global.fetch = fetchMock;
  });

  afterEach(() => { window.RastiChat?.destroy?.(); vi.restoreAllMocks(); });

  it('applies the launcher configuration: icon+text, position, offsets, colour, icon, tooltip, accessibility', async () => {
    setConfig({ launcher: { mode: 'icon_text', label: 'Help', tooltip: 'Chat with us', icon: 'headset', position: 'bottom-left', offset: { x: 8, y: 90 }, color: '#112233' }, locale: 'en', direction: 'ltr' });
    await init();
    const launcher = document.getElementById('rasti-launcher')!;
    expect(launcher.textContent).toContain('Help');
    expect(launcher.textContent).toContain('🎧');
    expect(launcher.classList.contains('has-label')).toBe(true);
    expect(launcher.getAttribute('aria-label')).toBe('Chat with us');
    expect(launcher.title).toBe('Chat with us');
    expect(launcher.getAttribute('role')).toBe('button');
    const css = document.getElementById('rasti-style')!.textContent!;
    expect(css).toContain('bottom: 90px; left: 8px;');
    expect(css).toContain('#112233');
    expect(css).toContain('direction: ltr');
  });

  it('explicit init() options still override the remote launcher configuration', async () => {
    setConfig({ launcher: { position: 'bottom-left', color: '#112233' } });
    await init({ position: 'right', primaryColor: '#ABCDEF' });
    const css = document.getElementById('rasti-style')!.textContent!;
    expect(css).toContain('right: 20px');
    expect(css).toContain('#ABCDEF');
  });

  it('a disabled launcher is not rendered visibly and makes no session or chat request', async () => {
    setConfig({ launcher: { enabled: false } });
    await init();
    expect((document.getElementById('rasti-container') as HTMLElement).style.display).toBe('none');
    expect(calls('/widget/init/')).toHaveLength(0);
    expect(calls('/widget/start/')).toHaveLength(0);
  });

  it('falls back to the original behaviour when the configuration endpoint is unavailable', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/widget/config/')) return Promise.reject(new Error('offline'));
      if (url.includes('/widget/init/')) return res({ session_token: 'guest-1' });
      if (url.includes('/widget/start/')) return res({ id: 'conv-1' });
      if (url.includes('/widget/ws-ticket/')) return res({ ticket: 't1' });
      return res([]);
    });
    await init();
    expect(calls('/widget/start/')).toHaveLength(1);
    expect(localStorage.getItem('rasti_session')).toBe('guest-1');
  });

  it('honours show/hide path rules and re-evaluates them on navigation', async () => {
    setConfig({ visibility: { hide_on_paths: ['/checkout*'] } });
    window.history.pushState({}, '', '/checkout/step-2');
    await init();
    const container = () => document.getElementById('rasti-container') as HTMLElement;
    expect(container().style.display).toBe('none');
    window.history.pushState({}, '', '/shop');
    window.dispatchEvent(new PopStateEvent('popstate'));
    expect(container().style.display).toBe('');
    window.RastiChat.destroy();
    setConfig({ visibility: { show_on_paths: ['/support*'] } });
    window.history.pushState({}, '', '/shop');
    await init();
    expect(container().style.display).toBe('none');             // not on an allowed path
    window.history.pushState({}, '', '/support/help');
    window.dispatchEvent(new PopStateEvent('popstate'));
    expect(container().style.display).toBe('');
  });

  it('shows the greeting once, dismisses it, and opens the panel from it', async () => {
    setConfig({ launcher: { greeting: 'سلام! کمک می‌خواهید؟' } });
    await init();
    const greeting = document.getElementById('rasti-greeting') as HTMLElement;
    expect(greeting.hidden).toBe(false);
    expect(greeting.textContent).toContain('سلام');
    (greeting.querySelector('.rasti-g-x') as HTMLElement).click();
    expect(greeting.hidden).toBe(true);
    window.RastiChat.destroy();
    await init();
    expect((document.getElementById('rasti-greeting') as HTMLElement).hidden).toBe(true);   // not shown again this session
  });

  it('auto-opens once per browser session', async () => {
    setConfig({ launcher: { auto_open: true } });
    await init();
    expect(document.getElementById('rasti-panel')!.classList.contains('open')).toBe(true);
    window.RastiChat.destroy();
    await init();
    expect(document.getElementById('rasti-panel')!.classList.contains('open')).toBe(false);
  });

  it('is keyboard operable: Enter/Space toggle, Escape closes, aria-expanded follows', async () => {
    await init();
    const launcher = document.getElementById('rasti-launcher')!;
    const panel = document.getElementById('rasti-panel')!;
    launcher.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(panel.classList.contains('open')).toBe(true);
    expect(launcher.getAttribute('aria-expanded')).toBe('true');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(panel.classList.contains('open')).toBe(false);
    expect(launcher.getAttribute('aria-expanded')).toBe('false');
    launcher.dispatchEvent(new KeyboardEvent('keydown', { key: ' ' }));
    expect(panel.classList.contains('open')).toBe(true);
  });

  it('capability flags hide attachment, voice and emoji controls', async () => {
    setConfig({ capabilities: { attachments: false, voice: false, emoji: false } });
    await init();
    for (const id of ['rasti-attach-btn', 'rasti-mic-btn', 'rasti-emoji-btn']) {
      expect((document.getElementById(id) as HTMLElement).style.display).toBe('none');
    }
  });

  describe('start modes (Mode A: no questions)', () => {
    it('on_first_message: nothing is created until the visitor sends a message, then the queued message is delivered in order', async () => {
      await init();
      expect(calls('/widget/init/')).toHaveLength(0);
      expect(calls('/widget/start/')).toHaveLength(0);
      (document.getElementById('rasti-launcher') as HTMLElement).click();
      const input = document.getElementById('rasti-input') as HTMLInputElement;
      input.value = 'I have a question about my order.';
      (document.getElementById('rasti-send') as HTMLElement).click();
      await flush(10);
      expect(calls('/widget/init/')).toHaveLength(1);
      expect(startBodies()).toEqual([{ session_token: 'guest-1' }]);                     // no pre_chat, not a peek
      const ws = FakeWebSocket.instances[0];
      expect(ws.sent.map((m) => JSON.parse(m).message)).toEqual(['I have a question about my order.']);
      expect(input.value).toBe('');
    });

    it('on_open: opening the panel creates the conversation', async () => {
      setConfig({ behavior: { start_mode: 'on_open' } });
      await init();
      expect(calls('/widget/start/')).toHaveLength(0);
      (document.getElementById('rasti-launcher') as HTMLElement).click();
      await flush(10);
      expect(startBodies()).toHaveLength(1);
      expect(FakeWebSocket.instances).toHaveLength(1);
    });

    it('a returning visitor (stored session) only PEEKS: history restored, nothing created when there is none', async () => {
      localStorage.setItem('rasti_session', 'stored');
      startResponses = [() => res({ id: null })];
      await init();
      expect(startBodies()).toEqual([{ session_token: 'stored', create: false }]);
      expect(FakeWebSocket.instances).toHaveLength(0);
    });

    it('a returning visitor with an open conversation gets history and the socket straight away', async () => {
      localStorage.setItem('rasti_session', 'stored');
      await init();
      expect(startBodies()[0].create).toBe(false);
      expect(FakeWebSocket.instances).toHaveLength(1);
    });

    it('legacy start mode on_load is unchanged (conversation created on page load)', async () => {
      setConfig({ behavior: { start_mode: 'on_load' } });
      await init();
      expect(calls('/widget/init/')).toHaveLength(1);
      expect(startBodies()).toEqual([{ session_token: 'guest-1' }]);
    });
  });

  describe('pre-chat form (Modes B and C)', () => {
    const form = {
      pre_chat: { enabled: true, title: 'Before we start', submit_label: 'Go', fields: [
        { key: 'topic', type: 'select', label: 'Topic', required: true, choices: [{ value: 'order', label: 'Order' }, { value: 'billing', label: 'Billing' }] },
        { key: 'email', type: 'email', label: 'Email' },
        { key: 'agree', type: 'consent', label: 'I agree to the terms', required: true },
        { key: 'page', type: 'hidden', label: '' },
      ] },
    };
    const open = () => (document.getElementById('rasti-launcher') as HTMLElement).click();
    const field = (key: string) => document.getElementById(`rasti-pc-${key}`) as HTMLInputElement;

    it('opening the panel shows the form instead of the composer; no request is made yet', async () => {
      setConfig(form);
      await init();
      open();
      const panel = document.getElementById('rasti-panel')!;
      expect(panel.classList.contains('rasti-prechat-on')).toBe(true);
      expect(document.querySelector('#rasti-prechat h2')!.textContent).toBe('Before we start');
      expect(document.querySelector('#rasti-prechat .rasti-submit')!.textContent).toBe('Go');
      expect(field('page')).toBeNull();                                          // hidden fields are never rendered
      expect(calls('/widget/start/')).toHaveLength(0);
    });

    it('validates required fields client-side with accessible messages and does not proceed', async () => {
      setConfig(form);
      await init();
      open();
      (document.querySelector('#rasti-prechat form') as HTMLFormElement).dispatchEvent(new Event('submit', { cancelable: true }));
      expect(document.getElementById('rasti-panel')!.classList.contains('rasti-prechat-on')).toBe(true);
      expect(field('topic').getAttribute('aria-invalid')).toBe('true');
      expect(field('topic').getAttribute('aria-required')).toBe('true');
      expect(document.querySelector('#rasti-prechat [role=alert]')).not.toBeNull();
      expect(document.getElementById('rasti-pc-topic-err')!.textContent).toBe('این فیلد الزامی است.');
      field('topic').value = 'order';
      field('email').value = 'nope';
      (document.querySelector('#rasti-prechat form') as HTMLFormElement).dispatchEvent(new Event('submit', { cancelable: true }));
      expect(document.getElementById('rasti-pc-email-err')!.textContent).toContain('ایمیل');
    });

    it('sends the answers (plus hidden page context) with the conversation-creating call on the first message', async () => {
      setConfig(form);
      await init({ context: { page: '/orders/17' } });
      open();
      field('topic').value = 'billing';
      field('agree').checked = true;
      (document.querySelector('#rasti-prechat form') as HTMLFormElement).dispatchEvent(new Event('submit', { cancelable: true }));
      expect(document.getElementById('rasti-panel')!.classList.contains('rasti-prechat-on')).toBe(false);
      expect(calls('/widget/start/')).toHaveLength(0);                            // still nothing created
      const input = document.getElementById('rasti-input') as HTMLInputElement;
      input.value = 'hello';
      (document.getElementById('rasti-send') as HTMLElement).click();
      await flush(10);
      expect(startBodies()).toEqual([{ session_token: 'guest-1', pre_chat: { topic: 'billing', agree: true, page: '/orders/17' } }]);
    });

    it('re-opens the form with the server\'s per-field messages when the answers are refused', async () => {
      setConfig(form);
      startResponses = [() => res({ code: 'pre_chat_invalid', errors: { topic: 'Choose one of the listed options.' } }, 400)];
      await init();
      open();
      field('topic').value = 'order';
      field('agree').checked = true;
      (document.querySelector('#rasti-prechat form') as HTMLFormElement).dispatchEvent(new Event('submit', { cancelable: true }));
      (document.getElementById('rasti-input') as HTMLInputElement).value = 'hi';
      (document.getElementById('rasti-send') as HTMLElement).click();
      await flush(10);
      expect(document.getElementById('rasti-panel')!.classList.contains('rasti-prechat-on')).toBe(true);
      expect(document.getElementById('rasti-pc-topic-err')!.textContent).toBe('Choose one of the listed options.');
      expect(FakeWebSocket.instances).toHaveLength(0);
    });

    it('one-question mode is just a form with a single field', async () => {
      setConfig({ pre_chat: { enabled: true, fields: [{ key: 'help', type: 'text', label: 'What can we help you with?', required: true }] } });
      await init();
      open();
      expect(document.querySelectorAll('#rasti-prechat .rasti-field')).toHaveLength(1);
      field('help').value = 'Where is my parcel?';
      (document.querySelector('#rasti-prechat form') as HTMLFormElement).dispatchEvent(new Event('submit', { cancelable: true }));
      expect(document.getElementById('rasti-panel')!.classList.contains('rasti-prechat-on')).toBe(false);
    });

    it('configuration text is never interpreted as HTML', async () => {
      setConfig({ pre_chat: { enabled: true, title: '<img src=x onerror=alert(1)>', fields: [{ key: 'q', type: 'text', label: '<b>bold</b>' }] } });
      await init();
      open();
      expect(document.querySelector('#rasti-prechat img')).toBeNull();
      expect(document.querySelector('#rasti-prechat b')).toBeNull();
      expect(document.querySelector('#rasti-prechat h2')!.textContent).toBe('<img src=x onerror=alert(1)>');
    });

    it('does not ask again once a conversation exists', async () => {
      setConfig(form);
      localStorage.setItem('rasti_session', 'stored');
      await init();
      open();
      expect(document.getElementById('rasti-panel')!.classList.contains('rasti-prechat-on')).toBe(false);
    });
  });

  describe('trusted identity bootstrap', () => {
    const assertion = (sub: string) => `h.${btoa(JSON.stringify({ sub })).replace(/=+$/, '')}.s`;

    it('exchanges the host-signed assertion instead of creating a guest session, and remembers who it belongs to', async () => {
      const bootstrap = vi.fn(async () => assertion('cust-42'));
      setConfig({ behavior: { start_mode: 'on_load' } });
      await init({ bootstrap });
      expect(calls('/widget/init/')).toHaveLength(0);
      const [url, opts] = calls('/identity/customer/')[0];
      expect(url).toContain('/identity/customer/');
      expect(JSON.parse(opts.body)).toEqual({ project_key: 'proj-1', assertion: assertion('cust-42') });
      expect(localStorage.getItem('rasti_session')).toBe('verified-1');
      expect(localStorage.getItem('rasti_session_sub')).toBe('cust-42');
      expect(startBodies()[0].session_token).toBe('verified-1');
    });

    it('resumes the stored session for the same person without another exchange', async () => {
      localStorage.setItem('rasti_session', 'verified-old');
      localStorage.setItem('rasti_session_sub', 'cust-42');
      await init({ bootstrap: async () => assertion('cust-42') });
      expect(calls('/identity/customer/')).toHaveLength(0);
      expect(startBodies()[0].session_token).toBe('verified-old');
    });

    it('a different signed-in person on the same browser never inherits the previous customer\'s session', async () => {
      localStorage.setItem('rasti_session', 'verified-old');
      localStorage.setItem('rasti_session_sub', 'cust-42');
      await init({ bootstrap: async () => assertion('cust-99') });
      expect(calls('/widget/session/revoke/')).toHaveLength(1);
      expect(JSON.parse(calls('/widget/session/revoke/')[0][1].body)).toEqual({ session_token: 'verified-old' });
      expect(localStorage.getItem('rasti_session_sub')).toBe('cust-99');
      expect(localStorage.getItem('rasti_session')).toBe('verified-1');
    });

    it('a page without bootstrap (host user signed out) drops a leftover verified session', async () => {
      localStorage.setItem('rasti_session', 'verified-old');
      localStorage.setItem('rasti_session_sub', 'cust-42');
      await init();
      expect(calls('/widget/session/revoke/')).toHaveLength(1);
      expect(localStorage.getItem('rasti_session_sub')).toBeNull();
    });

    it('offers an existing guest session for upgrade (sent in a header, never merged by id)', async () => {
      localStorage.setItem('rasti_session', 'guest-old');
      await init({ bootstrap: async () => assertion('cust-42') });
      const [, opts] = calls('/identity/customer/')[0];
      expect(opts.headers['X-Widget-Session']).toBe('guest-old');
    });

    it('a signed-out host page keeps working as a guest when guests are allowed', async () => {
      setConfig({ behavior: { start_mode: 'on_load' } });
      await init({ bootstrap: async () => null });
      expect(calls('/identity/customer/')).toHaveLength(0);
      expect(localStorage.getItem('rasti_session')).toBe('guest-1');
    });

    it('a bootstrap that throws is treated as signed out, never as a crash', async () => {
      setConfig({ behavior: { start_mode: 'on_load' } });
      const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
      await init({ bootstrap: async () => { throw new Error('host backend down'); } });
      expect(localStorage.getItem('rasti_session')).toBe('guest-1');
      spy.mockRestore();
    });

    it('authenticated-only projects never open a guest session and tell the visitor to sign in', async () => {
      setConfig({ identity: { guest_allowed: false, authenticated_only: true }, behavior: { start_mode: 'on_load' } });
      await init({ bootstrap: async () => null });
      expect(calls('/widget/init/')).toHaveLength(0);
      expect(document.getElementById('rasti-notice')!.textContent).toContain('وارد');
      expect(document.getElementById('rasti-launcher')!.classList.contains('rasti-unavailable')).toBe(true);
    });

    it('renews an expired verified session by asking the host for a fresh assertion', async () => {
      localStorage.setItem('rasti_session', 'verified-old');
      localStorage.setItem('rasti_session_sub', 'cust-42');
      setConfig({ behavior: { start_mode: 'on_load' } });
      startResponses = [() => res({ code: 'session_invalid' }, 401)];
      const bootstrap = vi.fn(async () => assertion('cust-42'));
      await init({ bootstrap });
      expect(bootstrap).toHaveBeenCalledTimes(2);                                 // once at boot, once for the renewal
      expect(calls('/identity/customer/')).toHaveLength(1);
      expect(localStorage.getItem('rasti_session')).toBe('verified-1');
    });
  });

  describe('unavailable / offline', () => {
    it('shows an unavailable state when no session can be opened', async () => {
      setConfig({ behavior: { start_mode: 'on_load' } });
      fetchMock.mockImplementation((url: string) => {
        if (url.includes('/widget/config/')) return res(config);
        return Promise.reject(new Error('network down'));
      });
      const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
      await init();
      expect(document.getElementById('rasti-notice')!.classList.contains('show')).toBe(true);
      expect(document.getElementById('rasti-launcher')!.classList.contains('rasti-unavailable')).toBe(true);
      spy.mockRestore();
    });

    it('destroy removes the widget and its listeners', async () => {
      await init();
      window.RastiChat.destroy();
      expect(document.getElementById('rasti-container')).toBeNull();
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));   // no throw after teardown
    });
  });
});
