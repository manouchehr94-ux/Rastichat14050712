import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fetchPlatformInbox, assignTicket, replyTicket, connectSupportWebSocket } from './api';

interface FakeSocket {
  url: string;
  readyState: number;
  sent: string[];
  onopen?: () => void;
  onmessage?: (event: { data: string }) => void;
  send(data: string): void;
  close: () => void;
}

describe('Platform API Client', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock = vi.fn();
    global.fetch = fetchMock as unknown as typeof fetch;
    global.localStorage = { getItem: vi.fn(() => 'test-token') } as unknown as Storage;
  });

  it('fetches platform inbox with auth header', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve([{ id: '1' }]) });
    const result = await fetchPlatformInbox();
    expect(result).toEqual([{ id: '1' }]);
    expect(fetchMock).toHaveBeenCalledWith('http://localhost:8080/api/v1/platform/support/', { headers: { Authorization: 'Bearer test-token' } });
  });

  it('assigns a ticket', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve({ id: '1', assigned_to: 'me' }) });
    await assignTicket('1');
    expect(fetchMock).toHaveBeenCalledWith('http://localhost:8080/api/v1/platform/support/1/assign/', { method: 'POST', headers: { Authorization: 'Bearer test-token' } });
  });

  it('replies to a ticket with correct payload', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve({ id: '2' }) });
    await replyTicket('1', 'Hello', 'msg1');
    expect(fetchMock).toHaveBeenCalledWith('http://localhost:8080/api/v1/platform/support/1/reply/', {
      method: 'POST', headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: 'Hello', client_message_id: 'msg1' })
    });
  });

  describe('support WebSocket', () => {
    let sockets: FakeSocket[];
    let socketCtor: ReturnType<typeof vi.fn>;

    beforeEach(() => {
      sockets = [];
      socketCtor = vi.fn((url: string) => {
        const sock: FakeSocket = { url, readyState: 1, sent: [], send(data) { this.sent.push(data); }, close: vi.fn() };
        sockets.push(sock);
        return sock;
      });
      global.WebSocket = Object.assign(socketCtor, { OPEN: 1, CONNECTING: 0 }) as unknown as typeof WebSocket;
    });

    it('opens with a single-use ticket and NO credential in the URL', async () => {
      fetchMock.mockResolvedValue({ ok: true, json: () => Promise.resolve({ ticket: 'tkt-123' }) });
      const received: unknown[] = [];
      const ws = connectSupportWebSocket('123', (d) => received.push(d));

      await vi.waitFor(() => expect(sockets.length).toBe(1));
      expect(fetchMock).toHaveBeenCalledWith('http://localhost:8080/api/v1/ws/ticket/', expect.objectContaining({
        method: 'POST',
        headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind: 'support', conversation_id: '123' }),
      }));
      expect(sockets[0].url).toBe('ws://localhost:8080/ws/v2/support/123/');
      expect(sockets[0].url).not.toContain('test-token');
      expect(sockets[0].url).not.toContain('tkt-123');

      // the ticket goes in the first frame; the socket only counts as OPEN once the server acknowledged it
      sockets[0].onopen!();
      expect(JSON.parse(sockets[0].sent[0])).toEqual({ type: 'auth', ticket: 'tkt-123' });
      expect(ws.readyState).not.toBe(WebSocket.OPEN);
      sockets[0].onmessage!({ data: JSON.stringify({ type: 'auth.ok' }) });
      expect(ws.readyState).toBe(WebSocket.OPEN);
      expect(received).toEqual([]);
      sockets[0].onmessage!({ data: JSON.stringify({ type: 'chat.message', id: 'm1', content: 'hi', sender_type: 'USER' }) });
      expect(received).toEqual([{ type: 'chat.message', id: 'm1', content: 'hi', sender_type: 'USER' }]);
    });

    it('does not open a socket when the ticket request is refused', async () => {
      fetchMock.mockResolvedValue({ ok: false, status: 404, json: () => Promise.resolve({}) });
      connectSupportWebSocket('123', () => {});
      await new Promise((r) => setTimeout(r, 0));
      expect(socketCtor).not.toHaveBeenCalled();
    });
  });
});
