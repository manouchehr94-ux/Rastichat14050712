import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import KnowledgeBasePage from './page';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock('@/lib/api', () => ({
    fetchKBArticles: vi.fn(),
    fetchKBCategories: vi.fn(),
    createKBArticle: vi.fn(),
    updateKBArticle: vi.fn(),
    publishKBArticle: vi.fn(),
    archiveKBArticle: vi.fn(),
    duplicateKBArticle: vi.fn(),
    fetchKBArticleRevisions: vi.fn(),
    restoreKBArticleRevision: vi.fn(),
    fetchKBFeedbackSummary: vi.fn(),
}));

import {
    fetchKBArticles, fetchKBCategories, createKBArticle, publishKBArticle, archiveKBArticle,
    duplicateKBArticle, fetchKBArticleRevisions, restoreKBArticleRevision, fetchKBFeedbackSummary,
} from '@/lib/api';

const oneArticle = {
    id: 'art-1', title: 'راهنمای مرجوعی', slug: 'refund-guide', excerpt: 'خلاصه', body: 'متن مقاله',
    rendered_body: '<p>متن مقاله</p>', status: 'DRAFT', visibility: 'INTERNAL', language: 'fa', tags: [],
    is_featured: false, category: null, current_revision_number: 1, view_count: 0,
    feedback_summary: { helpful: 0, not_helpful: 0, total: 0 },
};

function setupDefaults() {
    vi.mocked(fetchKBArticles).mockResolvedValue([oneArticle]);
    vi.mocked(fetchKBCategories).mockResolvedValue([]);
    vi.mocked(fetchKBFeedbackSummary).mockResolvedValue({ helpful: 0, not_helpful: 0, total: 0 });
}

describe('Knowledge Base page', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        global.localStorage = { getItem: vi.fn(() => 'token'), setItem: vi.fn(), removeItem: vi.fn() } as unknown as Storage;
    });

    it('renders the article list with title and status', async () => {
        setupDefaults();
        render(<KnowledgeBasePage />);
        await waitFor(() => expect(screen.getByText('راهنمای مرجوعی')).toBeDefined());
        // "پیش‌نویس" also appears as a <option> in the status filter dropdown,
        // so assert on the table cell specifically rather than by bare text.
        const row = screen.getByText('راهنمای مرجوعی').closest('tr');
        expect(row).not.toBeNull();
        expect(row!.textContent).toContain('پیش‌نویس');
    });

    it('shows a permission-denied message for a non-member (403)', async () => {
        vi.mocked(fetchKBArticles).mockRejectedValue(new Error('403: forbidden'));
        vi.mocked(fetchKBCategories).mockResolvedValue([]);
        render(<KnowledgeBasePage />);
        await waitFor(() => expect(screen.getByText(/دسترسی به پایگاه دانش/)).toBeDefined());
    });

    it('creates a new article via the editor form', async () => {
        setupDefaults();
        vi.mocked(createKBArticle).mockResolvedValue({ ...oneArticle, id: 'art-2', title: 'مقاله جدید' });
        render(<KnowledgeBasePage />);
        await waitFor(() => expect(screen.getByText('راهنمای مرجوعی')).toBeDefined());

        fireEvent.click(screen.getByText('+ مقاله جدید'));
        // The title field is the first input rendered in the editor form.
        const titleField = document.querySelectorAll('input')[0] as HTMLInputElement;
        fireEvent.change(titleField, { target: { value: 'مقاله جدید' } });
        fireEvent.click(screen.getByText('ذخیره'));

        await waitFor(() => expect(createKBArticle).toHaveBeenCalled());
        const payload = vi.mocked(createKBArticle).mock.calls[0][0] as Record<string, unknown>;
        expect(payload.title).toBe('مقاله جدید');
    });

    it('publishes an article', async () => {
        setupDefaults();
        vi.mocked(publishKBArticle).mockResolvedValue({ ...oneArticle, status: 'PUBLISHED' });
        render(<KnowledgeBasePage />);
        await waitFor(() => expect(screen.getByText('انتشار')).toBeDefined());
        fireEvent.click(screen.getByText('انتشار'));
        await waitFor(() => expect(publishKBArticle).toHaveBeenCalledWith('art-1'));
    });

    it('archives an article', async () => {
        setupDefaults();
        vi.mocked(archiveKBArticle).mockResolvedValue({ ...oneArticle, status: 'ARCHIVED' });
        render(<KnowledgeBasePage />);
        await waitFor(() => expect(screen.getByText('بایگانی')).toBeDefined());
        fireEvent.click(screen.getByText('بایگانی'));
        await waitFor(() => expect(archiveKBArticle).toHaveBeenCalledWith('art-1'));
    });

    it('duplicates an article', async () => {
        setupDefaults();
        vi.mocked(duplicateKBArticle).mockResolvedValue({ ...oneArticle, id: 'art-3' });
        render(<KnowledgeBasePage />);
        await waitFor(() => expect(screen.getByText('کپی')).toBeDefined());
        fireEvent.click(screen.getByText('کپی'));
        await waitFor(() => expect(duplicateKBArticle).toHaveBeenCalledWith('art-1'));
    });

    it('shows revision history and restores a past revision', async () => {
        // current_revision_number must be 2 here (not the fixture default of
        // 1) so revision 1 is recognized as a PAST revision with a restore
        // button — restoring the article's own current revision is a no-op
        // the UI correctly doesn't offer.
        vi.mocked(fetchKBArticles).mockResolvedValue([{ ...oneArticle, current_revision_number: 2 }]);
        vi.mocked(fetchKBCategories).mockResolvedValue([]);
        vi.mocked(fetchKBFeedbackSummary).mockResolvedValue({ helpful: 0, not_helpful: 0, total: 0 });
        vi.mocked(fetchKBArticleRevisions).mockResolvedValue([
            { id: 'rev-2', revision_number: 2, title: 'راهنمای مرجوعی', excerpt: '', body: '', change_summary: '', created_at: '2026-01-02T00:00:00Z' },
            { id: 'rev-1', revision_number: 1, title: 'نسخه اول', excerpt: '', body: '', change_summary: 'نسخه اولیه', created_at: '2026-01-01T00:00:00Z' },
        ]);
        vi.mocked(restoreKBArticleRevision).mockResolvedValue({ ...oneArticle, title: 'نسخه اول' });
        vi.stubGlobal('confirm', vi.fn(() => true));
        render(<KnowledgeBasePage />);
        await waitFor(() => expect(screen.getByText('تاریخچه')).toBeDefined());
        fireEvent.click(screen.getByText('تاریخچه'));
        await waitFor(() => expect(screen.getByText(/نسخه 1 —/)).toBeDefined());
        fireEvent.click(screen.getByText('بازگردانی این نسخه'));
        await waitFor(() => expect(restoreKBArticleRevision).toHaveBeenCalledWith('art-1', 1));
    });
});
