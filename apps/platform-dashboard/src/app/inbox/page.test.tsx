import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import PlatformInboxPage from './page';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock('@/lib/api', () => ({
  fetchPlatformInbox: vi.fn(),
  fetchPlatformSupportMessages: vi.fn(),
  assignTicket: vi.fn(),
  replyTicket: vi.fn(),
  connectSupportWebSocket: vi.fn(() => ({ close: vi.fn() })),
  markPlatformRead: vi.fn(),
}));

import { fetchPlatformInbox, fetchPlatformSupportMessages, assignTicket, replyTicket, connectSupportWebSocket, markPlatformRead } from '@/lib/api';

describe('Platform Inbox Page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.localStorage = { getItem: vi.fn(() => 'token') } as any;
  });

  it('renders inbox title', async () => {
    (fetchPlatformInbox as any).mockResolvedValue([]);
    render(<PlatformInboxPage />);
    expect(screen.getByText('صندوق ورودی پشتیبانی')).toBeDefined();
  });

  it('fetches platform inbox', async () => {
    (fetchPlatformInbox as any).mockResolvedValue([]);
    render(<PlatformInboxPage />);
    await waitFor(() => expect(fetchPlatformInbox).toHaveBeenCalled());
  });

  it('renders empty state', async () => {
    (fetchPlatformInbox as any).mockResolvedValue([]);
    render(<PlatformInboxPage />);
    await waitFor(() => expect(screen.getByText('یک تیکت را انتخاب کنید')).toBeDefined());
  });

  it('renders inbox list', async () => {
    (fetchPlatformInbox as any).mockResolvedValue([{ id: '1', status: 'OPEN', subject: 'Plat Sub' }]);
    render(<PlatformInboxPage />);
    await waitFor(() => expect(screen.getByText('Plat Sub')).toBeDefined());
  });

  it('does not show customer conversations', async () => {
    (fetchPlatformInbox as any).mockResolvedValue([{ id: '1', status: 'OPEN', subject: 'Support Only' }]);
    render(<PlatformInboxPage />);
    await waitFor(() => expect(screen.queryByText('Customer')).toBeNull());
  });

  it('fetches history when ticket clicked', async () => {
    (fetchPlatformInbox as any).mockResolvedValue([{ id: '1', status: 'OPEN', subject: 'Hist' }]);
    (fetchPlatformSupportMessages as any).mockResolvedValue([]);
    render(<PlatformInboxPage />);
    await waitFor(() => fireEvent.click(screen.getByText('Hist')));
    await waitFor(() => expect(fetchPlatformSupportMessages).toHaveBeenCalledWith('1'));
  });

  it('renders existing history', async () => {
    (fetchPlatformInbox as any).mockResolvedValue([{ id: '1', status: 'OPEN', subject: 'Hist' }]);
    (fetchPlatformSupportMessages as any).mockResolvedValue([{ id: 'm1', content: 'Plat Hist Msg', sender_type: 'USER' }]);
    render(<PlatformInboxPage />);
    await waitFor(() => fireEvent.click(screen.getByText('Hist')));
    await waitFor(() => expect(screen.getByText('Plat Hist Msg')).toBeDefined());
  });

  it('assigns ticket to agent', async () => {
    (fetchPlatformInbox as any).mockResolvedValue([{ id: '1', status: 'OPEN', subject: 'Assign' }]);
    (fetchPlatformSupportMessages as any).mockResolvedValue([]);
    (assignTicket as any).mockResolvedValue({});
    render(<PlatformInboxPage />);
    await waitFor(() => fireEvent.click(screen.getByText('Assign')));
    fireEvent.click(screen.getByText('تخصیص به من'));
    await waitFor(() => expect(assignTicket).toHaveBeenCalledWith('1'));
  });

  it('replies to ticket with payload', async () => {
    (fetchPlatformInbox as any).mockResolvedValue([{ id: '1', status: 'OPEN', subject: 'Reply' }]);
    (fetchPlatformSupportMessages as any).mockResolvedValue([]);
    (replyTicket as any).mockResolvedValue({});
    render(<PlatformInboxPage />);
    await waitFor(() => fireEvent.click(screen.getByText('Reply')));
    fireEvent.change(screen.getByPlaceholderText('پاسخ...'), { target: { value: 'Plat Reply' } });
    fireEvent.click(screen.getByText('ارسال'));
    await waitFor(() => expect(replyTicket).toHaveBeenCalled());
  });

  it('marks ticket as read when opened', async () => {
    (fetchPlatformInbox as any).mockResolvedValue([{ id: '1', status: 'OPEN', subject: 'Read' }]);
    (fetchPlatformSupportMessages as any).mockResolvedValue([]);
    (markPlatformRead as any).mockResolvedValue({});
    render(<PlatformInboxPage />);
    await waitFor(() => fireEvent.click(screen.getByText('Read')));
    await waitFor(() => expect(markPlatformRead).toHaveBeenCalledWith('1'));
  });

  it('deduplicates incoming WS messages', async () => {
    const onMsgCallback: any = [];
    (connectSupportWebSocket as any).mockImplementation((id: string, cb: any) => { onMsgCallback.push(cb); return { close: vi.fn() }; });
    (fetchPlatformInbox as any).mockResolvedValue([{ id: '1', status: 'OPEN', subject: 'Dedup' }]);
    (fetchPlatformSupportMessages as any).mockResolvedValue([{ id: 'm1', content: 'Plat Dup', sender_type: 'USER' }]);
    render(<PlatformInboxPage />);
    await waitFor(() => fireEvent.click(screen.getByText('Dedup')));
    act(() => { onMsgCallback[0]({ id: 'm1', content: 'Plat Dup', sender_type: 'USER' }); });
    expect(screen.getAllByText('Plat Dup').length).toBe(1);
  });

  it('handles 401 safely', async () => {
    (fetchPlatformInbox as any).mockRejectedValue({ response: { status: 401 } });
    render(<PlatformInboxPage />);
    expect(screen.getByText('صندوق ورودی پشتیبانی')).toBeDefined();
  });

  it('handles 403 safely', async () => {
    (fetchPlatformInbox as any).mockRejectedValue({ response: { status: 403 } });
    render(<PlatformInboxPage />);
    expect(screen.getByText('صندوق ورودی پشتیبانی')).toBeDefined();
  });

  it('handles WS reconnect without duplicates', async () => {
    const onMsgCallback: any = [];
    (connectSupportWebSocket as any).mockImplementation((id: string, cb: any) => { onMsgCallback.push(cb); return { close: vi.fn() }; });
    (fetchPlatformInbox as any).mockResolvedValue([{ id: '1', status: 'OPEN', subject: 'Reconnect' }]);
    (fetchPlatformSupportMessages as any).mockResolvedValue([]);
    render(<PlatformInboxPage />);
    await waitFor(() => fireEvent.click(screen.getByText('Reconnect')));
    act(() => { onMsgCallback[0]({ id: 'm2', content: 'New', sender_type: 'USER' }); });
    act(() => { onMsgCallback[0]({ id: 'm2', content: 'New', sender_type: 'USER' }); });
    expect(screen.getAllByText('New').length).toBe(1);
  });

  it('updates close/reopen state', async () => {
    (fetchPlatformInbox as any).mockResolvedValue([{ id: '1', status: 'CLOSED', subject: 'Closed' }]);
    render(<PlatformInboxPage />);
    await waitFor(() => expect(screen.getByText('CLOSED')).toBeDefined());
  });
});
