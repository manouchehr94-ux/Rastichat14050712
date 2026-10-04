import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import DashboardPage from './page';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock('@/lib/api', () => ({
  fetchConversations: vi.fn(),
  fetchMessages: vi.fn(),
  sendMessage: vi.fn(),
  connectWebSocket: vi.fn(() => ({ close: vi.fn() })),
  patchConversation: vi.fn(),
  markConversationRead: vi.fn(),
  assignConversation: vi.fn(),
  closeConversation: vi.fn(),
  reopenConversation: vi.fn(),
  fetchTeammates: vi.fn(),
  uploadAttachment: vi.fn(),
  shareProduct: vi.fn(),
  requestRating: vi.fn(),
  fetchProducts: vi.fn(),
  sendTypingEvent: vi.fn(),
  sendMarkReadEvent: vi.fn(),
  fetchTags: vi.fn(),
  fetchConversationTags: vi.fn(),
  attachConversationTag: vi.fn(),
  detachConversationTag: vi.fn(),
  fetchConversationNotes: vi.fn(),
  createConversationNote: vi.fn(),
  fetchCustomerContext: vi.fn(),
  fetchTeams: vi.fn(),
  fetchQueues: vi.fn(),
  claimConversation: vi.fn(),
  transferConversation: vi.fn(),
  escalateConversation: vi.fn(),
  setConversationPriority: vi.fn(),
  fetchAssignmentHistory: vi.fn(),
  createInternalNote: vi.fn(),
  fetchQuickReplies: vi.fn(),
  applyQuickReply: vi.fn(),
  fetchNotifications: vi.fn(),
  fetchUnreadNotificationCount: vi.fn(),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
  connectNotificationsWebSocket: vi.fn(() => ({ close: vi.fn() })),
  fetchKBArticles: vi.fn(),
  shareKBArticle: vi.fn(),
  fetchMacros: vi.fn(),
  previewMacro: vi.fn(),
  executeMacro: vi.fn(),
}));

import {
  fetchConversations, fetchMessages, connectWebSocket, uploadAttachment, shareProduct,
  fetchProducts, fetchTags, fetchConversationTags, attachConversationTag,
  fetchConversationNotes, createConversationNote, fetchCustomerContext, markConversationRead,
  assignConversation, closeConversation, reopenConversation, fetchTeammates,
  fetchTeams, fetchQueues, claimConversation, transferConversation, escalateConversation, setConversationPriority,
  fetchAssignmentHistory, createInternalNote, fetchQuickReplies, applyQuickReply,
  fetchNotifications, fetchUnreadNotificationCount, markNotificationRead, markAllNotificationsRead,
  fetchKBArticles, shareKBArticle, fetchMacros, previewMacro, executeMacro,
} from '@/lib/api';

const visitorA = { id: 'v1', name: 'سارا محمدی', email: null, mobile: '0912', created_at: '2024-01-01T00:00:00Z' };
const visitorB = { id: 'v2', name: 'علی رضایی', email: null, mobile: null, created_at: '2024-01-01T00:00:00Z' };

const convA = {
  id: 'c1', status: 'OPEN', subject: '', category: '', notes: '', rating: null,
  created_at: '2024-01-01T10:00:00Z', updated_at: '2024-01-01T10:00:00Z', unread_count: 2,
  visitor: visitorA, last_message: { content: 'سلام دنیا', message_type: 'TEXT', sender_type: 'VISITOR', created_at: '2024-01-01T10:00:00Z' },
  priority: 'NORMAL', queue: null, team: null, sla: null,
};
const convB = {
  id: 'c2', status: 'PENDING', subject: '', category: '', notes: '', rating: null,
  created_at: '2024-01-01T09:00:00Z', updated_at: '2024-01-01T09:00:00Z', unread_count: 0,
  visitor: visitorB, last_message: { content: 'ممنون', message_type: 'TEXT', sender_type: 'VISITOR', created_at: '2024-01-01T09:00:00Z' },
  priority: 'NORMAL', queue: null, team: null, sla: null,
};
const convClosed = {
  id: 'c3', status: 'CLOSED', subject: '', category: '', notes: '', rating: null, closed_at: '2024-01-02T00:00:00Z',
  created_at: '2024-01-01T08:00:00Z', updated_at: '2024-01-01T08:00:00Z', unread_count: 0,
  visitor: { id: 'v3', name: 'رضا کریمی', email: null, mobile: null, created_at: '2024-01-01T00:00:00Z' }, last_message: null,
};

