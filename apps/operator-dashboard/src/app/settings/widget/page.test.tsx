import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import WidgetSettingsPage from './page';

const push = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
vi.mock('next/link', () => ({ default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }));

vi.mock('@/lib/api', () => {
  class WidgetConfigError extends Error { constructor(public errors: Record<string, string>) { super('x'); } }
  return {
    fetchAdminProjects: vi.fn(), fetchWidgetConfig: vi.fn(), saveWidgetConfig: vi.fn(),
    widgetApiBase: () => 'https://chat.example.com/api/v1', WidgetConfigError,
  };
});
import { fetchAdminProjects, fetchWidgetConfig, saveWidgetConfig, WidgetConfigError } from '@/lib/api';

const effective = () => ({
  launcher: { enabled: true, mode: 'icon', position: 'bottom-right', offset: { x: 20, y: 20 }, label: '', tooltip: '', icon: 'chat', color: '#BC5A38', greeting: '', auto_open: false, mobile: { fullscreen: true } },
  behavior: { start_mode: 'on_first_message' },
  pre_chat: { enabled: false, title: '', submit_label: '', fields: [] },
  identity: { guest_allowed: true, authenticated_only: false }, locale: 'fa', direction: 'rtl',
});

describe('Widget settings page', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.localStorage = { getItem: vi.fn(() => 'token'), setItem: vi.fn(), removeItem: vi.fn() } as unknown as Storage;
    vi.mocked(fetchAdminProjects).mockResolvedValue([{ id: 7, name: 'فروشگاه نمونه', workspace_id: 1, public_key: 'PUB-KEY-1', is_active: true, allowed_domains: 'shop.example.com' }]);
    vi.mocked(fetchWidgetConfig).mockResolvedValue({ config: {}, effective: effective() });
  });

  it('redirects to login without a token', () => {
    global.localStorage = { getItem: vi.fn(() => null), setItem: vi.fn(), removeItem: vi.fn() } as unknown as Storage;
    render(<WidgetSettingsPage />);
    expect(push).toHaveBeenCalledWith('/login');
  });

  it('loads the project configuration and shows the embed code with only the public key', async () => {
    render(<WidgetSettingsPage />);
    await screen.findByText('دکمهٔ گفتگو');
    expect(fetchWidgetConfig).toHaveBeenCalledWith(7);
    const pre = document.querySelector('pre')!;
    expect(pre.textContent).toContain('projectKey: "PUB-KEY-1"');
    expect(pre.textContent).not.toMatch(/secret|password/i);
  });

  it('defaults to "no questions"; switching to one question shows a single editable field', async () => {
    render(<WidgetSettingsPage />);
    await screen.findByText('پرسش‌های پیش از گفتگو');
    expect((screen.getByLabelText(/بدون پرسش/) as HTMLInputElement).checked).toBe(true);
    expect(screen.queryAllByTestId('field-editor')).toHaveLength(0);
    fireEvent.click(screen.getByLabelText('یک پرسش'));
    expect(screen.getAllByTestId('field-editor')).toHaveLength(1);
    fireEvent.click(screen.getByLabelText('فرم ساختاریافته'));
    fireEvent.click(screen.getByText('+ افزودن پرسش'));
    expect(screen.getAllByTestId('field-editor')).toHaveLength(2);
  });

  it('saves the document built from the form (no code change needed to change questions)', async () => {
    vi.mocked(saveWidgetConfig).mockResolvedValue({ config: {}, effective: effective() });
    render(<WidgetSettingsPage />);
    await screen.findByText('پرسش‌های پیش از گفتگو');
    fireEvent.click(screen.getByLabelText('یک پرسش'));
    fireEvent.change(screen.getAllByLabelText('برچسب پرسش')[0], { target: { value: 'چه کمکی می‌خواهید؟' } });
    fireEvent.click(screen.getByRole('button', { name: 'ذخیره' }));
    await waitFor(() => expect(saveWidgetConfig).toHaveBeenCalled());
    const [id, doc] = vi.mocked(saveWidgetConfig).mock.calls[0] as [number, { pre_chat: { enabled: boolean; fields: { label: string; required: boolean }[] } }];
    expect(id).toBe(7);
    expect(doc.pre_chat.enabled).toBe(true);
    expect(doc.pre_chat.fields).toHaveLength(1);
    expect(doc.pre_chat.fields[0].label).toBe('چه کمکی می‌خواهید؟');
    expect(doc.pre_chat.fields[0].required).toBe(true);
    expect(await screen.findByText('تنظیمات ذخیره شد.')).toBeDefined();
  });

  it('shows the server\'s per-field validation errors next to the fields', async () => {
    vi.mocked(saveWidgetConfig).mockRejectedValue(new WidgetConfigError({ 'launcher.color': 'Must be a #RRGGBB colour.', 'pre_chat.fields[0].key': 'Duplicate key.' }));
    render(<WidgetSettingsPage />);
    await screen.findByText('پرسش‌های پیش از گفتگو');
    fireEvent.click(screen.getByLabelText('یک پرسش'));
    fireEvent.click(screen.getByRole('button', { name: 'ذخیره' }));
    expect(await screen.findByText('Must be a #RRGGBB colour.')).toBeDefined();
    expect(screen.getByText('Duplicate key.')).toBeDefined();
    expect(screen.getByText('لطفاً خطاهای مشخص‌شده را اصلاح کنید.')).toBeDefined();
  });

  it('authenticated-only and guest access are mutually exclusive', async () => {
    render(<WidgetSettingsPage />);
    await screen.findByText('هویت مشتری');
    const guest = screen.getByLabelText(/مهمان \(بدون ورود\)/) as HTMLInputElement;
    const authOnly = screen.getByLabelText(/فقط مشتریان واردشده/) as HTMLInputElement;
    expect(guest.checked).toBe(true);
    fireEvent.click(authOnly);
    expect(authOnly.checked).toBe(true);
    expect(guest.checked).toBe(false);
  });

  it('says so when the caller administers no project (403/empty)', async () => {
    vi.mocked(fetchAdminProjects).mockResolvedValue([]);
    render(<WidgetSettingsPage />);
    expect(await screen.findByText('پروژه‌ای برای مدیریت وجود ندارد.')).toBeDefined();
  });
});
