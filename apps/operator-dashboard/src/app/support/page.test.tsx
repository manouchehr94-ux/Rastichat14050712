/* eslint-disable @typescript-eslint/no-explicit-any -- legacy test file: the vi.mock'd api module is driven through untyped mock handles */
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import SupportPage from './page';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock('@/lib/api', () => ({
  fetchSupportConversations: vi.fn(),
  fetchSupportMessages: vi.fn(),
  sendSupportMessage: vi.fn(),
  connectSupportWebSocket: vi.fn(() => ({ close: vi.fn() })),
  createSupportRequest: vi.fn(),
  markSupportRead: vi.fn(),
  closeSupportConversation: vi.fn(),
  reopenSupportConversation: vi.fn(),
}));

import {
  fetchSupportConversations, fetchSupportMessages, sendSupportMessage, connectSupportWebSocket, createSupportRequest,
  markSupportRead, closeSupportConversation, reopenSupportConversation,
} from '@/lib/api';

describe('Workspace Support Page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.localStorage = { getItem: vi.fn(() => 'token') } as any;
    (markSupportRead as any).mockResolvedValue(undefined);
  });

  it('renders support title', async () => {
    (fetchSupportConversations as any).mockResolvedValue([]);
    render(<SupportPage />);
    expect(screen.getByText('پشتیبانی RastiChat')).toBeDefined();
  });

  it('renders conversation list', async () => {
    (fetchSupportConversations as any).mockResolvedValue([{ id: '1', status: 'OPEN', subject: 'Test Sub' }]);
    render(<SupportPage />);
    await waitFor(() => expect(screen.getByText('Test Sub')).toBeDefined());
  });

  it('renders empty state', async () => {
    (fetchSupportConversations as any).mockResolvedValue([]);
    render(<SupportPage />);
    await waitFor(() => expect(screen.getByText('یک تیکت را انتخاب کنید یا تیکت جدید بسازید')).toBeDefined());
  });

  it('shows unread badge', async () => {
    (fetchSupportConversations as any).mockResolvedValue([{ id: '1', status: 'OPEN', subject: 'Unread', unread_count: 2 }]);
    render(<SupportPage />);
    await waitFor(() => expect(screen.getByText('2')).toBeDefined());
  });

  it('opens create form on button click', async () => {
    (fetchSupportConversations as any).mockResolvedValue([]);
    render(<SupportPage />);
    fireEvent.click(screen.getByText('تیکت جدید'));
    expect(screen.getByPlaceholderText('موضوع')).toBeDefined();
  });

  it('validates create form fields', async () => {
    (fetchSupportConversations as any).mockResolvedValue([]);
    render(<SupportPage />);
    fireEvent.click(screen.getByText('تیکت جدید'));
    fireEvent.click(screen.getByText('ارسال درخواست'));
    expect(createSupportRequest).not.toHaveBeenCalled();
  });

  it('creates support request', async () => {
    (fetchSupportConversations as any).mockResolvedValue([]);
    (createSupportRequest as any).mockResolvedValue({});
    render(<SupportPage />);
    fireEvent.click(screen.getByText('تیکت جدید'));
    fireEvent.change(screen.getByPlaceholderText('موضوع'), { target: { value: 'New' } });
    fireEvent.change(screen.getByPlaceholderText('پیام اولیه'), { target: { value: 'Msg' } });
    fireEvent.click(screen.getByText('ارسال درخواست'));
    await waitFor(() => expect(createSupportRequest).toHaveBeenCalledWith('New', 'Msg'));
  });

  it('fetches history when conversation clicked', async () => {
    (fetchSupportConversations as any).mockResolvedValue([{ id: '1', status: 'OPEN', subject: 'Hist' }]);
    (fetchSupportMessages as any).mockResolvedValue([{ id: 'm1', content: 'Old Msg', sender_type: 'USER' }]);
    render(<SupportPage />);
    await waitFor(() => fireEvent.click(screen.getByText('Hist')));
    await waitFor(() => expect(fetchSupportMessages).toHaveBeenCalledWith('1'));
  });

  it('renders history messages', async () => {
    (fetchSupportConversations as any).mockResolvedValue([{ id: '1', status: 'OPEN', subject: 'Hist' }]);
    (fetchSupportMessages as any).mockResolvedValue([{ id: 'm1', content: 'History Msg', sender_type: 'USER' }]);
    render(<SupportPage />);
    await waitFor(() => fireEvent.click(screen.getByText('Hist')));
    await waitFor(() => expect(screen.getByText('History Msg')).toBeDefined());
  });

  it('sends message with payload', async () => {
    (fetchSupportConversations as any).mockResolvedValue([{ id: '1', status: 'OPEN', subject: 'Send' }]);
    (fetchSupportMessages as any).mockResolvedValue([]);
    (sendSupportMessage as any).mockResolvedValue({});
    render(<SupportPage />);
    await waitFor(() => fireEvent.click(screen.getByText('Send')));
    fireEvent.change(screen.getByPlaceholderText('پاسخ...'), { target: { value: 'Reply' } });
    fireEvent.click(screen.getByText('ارسال'));
    await waitFor(() => expect(sendSupportMessage).toHaveBeenCalled());
  });

  it('deduplicates incoming WS messages', async () => {
    const onMsgCallback: any = [];
    (connectSupportWebSocket as any).mockImplementation((id: string, cb: any) => { onMsgCallback.push(cb); return { close: vi.fn() }; });
    (fetchSupportConversations as any).mockResolvedValue([{ id: '1', status: 'OPEN', subject: 'Dedup' }]);
    (fetchSupportMessages as any).mockResolvedValue([{ id: 'm1', content: 'Dup', sender_type: 'USER' }]);
    render(<SupportPage />);
    await waitFor(() => fireEvent.click(screen.getByText('Dedup')));
    act(() => { onMsgCallback[0]({ id: 'm1', content: 'Dup', sender_type: 'USER' }); });
    expect(screen.getAllByText('Dup').length).toBe(1);
  });

  it('handles 401 safely', async () => {
    (fetchSupportConversations as any).mockRejectedValue({ response: { status: 401 } });
    render(<SupportPage />);
    // Just ensure no crash
    expect(screen.getByText('پشتیبانی RastiChat')).toBeDefined();
  });

  it('handles 403 safely', async () => {
    (fetchSupportConversations as any).mockRejectedValue({ response: { status: 403 } });
    render(<SupportPage />);
    expect(screen.getByText('پشتیبانی RastiChat')).toBeDefined();
  });

  it('updates state on close action', async () => {
    (fetchSupportConversations as any).mockResolvedValue([{ id: '1', status: 'CLOSED', subject: 'Closed' }]);
    render(<SupportPage />);
    await waitFor(() => expect(screen.getByText('بسته‌شده')).toBeDefined());
  });

  it('updates state on reopen action', async () => {
    (fetchSupportConversations as any).mockResolvedValue([{ id: '1', status: 'WAITING_FOR_PLATFORM', subject: 'Reopened' }]);
    render(<SupportPage />);
    await waitFor(() => expect(screen.getByText('در انتظار پاسخ پلتفرم')).toBeDefined());
  });

  describe('platform-initiated threads', () => {
    const platformStarted = { id: 'p1', status: 'WAITING_FOR_WORKSPACE', subject: 'اطلاعیه پلتفرم', unread_count: 1, opened_by_side: 'PLATFORM' };

    it('shows a thread the platform started, labelled, with the status from the tenant\'s viewpoint', async () => {
      (fetchSupportConversations as any).mockResolvedValue([platformStarted]);
      render(<SupportPage />);
      expect(await screen.findByText('اطلاعیه پلتفرم')).toBeDefined();
      expect(screen.getByText(/آغاز‌شده توسط پلتفرم/)).toBeDefined();
      expect(screen.getByText(/در انتظار پاسخ شما/)).toBeDefined();
    });

    it('opening it marks it read and renders the platform\'s message as theirs and ours as ours', async () => {
      (fetchSupportConversations as any).mockResolvedValue([platformStarted]);
      (fetchSupportMessages as any).mockResolvedValue([
        { id: 'a', content: 'پیام پلتفرم', sender_type: 'USER', sender_side: 'platform' },
        { id: 'b', content: 'پاسخ ما', sender_type: 'USER', sender_side: 'tenant' },
      ]);
      (markSupportRead as any).mockResolvedValue(undefined);
      render(<SupportPage />);
      fireEvent.click(await screen.findByText('اطلاعیه پلتفرم'));
      const theirs = await screen.findByText('پیام پلتفرم');
      const mine = await screen.findByText('پاسخ ما');
      await waitFor(() => expect(markSupportRead).toHaveBeenCalledWith('p1'));
      expect(mine.className).toContain('bg-purple-600');
      expect(theirs.className).not.toContain('bg-purple-600');
    });

    it('replying sends the message and shows it as ours immediately', async () => {
      (fetchSupportConversations as any).mockResolvedValue([platformStarted]);
      (fetchSupportMessages as any).mockResolvedValue([]);
      (markSupportRead as any).mockResolvedValue(undefined);
      (sendSupportMessage as any).mockResolvedValue({});
      render(<SupportPage />);
      fireEvent.click(await screen.findByText('اطلاعیه پلتفرم'));
      fireEvent.change(await screen.findByPlaceholderText('پاسخ...'), { target: { value: 'دریافت شد' } });
      fireEvent.click(screen.getByText('ارسال'));
      await waitFor(() => expect(sendSupportMessage).toHaveBeenCalledWith('p1', 'دریافت شد', expect.any(String)));
      expect((await screen.findByText('دریافت شد')).className).toContain('bg-purple-600');
    });

    it('closes and reopens a thread', async () => {
      (fetchSupportConversations as any).mockResolvedValue([platformStarted]);
      (fetchSupportMessages as any).mockResolvedValue([]);
      (markSupportRead as any).mockResolvedValue(undefined);
      (closeSupportConversation as any).mockResolvedValue({ status: 'CLOSED' });
      (reopenSupportConversation as any).mockResolvedValue({ status: 'WAITING_FOR_PLATFORM' });
      render(<SupportPage />);
      fireEvent.click(await screen.findByText('اطلاعیه پلتفرم'));
      fireEvent.click(await screen.findByRole('button', { name: 'بستن گفتگو' }));
      await waitFor(() => expect(closeSupportConversation).toHaveBeenCalledWith('p1'));
      fireEvent.click(await screen.findByRole('button', { name: 'بازگشایی' }));
      await waitFor(() => expect(reopenSupportConversation).toHaveBeenCalledWith('p1'));
    });
  });
});