const product = {
  id: 'p1', brand: 'Arom', name: 'Candle', price: '890000', old_price: '1120000', currency: 'IRT', discount_percent: 21,
  rating: '5', reviews_count: 12, image: '', product_url: '', is_available: true,
};

function setDefaultMocks() {
  vi.mocked(fetchConversations).mockResolvedValue([convA, convB]);
  vi.mocked(fetchMessages).mockResolvedValue([]);
  vi.mocked(fetchProducts).mockResolvedValue([product]);
  vi.mocked(fetchTags).mockResolvedValue([{ id: 't1', name: 'VIP', color: '' }]);
  vi.mocked(fetchConversationTags).mockResolvedValue([]);
  vi.mocked(fetchConversationNotes).mockResolvedValue([]);
  vi.mocked(fetchCustomerContext).mockResolvedValue({
    name: 'سارا محمدی', phone: '0912', location: 'تهران', customer_since: '2024-01-01T00:00:00Z',
    order_count: 3, total_spent: '2340000', score: '4.9', recent_orders: [],
  });
  vi.mocked(markConversationRead).mockResolvedValue(undefined);
  vi.mocked(fetchTeammates).mockResolvedValue([{ id: 'op1', display_name: 'همکار یک', email: 'op1@test.com' }]);
  vi.mocked(fetchTeams).mockResolvedValue([{ id: 'team1', name: 'فروش', is_active: true }]);
  vi.mocked(fetchQueues).mockResolvedValue([{ id: 'q1', name: 'صف فروش', team: 'team1', is_active: true }]);
  vi.mocked(fetchAssignmentHistory).mockResolvedValue([]);
  vi.mocked(fetchQuickReplies).mockResolvedValue([]);
  vi.mocked(fetchNotifications).mockResolvedValue([]);
  vi.mocked(fetchUnreadNotificationCount).mockResolvedValue({ count: 0 });
  vi.mocked(fetchKBArticles).mockResolvedValue([]);
  vi.mocked(fetchMacros).mockResolvedValue([]);
}

// jsdom doesn't implement scrollIntoView; the page calls it on every message-list update.
Element.prototype.scrollIntoView = vi.fn();

// Once a conversation is selected, its visitor name renders both in the sidebar
// list item and in the chat header, so plain getByText becomes ambiguous. The
// sidebar occurrence always renders first in document order.
async function selectConversation(name: string) {
  const matches = await screen.findAllByText(name);
  fireEvent.click(matches[0]);
}

