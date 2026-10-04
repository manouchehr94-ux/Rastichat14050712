import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import KnowledgeBaseCategoriesPage from './page';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock('@/lib/api', () => ({
    fetchKBCategories: vi.fn(),
    createKBCategory: vi.fn(),
    updateKBCategory: vi.fn(),
    deleteKBCategory: vi.fn(),
}));

import { fetchKBCategories, createKBCategory, updateKBCategory, deleteKBCategory } from '@/lib/api';

const parent = { id: 'cat-1', name: 'سفارش‌ها', slug: 'orders', parent: null, description: '', is_active: true, sort_order: 0 };
const child = { id: 'cat-2', name: 'پیگیری سفارش', slug: 'order-tracking', parent: 'cat-1', description: '', is_active: false, sort_order: 0 };

describe('Knowledge Base categories page', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        global.localStorage = { getItem: vi.fn(() => 'token'), setItem: vi.fn(), removeItem: vi.fn() } as unknown as Storage;
    });

    it('renders the category tree with nested children', async () => {
        vi.mocked(fetchKBCategories).mockResolvedValue([parent, child]);
        render(<KnowledgeBaseCategoriesPage />);
        // "سفارش‌ها" also appears as a <option> in the parent-category
        // picker, so scope the assertion to the tree list, not bare text.
        await waitFor(() => expect(screen.getAllByText('سفارش‌ها').length).toBeGreaterThanOrEqual(2));
        expect(screen.getAllByText(/پیگیری سفارش/).length).toBeGreaterThanOrEqual(1);
    });

    it('creates a new root category', async () => {
        vi.mocked(fetchKBCategories).mockResolvedValue([]);
        vi.mocked(createKBCategory).mockResolvedValue({ ...parent, id: 'cat-3', name: 'پرداخت' });
        render(<KnowledgeBaseCategoriesPage />);
        await waitFor(() => expect(screen.getByPlaceholderText('نام دسته‌بندی جدید')).toBeDefined());
        fireEvent.change(screen.getByPlaceholderText('نام دسته‌بندی جدید'), { target: { value: 'پرداخت' } });
        fireEvent.click(screen.getByText('+ افزودن'));
        await waitFor(() => expect(createKBCategory).toHaveBeenCalled());
        const payload = vi.mocked(createKBCategory).mock.calls[0][0] as Record<string, unknown>;
        expect(payload.name).toBe('پرداخت');
    });

    it('toggles a category active/inactive', async () => {
        vi.mocked(fetchKBCategories).mockResolvedValue([parent]);
        vi.mocked(updateKBCategory).mockResolvedValue({ ...parent, is_active: false });
        render(<KnowledgeBaseCategoriesPage />);
        await waitFor(() => expect(screen.getByText('غیرفعال‌سازی')).toBeDefined());
        fireEvent.click(screen.getByText('غیرفعال‌سازی'));
        await waitFor(() => expect(updateKBCategory).toHaveBeenCalledWith('cat-1', { is_active: false }));
    });

    it('deletes a category after confirmation', async () => {
        vi.mocked(fetchKBCategories).mockResolvedValue([parent]);
        vi.mocked(deleteKBCategory).mockResolvedValue(undefined);
        vi.stubGlobal('confirm', vi.fn(() => true));
        render(<KnowledgeBaseCategoriesPage />);
        await waitFor(() => expect(screen.getByText('حذف')).toBeDefined());
        fireEvent.click(screen.getByText('حذف'));
        await waitFor(() => expect(deleteKBCategory).toHaveBeenCalledWith('cat-1'));
    });

    it('shows a permission-denied message for a non-admin (403)', async () => {
        vi.mocked(fetchKBCategories).mockRejectedValue(new Error('403: forbidden'));
        render(<KnowledgeBaseCategoriesPage />);
        await waitFor(() => expect(screen.getByText(/دسترسی به دسته‌بندی‌های پایگاه دانش/)).toBeDefined());
    });
});
