import { describe, it, expect, vi, beforeEach } from 'vitest';
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
});
