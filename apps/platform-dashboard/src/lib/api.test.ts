import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fetchPlatformInbox, assignTicket, replyTicket, connectSupportWebSocket } from './api';

describe('Platform API Client', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = vi.fn();
    global.localStorage = { getItem: vi.fn(() => 'test-token') } as any;
    global.WebSocket = vi.fn(() => ({ close: vi.fn(), send: vi.fn() })) as any;
  });

  it('fetches platform inbox with auth header', async () => {
    (global.fetch as any).mockResolvedValue({ ok: true, json: () => Promise.resolve([{ id: '1' }]) });
    const result = await fetchPlatformInbox();
    expect(result).toEqual([{ id: '1' }]);
    expect(global.fetch).toHaveBeenCalledWith('http://localhost:8080/api/v1/platform/support/', { headers: { Authorization: 'Bearer test-token' } });
  });

  it('assigns a ticket', async () => {
    (global.fetch as any).mockResolvedValue({ ok: true, json: () => Promise.resolve({ id: '1', assigned_to: 'me' }) });
    await assignTicket('1');
    expect(global.fetch).toHaveBeenCalledWith('http://localhost:8080/api/v1/platform/support/1/assign/', { method: 'POST', headers: { Authorization: 'Bearer test-token' } });
  });

  it('replies to a ticket with correct payload', async () => {
    (global.fetch as any).mockResolvedValue({ ok: true, json: () => Promise.resolve({ id: '2' }) });
    await replyTicket('1', 'Hello', 'msg1');
    expect(global.fetch).toHaveBeenCalledWith('http://localhost:8080/api/v1/platform/support/1/reply/', {
      method: 'POST', headers: { Authorization: 'Bearer test-token', 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: 'Hello', client_message_id: 'msg1' })
    });
  });

  it('connects WebSocket with correct URL', () => {
    connectSupportWebSocket('123', () => {});
    expect(global.WebSocket).toHaveBeenCalledWith('ws://localhost:8080/ws/dashboard/support/test-token/123/');
  });
});
