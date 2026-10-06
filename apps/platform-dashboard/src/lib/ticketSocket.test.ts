import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { TicketSocket } from './ticketSocket';

interface FakeSock { url: string; readyState: number; sent: string[]; onopen?: () => void; onmessage?: (e: { data: string }) => void; onclose?: (e: { code: number }) => void; closed: boolean; send(d: string): void; close(): void }

describe('TicketSocket', () => {
  let sockets: FakeSock[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let fetchMock: any;
  const opts = (over: Record<string, unknown> = {}) => ({
    apiBase: 'https://api.example/api/v1', wsBase: 'wss://api.example/ws', getToken: () => 'JWT-SECRET',
    path: '/v2/dashboard/c1/', ticketRequest: { kind: 'dashboard_chat', conversation_id: 'c1' },
    onMessage: vi.fn(), ...over,
  });
  const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

  beforeEach(() => {
    sockets = [];
    fetchMock = vi.fn(() => Promise.resolve({ ok: true, status: 201, json: () => Promise.resolve({ ticket: 'TICKET-1' }) }));
    // @ts-expect-error test double
    global.fetch = fetchMock;
    // @ts-expect-error test double
    global.WebSocket = Object.assign(vi.fn((url: string) => {
      const s: FakeSock = { url, readyState: 1, sent: [], closed: false, send(d) { this.sent.push(d); }, close() { this.closed = true; } };
      sockets.push(s);
      return s;
    }), { OPEN: 1, CONNECTING: 0 });
  });

  it('mints a ticket with the JWT in a header and opens a URL that carries no credential', async () => {
    new TicketSocket(opts());
    await flush();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://api.example/api/v1/ws/ticket/');
    expect(init.headers.Authorization).toBe('Bearer JWT-SECRET');
    expect(JSON.parse(init.body)).toEqual({ kind: 'dashboard_chat', conversation_id: 'c1' });
    expect(sockets[0].url).toBe('wss://api.example/ws/v2/dashboard/c1/');
    expect(sockets[0].url).not.toMatch(/JWT-SECRET|TICKET-1/);
  });

  it('sends the ticket as the first frame and is OPEN only after auth.ok; messages flow afterwards', async () => {
    const onMessage = vi.fn();
    const onOpen = vi.fn();
    const ws = new TicketSocket(opts({ onMessage, onOpen }));
    await flush();
    sockets[0].onopen!();
    expect(JSON.parse(sockets[0].sent[0])).toEqual({ type: 'auth', ticket: 'TICKET-1' });
    expect(ws.readyState).toBe(WebSocket.CONNECTING);
    ws.send('premature');
    expect(sockets[0].sent).toHaveLength(1); // nothing but the auth frame before acknowledgement
    sockets[0].onmessage!({ data: JSON.stringify({ type: 'auth.ok' }) });
    expect(ws.readyState).toBe(WebSocket.OPEN);
    expect(onOpen).toHaveBeenCalledOnce();
    expect(onMessage).not.toHaveBeenCalled(); // auth.ok is protocol, not application data
    ws.send('hello');
    expect(sockets[0].sent[1]).toBe('hello');
    sockets[0].onmessage!({ data: JSON.stringify({ type: 'chat.message', content: 'x' }) });
    expect(onMessage).toHaveBeenCalledWith({ type: 'chat.message', content: 'x' });
  });

  it('does not open a socket when the ticket is refused and reports the close', async () => {
    fetchMock.mockImplementation(() => Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) }));
    const onClose = vi.fn();
    new TicketSocket(opts({ onClose }));
    await flush();
    expect(sockets).toHaveLength(0);
    expect(onClose).toHaveBeenCalled();
  });

  it('does not open a socket if the caller closed it while the ticket was in flight', async () => {
    const ws = new TicketSocket(opts());
    ws.close();
    await flush();
    expect(sockets).toHaveLength(0);
  });

  it('becomes not-OPEN again when the server closes it', async () => {
    const onClose = vi.fn();
    const ws = new TicketSocket(opts({ onClose }));
    await flush();
    sockets[0].onopen!();
    sockets[0].onmessage!({ data: JSON.stringify({ type: 'auth.ok' }) });
    sockets[0].onclose!({ code: 4403 });
    expect(ws.readyState).toBe(WebSocket.CONNECTING);
    expect(onClose).toHaveBeenCalledWith(4403);
  });

  describe('reconnection', () => {
    const authenticate = (i: number) => { sockets[i].onopen!(); sockets[i].onmessage!({ data: JSON.stringify({ type: 'auth.ok' }) }); };
    beforeEach(() => { vi.useFakeTimers(); });
    afterEach(() => { vi.useRealTimers(); });

    it('reconnects after a server-side drop with a FRESH ticket and tells the caller to resync', async () => {
      let n = 0;
      fetchMock.mockImplementation(() => Promise.resolve({ ok: true, status: 201, json: () => Promise.resolve({ ticket: `TICKET-${++n}` }) }));
      const onReconnect = vi.fn();
      const onOpen = vi.fn();
      new TicketSocket(opts({ onReconnect, onOpen }));
      await vi.advanceTimersByTimeAsync(0);
      authenticate(0);
      expect(onReconnect).not.toHaveBeenCalled(); // the first connection is not a *re*connection
      sockets[0].onclose!({ code: 1006 });
      await vi.advanceTimersByTimeAsync(1100);
      expect(sockets).toHaveLength(2);
      sockets[1].onopen!();
      expect(JSON.parse(sockets[1].sent[0])).toEqual({ type: 'auth', ticket: 'TICKET-2' });
      sockets[1].onmessage!({ data: JSON.stringify({ type: 'auth.ok' }) });
      expect(onReconnect).toHaveBeenCalledOnce();
      expect(onOpen).toHaveBeenCalledTimes(2);
    });

    it('also resyncs when the FIRST connection only succeeded after failed attempts (backend restarting during page load)', async () => {
      let up = false;
      fetchMock.mockImplementation(() => up
        ? Promise.resolve({ ok: true, status: 201, json: () => Promise.resolve({ ticket: 'T' }) })
        : Promise.reject(new Error('backend restarting')));
      const onReconnect = vi.fn();
      new TicketSocket(opts({ onReconnect }));
      await vi.advanceTimersByTimeAsync(0);
      up = true;
      await vi.advanceTimersByTimeAsync(1100);
      expect(sockets).toHaveLength(1);
      authenticate(0);
      expect(onReconnect).toHaveBeenCalledOnce(); // history was fetched before the socket was live: replay what arrived since
    });

    it('does not resync on a clean first connection', async () => {
      const onReconnect = vi.fn();
      new TicketSocket(opts({ onReconnect }));
      await vi.advanceTimersByTimeAsync(0);
      authenticate(0);
      expect(onReconnect).not.toHaveBeenCalled();
    });

    it('backs off exponentially while the backend is down and recovers when it is back', async () => {
      fetchMock.mockImplementation(() => Promise.reject(new Error('network down')));
      const onClose = vi.fn();
      new TicketSocket(opts({ onClose }));
      await vi.advanceTimersByTimeAsync(0);
      const afterFirst = fetchMock.mock.calls.length;
      await vi.advanceTimersByTimeAsync(1100);
      await vi.advanceTimersByTimeAsync(2100);
      expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(afterFirst + 2);
      expect(fetchMock.mock.calls.length).toBeLessThan(afterFirst + 5); // not a tight loop
      expect(sockets).toHaveLength(0);
    });

    it('never reconnects when the server revoked access (4403)', async () => {
      new TicketSocket(opts());
      await vi.advanceTimersByTimeAsync(0);
      authenticate(0);
      sockets[0].onclose!({ code: 4403 });
      await vi.advanceTimersByTimeAsync(120000);
      expect(sockets).toHaveLength(1);
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it('never reconnects after the caller closed it', async () => {
      const ws = new TicketSocket(opts());
      await vi.advanceTimersByTimeAsync(0);
      authenticate(0);
      ws.close();
      sockets[0].onclose!({ code: 1000 });
      await vi.advanceTimersByTimeAsync(120000);
      expect(sockets).toHaveLength(1);
    });

    it('does not retry when the ticket is refused for lack of access, but does when the backend errors', async () => {
      fetchMock.mockImplementation(() => Promise.resolve({ ok: false, status: 403, json: () => Promise.resolve({}) }));
      new TicketSocket(opts());
      await vi.advanceTimersByTimeAsync(60000);
      expect(fetchMock).toHaveBeenCalledTimes(1);
      fetchMock.mockClear();
      fetchMock.mockImplementation(() => Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) }));
      new TicketSocket(opts());
      await vi.advanceTimersByTimeAsync(5000);
      expect(fetchMock.mock.calls.length).toBeGreaterThan(1);
    });

    it('gives up after three consecutive authentication failures (4401)', async () => {
      new TicketSocket(opts());
      for (let i = 0; i < 6; i++) {
        await vi.advanceTimersByTimeAsync(40000);
        if (sockets[i]) sockets[i].onclose!({ code: 4401 });
      }
      await vi.advanceTimersByTimeAsync(120000);
      expect(sockets).toHaveLength(3);
    });

    it('can be disabled', async () => {
      new TicketSocket(opts({ reconnect: false }));
      await vi.advanceTimersByTimeAsync(0);
      authenticate(0);
      sockets[0].onclose!({ code: 1006 });
      await vi.advanceTimersByTimeAsync(120000);
      expect(sockets).toHaveLength(1);
    });
  });
});
