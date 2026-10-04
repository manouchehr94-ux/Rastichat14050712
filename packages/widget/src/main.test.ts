import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import './main';

class FakeWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSING = 2;
  static CLOSED = 3;
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
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }
  emitMessage(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  state: 'inactive' | 'recording' = 'inactive';
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  stream: unknown;

  constructor(stream: unknown) {
    this.stream = stream;
    FakeMediaRecorder.instances.push(this);
  }
  start() {
    this.state = 'recording';
  }
  stop() {
    this.state = 'inactive';
    this.ondataavailable?.({ data: new Blob(['x'], { type: 'audio/webm' }) });
    this.onstop?.();
  }
}

function jsonResponse(body: unknown, ok = true) {
  return Promise.resolve({
    ok,
    json: () => Promise.resolve(body),
  } as Response);
}

async function flushMicrotasks(times = 5) {
  for (let i = 0; i < times; i++) {
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  }
}

describe('RastiChatWidget', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let fetchMock: any;

  let clock = 1_000_000;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let getUserMediaMock: any;

  beforeEach(() => {
    document.body.innerHTML = '';
    localStorage.clear();
    FakeWebSocket.instances = [];
    FakeMediaRecorder.instances = [];
    // @ts-expect-error test double
    global.WebSocket = FakeWebSocket;
    // @ts-expect-error test double
    global.MediaRecorder = FakeMediaRecorder;

    clock = 1_000_000;
    vi.spyOn(Date, 'now').mockImplementation(() => clock);

    const fakeStream = { getTracks: () => [{ stop: vi.fn() }] };
    getUserMediaMock = vi.fn(() => Promise.resolve(fakeStream));
    Object.defineProperty(navigator, 'mediaDevices', {
      value: { getUserMedia: getUserMediaMock },
      configurable: true,
    });

    fetchMock = vi.fn((url: string) => {
      if (url.includes('/widget/init/')) return jsonResponse({ session_token: 'sess-1' });
      if (url.includes('/widget/start/')) return jsonResponse({ id: 'conv-1' });
      if (url.includes('/messages/')) return jsonResponse([]);
      return jsonResponse({});
    });
    // @ts-expect-error test double
    global.fetch = fetchMock;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  async function initWidget(config: Record<string, unknown> = {}) {
    window.RastiChat.init({ projectKey: 'proj-1', ...config });
    await flushMicrotasks();
    return FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  }

  it('renders the launcher and opens the panel on click', async () => {
    await initWidget();
    const launcher = document.getElementById('rasti-launcher')!;
    const panel = document.getElementById('rasti-panel')!;
    expect(panel.classList.contains('open')).toBe(false);
    (launcher as HTMLElement).click();
    expect(panel.classList.contains('open')).toBe(true);
  });

  it('initializes a session and starts a chat, persisting the session token', async () => {
    await initWidget();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/widget/init/'),
      expect.objectContaining({ method: 'POST' }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/widget/start/'),
      expect.objectContaining({ method: 'POST' }),
    );
    expect(localStorage.getItem('rasti_session')).toBe('sess-1');
  });

  it('reuses a stored session token instead of calling init again', async () => {
    localStorage.setItem('rasti_session', 'existing-session');
    await initWidget();
    const initCalls = fetchMock.mock.calls.filter((c: unknown[]) => String(c[0]).includes('/widget/init/'));
    expect(initCalls.length).toBe(0);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/widget/start/'),
      expect.objectContaining({ body: JSON.stringify({ session_token: 'existing-session' }) }),
    );
  });

  it('sends a text message over the websocket and renders it optimistically', async () => {
    const ws = await initWidget();
    const input = document.getElementById('rasti-input') as HTMLInputElement;
    input.value = 'سلام';
    document.getElementById('rasti-send')!.click();

    expect(ws.sent.length).toBe(1);
    const sent = JSON.parse(ws.sent[0]);
    expect(sent.message).toBe('سلام');
    expect(sent.client_message_id).toBeTruthy();

    const bubbles = document.querySelectorAll('.rasti-msg.visitor .rasti-bubble');
    expect(Array.from(bubbles).some((b) => b.textContent === 'سلام')).toBe(true);
    expect(input.value).toBe('');
  });

  it('fills the input with a quick reply without sending it', async () => {
    await initWidget();
    const chip = document.querySelector('.rasti-chip') as HTMLElement;
    const input = document.getElementById('rasti-input') as HTMLInputElement;
    chip.click();
    expect(input.value.length).toBeGreaterThan(0);
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    expect(ws.sent.length).toBe(0);
  });

  it('inserts an emoji into the input via the emoji picker', async () => {
    await initWidget();
    const emojiBtn = document.getElementById('rasti-emoji-btn')!;
    (emojiBtn as HTMLElement).click();
    const pop = document.getElementById('rasti-emoji-pop')!;
    expect(pop.classList.contains('open')).toBe(true);
    const firstEmoji = pop.querySelector('button') as HTMLButtonElement;
    const input = document.getElementById('rasti-input') as HTMLInputElement;
    firstEmoji.click();
    expect(input.value).toBe(firstEmoji.textContent);
  });

  it('renders an incoming product card with brand, name, price and rating', async () => {
    const ws = await initWidget();
    ws.emitMessage({
      sender_type: 'USER', message_type: 'PRODUCT', client_message_id: 'p1', created_at: new Date().toISOString(),
      metadata: { brand: 'Arom', name: 'Candle', price: 890000, old_price: 1120000, rating: 5, reviews_count: 128 },
    });
    const card = document.querySelector('.rasti-bubble.rasti-product')!;
    expect(card.querySelector('.rasti-p-brand')?.textContent).toBe('Arom');
    expect(card.querySelector('.rasti-p-name')?.textContent).toBe('Candle');
    // Prices render Persian-locale digits (fa-IR), not ASCII.
    expect(card.querySelector('.rasti-p-now')?.textContent).toContain('۸۹۰');
    expect(card.querySelector('.rasti-p-old')?.textContent).toContain('۱');
  });

  it('renders an incoming Knowledge Base article card with title, excerpt and link', async () => {
    const ws = await initWidget();
    ws.emitMessage({
      sender_type: 'USER', message_type: 'ARTICLE', client_message_id: 'a1', created_at: new Date().toISOString(),
      metadata: {
        article: {
          article_id: 'art-1', title: 'راهنمای مرجوعی', excerpt: 'خلاصه کوتاه', category: 'مرجوعی',
          url: 'http://localhost:8081/kb/refund-guide',
        },
      },
    });
    const card = document.querySelector('.rasti-bubble.rasti-article')!;
    expect(card).toBeTruthy();
    expect(card.querySelector('.rasti-a-title')?.textContent).toBe('راهنمای مرجوعی');
    expect(card.querySelector('.rasti-a-excerpt')?.textContent).toBe('خلاصه کوتاه');
    expect(card.querySelector('.rasti-a-cat')?.textContent).toContain('مرجوعی');
    expect(card.querySelector('.rasti-a-link')?.getAttribute('href')).toBe('http://localhost:8081/kb/refund-guide');
  });

  it('renders a rating request and submits a rating via POST', async () => {
    const ws = await initWidget();
    ws.emitMessage({ sender_type: 'USER', message_type: 'RATING_REQUEST', client_message_id: 'r1', created_at: new Date().toISOString() });
    const stars = document.querySelectorAll('.rasti-r-stars button');
    expect(stars.length).toBe(5);
    (stars[3] as HTMLElement).click(); // 4th star = rating 4
    await flushMicrotasks();

    const rateCall = fetchMock.mock.calls.find((c: unknown[]) => String(c[0]).includes('/rate/'));
    expect(rateCall).toBeTruthy();
    const body = JSON.parse((rateCall![1] as RequestInit).body as string);
    expect(body.rating).toBe(4);
    expect(body.session_token).toBe('sess-1');

    const thanks = document.querySelector('.rasti-r-thanks') as HTMLElement;
    expect(thanks.style.display).toBe('block');
  });

  it('uploads an image and renders the server response', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/widget/init/')) return jsonResponse({ session_token: 'sess-1' });
      if (url.includes('/widget/start/')) return jsonResponse({ id: 'conv-1' });
      if (url.includes('/messages/')) return jsonResponse([]);
      if (url.includes('/upload/')) {
        return jsonResponse({
          sender_type: 'VISITOR', message_type: 'IMAGE', client_message_id: 'img1',
          created_at: new Date().toISOString(), attachment_url: 'https://example.com/pic.png',
        });
      }
      return jsonResponse({});
    });
    await initWidget();
    const file = new File(['abc'], 'pic.png', { type: 'image/png' });
    const fileInput = document.getElementById('rasti-file') as HTMLInputElement;
    Object.defineProperty(fileInput, 'files', { value: [file] });
    fileInput.dispatchEvent(new Event('change'));
    await flushMicrotasks();

    const uploadCall = fetchMock.mock.calls.find((c: unknown[]) => String(c[0]).includes('/upload/'));
    expect(uploadCall).toBeTruthy();
    expect(document.querySelector('.rasti-bubble.rasti-img img')).toBeTruthy();
  });

  it('reconnects the websocket after it closes', async () => {
    const ws = await initWidget();
    expect(FakeWebSocket.instances.length).toBe(1);
    ws.close();
    // The widget schedules its reconnect with a real 2s setTimeout; wait it out for real
    // rather than faking timers, which is simpler to keep isolated from the other tests.
    await new Promise((r) => setTimeout(r, 2200));
    expect(FakeWebSocket.instances.length).toBe(2);
  }, 10000);

  it('deduplicates an incoming message that shares a client_message_id with one already rendered', async () => {
    const ws = await initWidget();
    const payload = {
      sender_type: 'USER', message_type: 'TEXT', content: 'hi', client_message_id: 'dup-1',
      created_at: new Date().toISOString(),
    };
    ws.emitMessage(payload);
    ws.emitMessage(payload);
    const bubbles = document.querySelectorAll('.rasti-msg.operator');
    expect(bubbles.length).toBe(1);
  });

  it('does not throw when the init request fails (offline/error state)', async () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error('network down')));
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(() => window.RastiChat.init({ projectKey: 'proj-1' })).not.toThrow();
    await flushMicrotasks();
    expect(errorSpy).toHaveBeenCalled();
    // The launcher must still render even though the network call failed.
    expect(document.getElementById('rasti-launcher')).toBeTruthy();
  });

  it('accepts a configurable apiBase/wsBase instead of hardcoded localhost', async () => {
    window.RastiChat.init({ projectKey: 'proj-1', apiBase: 'https://chat.example.com/api/v1', wsBase: 'wss://chat.example.com/ws' });
    await flushMicrotasks();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('https://chat.example.com/api/v1/widget/init/'),
      expect.anything(),
    );
    expect(FakeWebSocket.instances[0].url.startsWith('wss://chat.example.com/ws/widget/')).toBe(true);
  });

  it('applies real store/consultant branding from the start response and never renders a hardcoded sample name', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/widget/init/')) return jsonResponse({ session_token: 'sess-1' });
      if (url.includes('/widget/start/')) {
        return jsonResponse({
          id: 'conv-1',
          branding: {
            store: { name: 'Real Store', logo_url: '', subtitle: 'خانه و سبک زندگی' },
            consultant: {
              display_name: 'Ali Operator', avatar_url: '', title: 'مشاور ارشد', status: 'ONLINE',
              response_time_label: null, rating: null,
            },
            workspace_online: true,
          },
        });
      }
      if (url.includes('/messages/')) return jsonResponse([]);
      return jsonResponse({});
    });
    await initWidget();
    expect(document.getElementById('rasti-title')!.textContent).toBe('Ali Operator');
    expect(document.getElementById('rasti-subtitle')!.textContent).toBe('مشاور ارشد');
    expect(document.body.innerHTML).not.toContain('آرُم');
    expect(document.body.innerHTML).not.toContain('مریم رضایی');
  });

  it('falls back to store identity when no consultant is assigned yet', async () => {
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/widget/init/')) return jsonResponse({ session_token: 'sess-1' });
      if (url.includes('/widget/start/')) {
        return jsonResponse({
          id: 'conv-1',
          branding: { store: { name: 'Store X', logo_url: '', subtitle: '' }, consultant: null, workspace_online: false },
        });
      }
      if (url.includes('/messages/')) return jsonResponse([]);
      return jsonResponse({});
    });
    await initWidget();
    expect(document.getElementById('rasti-title')!.textContent).toBe('Store X');
  });

  it('updates branding live when a branding.updated event arrives over the websocket', async () => {
    const ws = await initWidget();
    ws.emitMessage({
      type: 'branding.updated',
      branding: {
        store: { name: 'Store', logo_url: '', subtitle: '' },
        consultant: {
          display_name: 'New Op', avatar_url: '', title: '', status: 'AWAY', response_time_label: null, rating: null,
        },
        workspace_online: true,
      },
    });
    expect(document.getElementById('rasti-title')!.textContent).toBe('New Op');
  });

  it('shows the offline banner when the websocket closes and hides it once reconnected', async () => {
    const ws = await initWidget();
    const banner = document.getElementById('rasti-offline-banner')!;
    expect(banner.classList.contains('show')).toBe(false);
    ws.close();
    expect(banner.classList.contains('show')).toBe(true);
    await new Promise((r) => setTimeout(r, 2200));
    const ws2 = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    ws2.onopen?.();
    expect(banner.classList.contains('show')).toBe(false);
  }, 10000);

  it('shows an upload-status spinner while a file is uploading and hides it once done', async () => {
    let resolveUpload!: (v: unknown) => void;
    fetchMock.mockImplementation((url: string) => {
      if (url.includes('/widget/init/')) return jsonResponse({ session_token: 'sess-1' });
      if (url.includes('/widget/start/')) return jsonResponse({ id: 'conv-1' });
      if (url.includes('/messages/')) return jsonResponse([]);
      if (url.includes('/upload/')) return new Promise((resolve) => { resolveUpload = resolve; });
      return jsonResponse({});
    });
    await initWidget();
    const file = new File(['abc'], 'pic.png', { type: 'image/png' });
    const fileInput = document.getElementById('rasti-file') as HTMLInputElement;
    Object.defineProperty(fileInput, 'files', { value: [file] });
    fileInput.dispatchEvent(new Event('change'));
    await flushMicrotasks();

    const status = document.getElementById('rasti-upload-status')!;
    expect(status.classList.contains('show')).toBe(true);

    resolveUpload({
      ok: true,
      json: () => Promise.resolve({
        sender_type: 'VISITOR', message_type: 'IMAGE', client_message_id: 'img1',
        created_at: new Date().toISOString(), attachment_url: 'https://example.com/pic.png',
      }),
    });
    await flushMicrotasks();
    expect(status.classList.contains('show')).toBe(false);
  });

  it('records and uploads a voice message on mic start/stop', async () => {
    await initWidget();
    const micBtn = document.getElementById('rasti-mic-btn')!;
    const cancelBtn = document.getElementById('rasti-mic-cancel')!;

    (micBtn as HTMLElement).click();
    await flushMicrotasks();

    expect(FakeMediaRecorder.instances.length).toBe(1);
    expect(micBtn.classList.contains('recording')).toBe(true);
    expect(cancelBtn.classList.contains('show')).toBe(true);

    clock += 1500; // simulate 1.5s of recording so it clears the accidental-tap threshold
    (micBtn as HTMLElement).click(); // stop
    await flushMicrotasks();

    expect(micBtn.classList.contains('recording')).toBe(false);
    expect(cancelBtn.classList.contains('show')).toBe(false);
    const uploadCall = fetchMock.mock.calls.find((c: unknown[]) => String(c[0]).includes('/upload/'));
    expect(uploadCall).toBeTruthy();
  });

  it('cancels a recording without uploading anything', async () => {
    await initWidget();
    const micBtn = document.getElementById('rasti-mic-btn')!;
    const cancelBtn = document.getElementById('rasti-mic-cancel')!;

    (micBtn as HTMLElement).click();
    await flushMicrotasks();
    clock += 1500;
    (cancelBtn as HTMLElement).click();
    await flushMicrotasks();

    expect(micBtn.classList.contains('recording')).toBe(false);
    const uploadCall = fetchMock.mock.calls.find((c: unknown[]) => String(c[0]).includes('/upload/'));
    expect(uploadCall).toBeFalsy();
  });

  it('shows an inline notice instead of alert() when microphone access is denied', async () => {
    getUserMediaMock.mockImplementation(() => Promise.reject(new Error('denied')));
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    await initWidget();

    (document.getElementById('rasti-mic-btn') as HTMLElement).click();
    await flushMicrotasks();

    expect(alertSpy).not.toHaveBeenCalled();
    expect(errorSpy).toHaveBeenCalled();
    const notice = document.getElementById('rasti-notice')!;
    expect(notice.classList.contains('show')).toBe(true);
    expect(notice.textContent).toContain('میکروفون');
  });

  it('locks and restores body scroll when opening/closing the panel on a mobile viewport', async () => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: true }) as unknown as typeof window.matchMedia;
    await initWidget();
    const launcher = document.getElementById('rasti-launcher')!;
    (launcher as HTMLElement).click();
    expect(document.body.style.overflow).toBe('hidden');
    (launcher as HTMLElement).click();
    expect(document.body.style.overflow).toBe('');
  });
});
