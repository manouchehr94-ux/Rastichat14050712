import { describe, it, expect, vi, beforeEach } from 'vitest';

describe('API Client', () => {
  beforeEach(() => {
    global.fetch = vi.fn();
    global.localStorage = {
      getItem: vi.fn(() => 'fake-token'),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    } as any;
  });

  it('should fetch conversations with auth header', async () => {
    (global.fetch as any).mockResolvedValue({
      ok: true,
      json: () => Promise.resolve([{ id: '1' }]),
    });
    
    // Dynamic import to ensure mocks are in place
    const api = await import('./api');
    const method = 'operator-dashboard' === 'operator-dashboard' ? api.fetchConversations : api.fetchPlatformInbox;
    const result = await method();
    
    expect(result).toEqual([{ id: '1' }]);
    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/conversations/customer/') === false ? expect.stringContaining('/platform/support/') : expect.stringContaining('/conversations/customer/'),
      { headers: { Authorization: 'Bearer fake-token' } }
    );
  });
});
