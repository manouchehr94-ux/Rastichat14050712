/* eslint-disable @typescript-eslint/no-explicit-any -- legacy test file: the vi.mock'd api module is driven through untyped mock handles */
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
  fetchPlatformWorkspaces: vi.fn(),
  startPlatformThread: vi.fn(),
  closePlatformThread: vi.fn(),
  reopenPlatformThread: vi.fn(),
}));

import {
  fetchPlatformInbox, fetchPlatformSupportMessages, assignTicket, replyTicket, connectSupportWebSocket, markPlatformRead,
  fetchPlatformWorkspaces, startPlatformThread, closePlatformThread, reopenPlatformThread,
} from '@/lib/api';

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
    await waitFor(() => expect(screen.getByText('بسته‌شده')).toBeDefined());
  });

  describe('platform-initiated conversation (platform -> tenant)', () => {
    const open = async () => {
      (fetchPlatformInbox as any).mockResolvedValue([]);
      (fetchPlatformWorkspaces as any).mockResolvedValue([{ id: 7, name: 'سازمان الف' }, { id: 8, name: 'سازمان ب' }]);
      render(<PlatformInboxPage />);
      fireEvent.click(screen.getByText('گفتگوی جدید با سازمان'));
      await screen.findByLabelText('گفتگوی جدید با سازمان');
    };

    it('lists the tenants the user may contact and starts a conversation without the tenant writing first', async () => {
      (startPlatformThread as any).mockResolvedValue({ id: 'c9', status: 'WAITING_FOR_WORKSPACE', subject: 'اطلاعیه', workspace_name: 'سازمان ب', opened_by_side: 'PLATFORM', created: true });
      (fetchPlatformSupportMessages as any).mockResolvedValue([{ id: 'm1', content: 'سلام از پلتفرم', sender_type: 'USER', sender_side: 'platform' }]);
      (markPlatformRead as any).mockResolvedValue({});
      await open();
      await waitFor(() => expect(screen.getByRole('option', { name: 'سازمان ب' })).toBeDefined());
      fireEvent.change(screen.getByLabelText('سازمان'), { target: { value: '8' } });
      fireEvent.change(screen.getByLabelText('موضوع'), { target: { value: 'اطلاعیه' } });
      fireEvent.change(screen.getByLabelText('پیام'), { target: { value: 'سلام از پلتفرم' } });
      fireEvent.click(screen.getByRole('button', { name: 'ارسال' }));
      await waitFor(() => expect(startPlatformThread).toHaveBeenCalled());
      const body = (startPlatformThread as any).mock.calls[0][0];
      expect(body).toMatchObject({ workspace_id: 8, subject: 'اطلاعیه', message: 'سلام از پلتفرم' });
      expect(body.client_message_id).toMatch(/^start_/);                       // retry-safe id
      await waitFor(() => expect(connectSupportWebSocket).toHaveBeenCalledWith('c9', expect.any(Function)));
      expect(await screen.findByText('سلام از پلتفرم')).toBeDefined();
    });

    it('needs a message and a tenant before it can be sent', async () => {
      await open();
      const send = screen.getByRole('button', { name: 'ارسال' }) as HTMLButtonElement;
      expect(send.disabled).toBe(true);
      expect(startPlatformThread).not.toHaveBeenCalled();
    });

    it('explains an unavailable tenant (409) and a foreign tenant (404)', async () => {
      await open();
      await waitFor(() => expect(screen.getByRole('option', { name: 'سازمان الف' })).toBeDefined());
      fireEvent.change(screen.getByLabelText('پیام'), { target: { value: 'x' } });
      (startPlatformThread as any).mockRejectedValueOnce(Object.assign(new Error('x'), { status: 409 }));
      fireEvent.click(screen.getByRole('button', { name: 'ارسال' }));
      expect((await screen.findByRole('alert')).textContent).toContain('فعال نیست');
      (startPlatformThread as any).mockRejectedValueOnce(Object.assign(new Error('x'), { status: 404 }));
      fireEvent.click(screen.getByRole('button', { name: 'ارسال' }));
      await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('در دسترس شما نیست'));
    });
  });

  describe('thread rendering and lifecycle', () => {
    const conv = { id: 'c1', status: 'WAITING_FOR_PLATFORM', subject: 'پرداخت', workspace_name: 'سازمان الف', opened_by_side: 'PLATFORM', unread_count: 0 };

    it('shows the tenant name, who opened the thread, and the status from the platform\'s viewpoint', async () => {
      (fetchPlatformInbox as any).mockResolvedValue([conv]);
      render(<PlatformInboxPage />);
      expect(await screen.findByText('سازمان الف')).toBeDefined();
      expect(screen.getByText(/آغاز‌شده توسط پلتفرم/)).toBeDefined();
      expect(screen.getByText(/در انتظار پاسخ شما/)).toBeDefined();
    });

    it('renders the platform\'s own messages and the tenant\'s on opposite sides', async () => {
      (fetchPlatformInbox as any).mockResolvedValue([conv]);
      (fetchPlatformSupportMessages as any).mockResolvedValue([
        { id: 'a', content: 'از پلتفرم', sender_type: 'USER', sender_side: 'platform' },
        { id: 'b', content: 'از سازمان', sender_type: 'USER', sender_side: 'tenant' },
      ]);
      (markPlatformRead as any).mockResolvedValue({});
      render(<PlatformInboxPage />);
      fireEvent.click(await screen.findByText('پرداخت'));
      const mine = await screen.findByText('از پلتفرم');
      const theirs = await screen.findByText('از سازمان');
      expect(mine.className).toContain('bg-indigo-600');
      expect(theirs.className).not.toContain('bg-indigo-600');
    });

    it('closes and reopens a conversation', async () => {
      (fetchPlatformInbox as any).mockResolvedValue([conv]);
      (fetchPlatformSupportMessages as any).mockResolvedValue([]);
      (markPlatformRead as any).mockResolvedValue({});
      (closePlatformThread as any).mockResolvedValue({ status: 'CLOSED' });
      (reopenPlatformThread as any).mockResolvedValue({ status: 'WAITING_FOR_WORKSPACE' });
      render(<PlatformInboxPage />);
      fireEvent.click(await screen.findByText('پرداخت'));
      fireEvent.click(await screen.findByRole('button', { name: 'بستن گفتگو' }));
      await waitFor(() => expect(closePlatformThread).toHaveBeenCalledWith('c1'));
      fireEvent.click(await screen.findByRole('button', { name: 'بازگشایی' }));
      await waitFor(() => expect(reopenPlatformThread).toHaveBeenCalledWith('c1'));
    });
  });
});