describe('Operator dashboard — customer conversations page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.localStorage = {
      getItem: vi.fn(() => 'token'), setItem: vi.fn(), removeItem: vi.fn(),
    } as unknown as Storage;
    setDefaultMocks();
  });

  it('renders the conversation list with visitor names and last-message previews', async () => {
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText('سارا محمدی')).toBeDefined());
    expect(screen.getByText('علی رضایی')).toBeDefined();
    expect(screen.getByText('سلام دنیا')).toBeDefined();
  });

  it('shows an unread badge only for conversations with unread_count > 0', async () => {
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText('2')).toBeDefined());
    expect(screen.queryByText('0')).toBeNull();
  });

  it('filters the list by search text', async () => {
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText('سارا محمدی')).toBeDefined());
    fireEvent.change(screen.getByPlaceholderText('جستجوی مشتری یا گفتگو…'), { target: { value: 'علی' } });
    expect(screen.queryByText('سارا محمدی')).toBeNull();
    expect(screen.getByText('علی رضایی')).toBeDefined();
  });

  it('filters the list by status tab', async () => {
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText('سارا محمدی')).toBeDefined());
    fireEvent.click(screen.getByRole('button', { name: 'در انتظار' }));
    expect(screen.queryByText('سارا محمدی')).toBeNull();
    expect(screen.getByText('علی رضایی')).toBeDefined();
  });

  it('selecting a conversation loads its messages, tags, notes, customer context and connects a socket', async () => {
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText('سارا محمدی')).toBeDefined());
    await selectConversation('سارا محمدی');
    await waitFor(() => expect(fetchMessages).toHaveBeenCalledWith('c1'));
    expect(connectWebSocket).toHaveBeenCalledWith('c1', expect.any(Function));
    expect(fetchConversationTags).toHaveBeenCalledWith('c1');
    expect(fetchConversationNotes).toHaveBeenCalledWith('c1');
    expect(fetchCustomerContext).toHaveBeenCalledWith('c1');
    expect(markConversationRead).toHaveBeenCalledWith('c1');
  });

  it('renders a product message card with brand, name and price', async () => {
    vi.mocked(fetchMessages).mockResolvedValue([
      { id: 'm1', sender_type: 'USER', content: '', message_type: 'PRODUCT', client_message_id: 'm1', created_at: '2024-01-01T10:00:00Z', seen: true, attachment_url: null,
        metadata: { brand: 'Arom', name: 'Candle', price: '890000', old_price: null, rating: '5', reviews_count: 12 } },
    ]);
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText('سارا محمدی')).toBeDefined());
    await selectConversation('سارا محمدی');
    await waitFor(() => expect(screen.getByText('Candle')).toBeDefined());
    expect(screen.getByText('Arom')).toBeDefined();
  });

  it('renders an incoming image message with its caption', async () => {
    vi.mocked(fetchMessages).mockResolvedValue([
      { id: 'm1', sender_type: 'VISITOR', content: '', message_type: 'IMAGE', client_message_id: 'm1', created_at: '2024-01-01T10:00:00Z', seen: false,
        attachment_url: 'https://example.com/pic.png', metadata: { caption: 'این عکسه' } },
    ]);
    render(<DashboardPage />);
    await selectConversation('سارا محمدی');
    await waitFor(() => expect(screen.getByText('این عکسه')).toBeDefined());
  });

  it('renders a voice message with a play control', async () => {
    vi.mocked(fetchMessages).mockResolvedValue([
      { id: 'm1', sender_type: 'VISITOR', content: '', message_type: 'VOICE', client_message_id: 'm1', created_at: '2024-01-01T10:00:00Z', seen: false,
        attachment_url: 'https://example.com/note.webm', metadata: { duration: 12 } },
    ]);
    render(<DashboardPage />);
    await selectConversation('سارا محمدی');
    await waitFor(() => expect(screen.getByText('▶')).toBeDefined());
  });

  it('opens the product picker and shares a product', async () => {
    vi.mocked(shareProduct).mockResolvedValue({
      id: 'm2', sender_type: 'USER', content: '', message_type: 'PRODUCT', client_message_id: 'gen', created_at: new Date().toISOString(), seen: true,
      attachment_url: null, metadata: { brand: 'Arom', name: 'Candle', price: '890000' },
    });
    render(<DashboardPage />);
    await selectConversation('سارا محمدی');
    fireEvent.click(await screen.findByTitle('معرفی محصول'));
    fireEvent.click(await screen.findByText('Candle'));
    await waitFor(() => expect(shareProduct).toHaveBeenCalledWith('c1', 'p1', expect.any(String)));
  });

  it('shows the discount badge for a discounted product and re-searches the catalog as the operator types', async () => {
    render(<DashboardPage />);
    await selectConversation('سارا محمدی');
    fireEvent.click(await screen.findByTitle('معرفی محصول'));
    await screen.findByText('Candle');
    expect(screen.getByText('٪21-')).toBeDefined();

    fireEvent.change(screen.getByPlaceholderText('جستجوی محصول یا برند…'), { target: { value: 'Vase' } });
    await waitFor(() => expect(fetchProducts).toHaveBeenLastCalledWith('Vase'), { timeout: 1000 });
  });

  it('disables sharing an out-of-stock product', async () => {
    vi.mocked(fetchProducts).mockResolvedValue([{ ...product, is_available: false }]);
    render(<DashboardPage />);
    await selectConversation('سارا محمدی');
    fireEvent.click(await screen.findByTitle('معرفی محصول'));
    await screen.findByText('ناموجود');
    const productBtn = screen.getByText('Candle').closest('button') as HTMLButtonElement;
    expect(productBtn.disabled).toBe(true);
    fireEvent.click(productBtn);
    expect(shareProduct).not.toHaveBeenCalled();
  });

  it('uploads an image attachment via the file input', async () => {
    vi.mocked(uploadAttachment).mockResolvedValue({
      id: 'm3', sender_type: 'USER', content: '', message_type: 'IMAGE', client_message_id: 'gen', created_at: new Date().toISOString(), seen: true,
      attachment_url: 'https://example.com/uploaded.png', metadata: {},
    });
    const { container } = render(<DashboardPage />);
    await selectConversation('سارا محمدی');
    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['abc'], 'pic.png', { type: 'image/png' });
    Object.defineProperty(fileInput, 'files', { value: [file] });
    fireEvent.change(fileInput);
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledWith('c1', file, 'IMAGE', expect.any(String)));
  });

  it('attaches a workspace tag to the selected conversation', async () => {
    vi.mocked(attachConversationTag).mockResolvedValue([{ id: 't1', name: 'VIP', color: '' }]);
    render(<DashboardPage />);
    await selectConversation('سارا محمدی');
    const tagButton = await screen.findByText('VIP');
    fireEvent.click(tagButton);
    await waitFor(() => expect(attachConversationTag).toHaveBeenCalledWith('c1', 't1'));
  });

  it('adds an operator note and renders it in the note list', async () => {
    vi.mocked(createConversationNote).mockResolvedValue({ id: 'n1', body: 'مشتری علاقه‌مند به رنگ گرم است', created_by_email: 'op@test.com', created_at: new Date().toISOString() });
    render(<DashboardPage />);
    await selectConversation('سارا محمدی');
    const textarea = await screen.findByPlaceholderText('یادداشت خصوصی درباره این مشتری…');
    fireEvent.change(textarea, { target: { value: 'مشتری علاقه‌مند به رنگ گرم است' } });
    fireEvent.click(screen.getByText('افزودن یادداشت'));
    await waitFor(() => expect(createConversationNote).toHaveBeenCalledWith('c1', 'مشتری علاقه‌مند به رنگ گرم است'));
    await waitFor(() => expect(screen.getByText('مشتری علاقه‌مند به رنگ گرم است')).toBeDefined());
  });

  it('renders the customer-context summary (phone, location, orders, spend)', async () => {
    render(<DashboardPage />);
    await selectConversation('سارا محمدی');
    await waitFor(() => expect(screen.getByText('📍 تهران')).toBeDefined());
    expect(screen.getByText('3')).toBeDefined();
  });

  it('degrades gracefully when a tenant has no customer-context data', async () => {
    vi.mocked(fetchCustomerContext).mockRejectedValue(new Error('not found'));
    render(<DashboardPage />);
    await selectConversation('سارا محمدی');
    await waitFor(() => expect(screen.getByText('یادداشت‌های اپراتور')).toBeDefined());
    expect(screen.queryByText('📍')).toBeNull();
  });

  it('mobile navigation: back button returns to the list, info button opens and closes the overlay', async () => {
    render(<DashboardPage />);
    await selectConversation('سارا محمدی');

    // Back to list (mobile-only control, always present in the DOM in jsdom)
    fireEvent.click(screen.getByText('›'));
    // Selecting again to get back into chat view for the info-overlay assertions
    await selectConversation('سارا محمدی');

    fireEvent.click(screen.getByTitle('اطلاعات مشتری'));
    const overlays = screen.getAllByText('خلاصه مشتری');
    expect(overlays.length).toBe(2); // desktop panel + mobile overlay both mounted
    expect(screen.getByText('پروفایل مشتری')).toBeDefined();

    fireEvent.click(screen.getByTitle('بازگشت'));
    await waitFor(() => expect(screen.queryByText('پروفایل مشتری')).toBeNull());
  });

  it('shows a friendly error and does not crash when the conversation list fails to load', async () => {
    vi.mocked(fetchConversations).mockRejectedValue(new Error('network error'));
    render(<DashboardPage />);
    await waitFor(() => expect(screen.getByText('Failed to load conversations')).toBeDefined());
  });

  it('deduplicates a websocket echo of a message already rendered', async () => {
    const wsCallbacks: Array<(data: unknown) => void> = [];
    vi.mocked(connectWebSocket).mockImplementation((_id: string, cb: (data: unknown) => void) => {
      wsCallbacks.push(cb);
      return { close: vi.fn() } as unknown as WebSocket;
    });
    vi.mocked(fetchMessages).mockResolvedValue([
      { id: 'm1', sender_type: 'VISITOR', content: 'پیام تکراری', message_type: 'TEXT', client_message_id: 'dup-1', created_at: '2024-01-01T10:00:00Z', seen: false, attachment_url: null, metadata: {} },
    ]);
    render(<DashboardPage />);
    await selectConversation('سارا محمدی');
    await waitFor(() => expect(screen.getByText('پیام تکراری')).toBeDefined());

    act(() => {
      wsCallbacks[0]({
        id: 'm1', sender_type: 'VISITOR', content: 'پیام تکراری', message_type: 'TEXT',
        client_message_id: 'dup-1', created_at: '2024-01-01T10:00:00Z', seen: false, attachment_url: null, metadata: {},
      });
    });
    expect(screen.getAllByText('پیام تکراری').length).toBe(1);
  });

  it('reconnects by opening a fresh socket when a different conversation is selected', async () => {
    render(<DashboardPage />);
    await selectConversation('سارا محمدی');
    await waitFor(() => expect(connectWebSocket).toHaveBeenCalledWith('c1', expect.any(Function)));
    await selectConversation('علی رضایی');
    await waitFor(() => expect(connectWebSocket).toHaveBeenCalledWith('c2', expect.any(Function)));
    expect(connectWebSocket).toHaveBeenCalledTimes(2);
  });

  it('loads teammates for the selected conversation and reassigns via the dropdown', async () => {
    vi.mocked(assignConversation).mockResolvedValue({ ...convA, assigned_to: { id: 'op1', display_name: 'همکار یک', email: 'op1@test.com' } });
    render(<DashboardPage />);
    await selectConversation('سارا محمدی');
    await waitFor(() => expect(fetchTeammates).toHaveBeenCalledWith('c1'));

    const select = await screen.findByTitle('واگذاری به همکار');
    fireEvent.change(select, { target: { value: 'op1' } });
    await waitFor(() => expect(assignConversation).toHaveBeenCalledWith('c1', 'op1'));
  });

  it('shows a reopen button for closed conversations and reopens them', async () => {
    vi.mocked(fetchConversations).mockResolvedValue([convA, convB, convClosed]);
    vi.mocked(reopenConversation).mockResolvedValue({ ...convClosed, status: 'OPEN', closed_at: null });
    render(<DashboardPage />);
    await selectConversation('رضا کریمی');
    const reopenBtn = await screen.findByText('بازگشایی');
    fireEvent.click(reopenBtn);
    await waitFor(() => expect(reopenConversation).toHaveBeenCalledWith('c3'));
    await waitFor(() => expect(screen.getByText('گفتگو دوباره باز شد')).toBeDefined());
  });

  it('shows a dismissible error toast when an action fails, instead of a blocking alert', async () => {
    vi.mocked(closeConversation).mockRejectedValue(new Error('network error'));
    render(<DashboardPage />);
    await selectConversation('سارا محمدی');
    fireEvent.click(await screen.findByText('پایان گفتگو'));
    const toastMsg = await screen.findByText('پایان گفتگو ناموفق بود');
    expect(toastMsg).toBeDefined();
    fireEvent.click(screen.getByText('✕'));
    await waitFor(() => expect(screen.queryByText('پایان گفتگو ناموفق بود')).toBeNull());
  });

  describe('voice recording', () => {
    let clock = 1_000_000;
    class FakeMediaRecorder {
      static instances: FakeMediaRecorder[] = [];
      state: 'inactive' | 'recording' = 'inactive';
      ondataavailable: ((e: { data: Blob }) => void) | null = null;
      onstop: (() => void) | null = null;
      constructor() { FakeMediaRecorder.instances.push(this); }
      start() { this.state = 'recording'; }
      stop() {
        this.state = 'inactive';
        this.ondataavailable?.({ data: new Blob(['x'], { type: 'audio/webm' }) });
        this.onstop?.();
      }
    }

    beforeEach(() => {
      clock = 1_000_000;
      vi.spyOn(Date, 'now').mockImplementation(() => clock);
      FakeMediaRecorder.instances = [];
      // @ts-expect-error test double
      global.MediaRecorder = FakeMediaRecorder;
      Object.defineProperty(navigator, 'mediaDevices', {
        value: { getUserMedia: vi.fn(() => Promise.resolve({ getTracks: () => [{ stop: vi.fn() }] })) },
        configurable: true,
      });
    });

    it('records and uploads a voice message on mic start/stop', async () => {
      vi.mocked(uploadAttachment).mockResolvedValue({
        id: 'm4', sender_type: 'USER', content: '', message_type: 'VOICE', client_message_id: 'gen',
        created_at: new Date().toISOString(), seen: true, attachment_url: 'https://example.com/v.webm', metadata: { duration: 2 },
      });
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      const micBtn = await screen.findByTitle('پیام صوتی');
      fireEvent.click(micBtn);
      await waitFor(() => expect(FakeMediaRecorder.instances.length).toBe(1));

      clock += 1500;
      fireEvent.click(micBtn);
      await waitFor(() => expect(uploadAttachment).toHaveBeenCalledWith('c1', expect.any(File), 'VOICE', expect.any(String), { duration: '2' }));
    });

    it('cancels a recording without uploading anything', async () => {
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      const micBtn = await screen.findByTitle('پیام صوتی');
      fireEvent.click(micBtn);
      await waitFor(() => expect(FakeMediaRecorder.instances.length).toBe(1));

      clock += 1500;
      fireEvent.click(await screen.findByTitle('لغو ضبط'));
      await new Promise((r) => setTimeout(r, 0));
      expect(uploadAttachment).not.toHaveBeenCalled();
    });

    it('shows an inline toast instead of alert() when microphone access is denied', async () => {
      Object.defineProperty(navigator, 'mediaDevices', {
        value: { getUserMedia: vi.fn(() => Promise.reject(new Error('denied'))) },
        configurable: true,
      });
      const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      fireEvent.click(await screen.findByTitle('پیام صوتی'));
      await screen.findByText('برای ارسال پیام صوتی، دسترسی به میکروفون لازم است.');
      expect(alertSpy).not.toHaveBeenCalled();
    });
  });

  describe('team operations: queues, priority, transfer, escalation, notes, quick replies, notifications', () => {
    it('filters the list by queue, team, priority, and unassigned/mine', async () => {
      const convUrgent = {
        ...convB, id: 'c4', priority: 'URGENT', queue: 'q1', team: 'team1',
        visitor: { id: 'v4', name: 'نگار احمدی', email: null, mobile: null, created_at: '2024-01-01T00:00:00Z' },
        assigned_to: null,
      };
      vi.mocked(fetchConversations).mockResolvedValue([convA, convB, convUrgent]);
      render(<DashboardPage />);
      await waitFor(() => expect(screen.getByText('سارا محمدی')).toBeDefined());
      expect(screen.getByText('نگار احمدی')).toBeDefined();

      fireEvent.click(screen.getByTitle('فیلترها'));
      const priorityFilter = await screen.findByDisplayValue('همه اولویت‌ها');
      fireEvent.change(priorityFilter, { target: { value: 'URGENT' } });
      expect(screen.queryByText('سارا محمدی')).toBeNull();
      expect(screen.getByText('نگار احمدی')).toBeDefined();
    });

    it('shows a claim button for an unassigned conversation and claims it', async () => {
      const unassigned = { ...convA, assigned_to: null };
      vi.mocked(fetchConversations).mockResolvedValue([unassigned, convB]);
      vi.mocked(claimConversation).mockResolvedValue({ ...unassigned, assigned_to: { id: 'me', display_name: 'من', email: 'me@test.com' } });
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      const claimButtons = await screen.findAllByText('برداشتن');
      expect(claimButtons.length).toBeGreaterThan(0);
      fireEvent.click(claimButtons[0]);
      await waitFor(() => expect(claimConversation).toHaveBeenCalledWith('c1'));
    });

    it('transfers the conversation to a different team', async () => {
      vi.mocked(transferConversation).mockResolvedValue({ ...convA, team: 'team1', team_name: 'فروش', assigned_to: null });
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      fireEvent.click(await screen.findByText('انتقال تیم'));
      fireEvent.click(await screen.findByText('فروش'));
      await waitFor(() => expect(transferConversation).toHaveBeenCalledWith('c1', 'team1'));
    });

    it('escalates the conversation to a supervisor', async () => {
      vi.mocked(escalateConversation).mockResolvedValue({ ...convA, priority: 'URGENT' });
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      fireEvent.click(await screen.findByTitle('تشدید به سرپرست'));
      await waitFor(() => expect(escalateConversation).toHaveBeenCalledWith('c1'));
    });

    it('changes the conversation priority', async () => {
      vi.mocked(setConversationPriority).mockResolvedValue({ ...convA, priority: 'HIGH' });
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      const prioritySelect = await screen.findByTitle('اولویت');
      fireEvent.change(prioritySelect, { target: { value: 'HIGH' } });
      await waitFor(() => expect(setConversationPriority).toHaveBeenCalledWith('c1', 'HIGH'));
    });

    it('switches to internal-note mode and sends a note that never touches sendMessage', async () => {
      vi.mocked(createInternalNote).mockResolvedValue({
        id: 'n1', sender_type: 'USER', content: 'یادداشت مخفی', message_type: 'INTERNAL_NOTE', metadata: {},
        attachment_url: null, client_message_id: 'gen', created_at: new Date().toISOString(), seen: true,
      });
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      fireEvent.click(screen.getByText('💬 پاسخ به مشتری (کلیک برای یادداشت داخلی)'));
      const input = screen.getByPlaceholderText('یادداشت داخلی (فقط برای همکاران)…');
      fireEvent.change(input, { target: { value: 'یادداشت مخفی' } });
      fireEvent.click(screen.getByText('➤'));
      await waitFor(() => expect(createInternalNote).toHaveBeenCalledWith('c1', 'یادداشت مخفی', expect.any(String), []));
      expect(await screen.findByText('🔒 یادداشت داخلی')).toBeDefined();
    });

    it('mentions a teammate while composing an internal note', async () => {
      vi.mocked(createInternalNote).mockResolvedValue({
        id: 'n2', sender_type: 'USER', content: 'note', message_type: 'INTERNAL_NOTE', metadata: {},
        attachment_url: null, client_message_id: 'gen', created_at: new Date().toISOString(), seen: true,
      });
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      fireEvent.click(screen.getByText('💬 پاسخ به مشتری (کلیک برای یادداشت داخلی)'));
      fireEvent.click(screen.getByTitle('اشاره به همکار'));
      fireEvent.click(await screen.findByText('@همکار یک'));
      expect(screen.getByText('@همکار یک', { selector: 'span' })).toBeDefined();

      const input = screen.getByPlaceholderText('یادداشت داخلی (فقط برای همکاران)…');
      fireEvent.change(input, { target: { value: 'note' } });
      fireEvent.click(screen.getByText('➤'));
      await waitFor(() => expect(createInternalNote).toHaveBeenCalledWith('c1', 'note', expect.any(String), ['op1']));
    });

    it('uses a managed quick reply to fill the composer', async () => {
      vi.mocked(fetchQuickReplies).mockResolvedValue([{ id: 'qr1', scope: 'WORKSPACE', title: 'خوش‌آمد', body: 'سلام وقت بخیر', shortcut: '', category: '', usage_count: 3 }]);
      vi.mocked(applyQuickReply).mockResolvedValue({ body: 'سلام وقت بخیر', usage_count: 4 });
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      fireEvent.click(await screen.findByText('خوش‌آمد'));
      await waitFor(() => expect(applyQuickReply).toHaveBeenCalledWith('qr1', 'c1'));
      const input = screen.getByPlaceholderText('پاسخ به مشتری…') as HTMLInputElement;
      await waitFor(() => expect(input.value).toContain('سلام وقت بخیر'));
    });

    it('shows unread notification count and marks a notification read', async () => {
      vi.mocked(fetchNotifications).mockResolvedValue([
        { id: 'notif1', event_type: 'CONVERSATION_ASSIGNED', title: 'گفتگویی واگذار شد', payload: {}, read_at: null, created_at: new Date().toISOString() },
      ]);
      vi.mocked(fetchUnreadNotificationCount).mockResolvedValue({ count: 1 });
      render(<DashboardPage />);
      await waitFor(() => expect(screen.getByText('1')).toBeDefined());
      fireEvent.click(screen.getByTitle('اعلان‌ها'));
      fireEvent.click(await screen.findByText('گفتگویی واگذار شد'));
      await waitFor(() => expect(markNotificationRead).toHaveBeenCalledWith('notif1'));
    });

    it('marks all notifications read', async () => {
      vi.mocked(fetchNotifications).mockResolvedValue([
        { id: 'notif1', event_type: 'MENTIONED', title: 'اشاره شدید', payload: {}, read_at: null, created_at: new Date().toISOString() },
      ]);
      vi.mocked(fetchUnreadNotificationCount).mockResolvedValue({ count: 1 });
      render(<DashboardPage />);
      fireEvent.click(await screen.findByTitle('اعلان‌ها'));
      fireEvent.click(await screen.findByText('علامت‌گذاری همه'));
      await waitFor(() => expect(markAllNotificationsRead).toHaveBeenCalled());
    });

    it('shows the assignment history panel for the selected conversation', async () => {
      vi.mocked(fetchAssignmentHistory).mockResolvedValue([
        { id: 'h1', action: 'CLAIM', assigned_to_email: 'op1@test.com', assigned_by_email: null, previous_assignee_email: null, previous_team_name: null, new_team_name: null, reason: '', created_at: '2024-01-01T10:05:00Z' },
      ]);
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      await waitFor(() => expect(fetchAssignmentHistory).toHaveBeenCalledWith('c1'));
      expect(screen.getByText('برداشت از صف')).toBeDefined();
      expect(screen.getByText(/op1@test.com/)).toBeDefined();
    });
  });

  describe('Knowledge Base + Macro composer integration', () => {
    const article = { id: 'art-1', title: 'راهنمای مرجوعی', excerpt: 'خلاصه', status: 'PUBLISHED', visibility: 'CUSTOMER' };
    const macro = { id: 'macro-1', name: 'درخواست مرجوعی', category: 'مرجوعی', description: '', is_active: true };

    it('searches the Knowledge Base and sends an article card', async () => {
      vi.mocked(fetchKBArticles).mockResolvedValue([article]);
      vi.mocked(shareKBArticle).mockResolvedValue({
        id: 'm3', sender_type: 'USER', content: 'راهنمای مرجوعی', message_type: 'ARTICLE', client_message_id: 'gen',
        created_at: new Date().toISOString(), seen: true, attachment_url: null,
        metadata: { article: { article_id: 'art-1', title: 'راهنمای مرجوعی', excerpt: 'خلاصه', category: '', url: '/kb/refund-guide' } },
      });
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      fireEvent.click(await screen.findByTitle('جستجوی پایگاه دانش'));
      fireEvent.click(await screen.findByText('ارسال کارت مقاله'));
      await waitFor(() => expect(shareKBArticle).toHaveBeenCalledWith('art-1', 'c1', expect.any(String)));
    });

    it('inserts an article link into the composer text instead of sending', async () => {
      vi.mocked(fetchKBArticles).mockResolvedValue([article]);
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      fireEvent.click(await screen.findByTitle('جستجوی پایگاه دانش'));
      fireEvent.click(await screen.findByText('درج در متن'));
      expect(shareKBArticle).not.toHaveBeenCalled();
      expect(screen.getByPlaceholderText('پاسخ به مشتری…')).toHaveProperty('value', '[راهنمای مرجوعی]');
    });

    it('opens the macro palette, previews with no side effects, then confirms execution', async () => {
      vi.mocked(fetchMacros).mockResolvedValue([macro]);
      vi.mocked(previewMacro).mockResolvedValue({
        macro_id: 'macro-1', macro_name: 'درخواست مرجوعی',
        actions: [{ type: 'SEND_REPLY', preview: 'سلام سارا محمدی، درخواست شما ثبت شد.' }],
      });
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      fireEvent.click(await screen.findByTitle('اجرای ماکرو'));
      fireEvent.click(await screen.findByText('درخواست مرجوعی'));

      await waitFor(() => expect(previewMacro).toHaveBeenCalledWith('macro-1', 'c1'));
      expect(await screen.findByText('سلام سارا محمدی، درخواست شما ثبت شد.')).toBeDefined();
      // Preview alone must never execute anything.
      expect(executeMacro).not.toHaveBeenCalled();

      vi.mocked(executeMacro).mockResolvedValue({ id: 'exec-1', status: 'SUCCEEDED', action_executions: [] });
      fireEvent.click(screen.getByText('تأیید و اجرا'));
      await waitFor(() => expect(executeMacro).toHaveBeenCalledWith('macro-1', 'c1', expect.any(String)));
    });

    it('shows partial-failure detail after a macro execution with a failed action', async () => {
      vi.mocked(fetchMacros).mockResolvedValue([macro]);
      vi.mocked(previewMacro).mockResolvedValue({ macro_id: 'macro-1', macro_name: 'درخواست مرجوعی', actions: [] });
      vi.mocked(executeMacro).mockResolvedValue({
        id: 'exec-1', status: 'PARTIALLY_SUCCEEDED',
        action_executions: [{ id: 'ae-1', action_index: 0, action_type: 'ADD_TAG', status: 'FAILED', error_summary: 'not found' }],
      });
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      fireEvent.click(await screen.findByTitle('اجرای ماکرو'));
      fireEvent.click(await screen.findByText('درخواست مرجوعی'));
      fireEvent.click(await screen.findByText('تأیید و اجرا'));
      await waitFor(() => expect(screen.getByText(/به‌صورت ناقص اجرا شد/)).toBeDefined());
      expect(screen.getByText(/ADD_TAG/)).toBeDefined();
    });

    it('double-clicking confirm during an in-flight execution does not send a second request', async () => {
      vi.mocked(fetchMacros).mockResolvedValue([macro]);
      vi.mocked(previewMacro).mockResolvedValue({ macro_id: 'macro-1', macro_name: 'درخواست مرجوعی', actions: [] });
      let resolveExecute: (v: unknown) => void = () => {};
      vi.mocked(executeMacro).mockReturnValue(new Promise(resolve => { resolveExecute = resolve; }));
      render(<DashboardPage />);
      await selectConversation('سارا محمدی');
      fireEvent.click(await screen.findByTitle('اجرای ماکرو'));
      fireEvent.click(await screen.findByText('درخواست مرجوعی'));
      const confirmBtn = await screen.findByText('تأیید و اجرا');
      fireEvent.click(confirmBtn);
      // While in flight the button is disabled — a second click can't fire another request.
      fireEvent.click(screen.getByText('در حال اجرا…'));
      expect(executeMacro).toHaveBeenCalledTimes(1);
      await act(async () => { resolveExecute({ id: 'exec-1', status: 'SUCCEEDED', action_executions: [] }); });
    });
  });
});
