import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import MacroHistoryPage from './page';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock('@/lib/api', () => ({
    fetchMacroExecutionHistory: vi.fn(),
    retryMacroExecution: vi.fn(),
}));

import { fetchMacroExecutionHistory, retryMacroExecution } from '@/lib/api';

const partialExecution = {
    id: 'exec-1', macro: 'macro-1', status: 'PARTIALLY_SUCCEEDED', started_at: '2026-01-01T00:00:00Z', completed_at: '2026-01-01T00:01:00Z',
    action_executions: [
        { id: 'ae-1', action_index: 0, action_type: 'SEND_REPLY', status: 'SUCCEEDED', error_summary: '' },
        { id: 'ae-2', action_index: 1, action_type: 'ADD_TAG', status: 'FAILED', error_summary: 'Tag not found in this workspace' },
    ],
};

describe('Macro execution history page', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        global.localStorage = { getItem: vi.fn(() => 'token'), setItem: vi.fn(), removeItem: vi.fn() } as unknown as Storage;
    });

    it('renders execution status and partial-failure per-action detail', async () => {
        vi.mocked(fetchMacroExecutionHistory).mockResolvedValue({ results: [partialExecution] });
        render(<MacroHistoryPage />);
        await waitFor(() => expect(screen.getByText('نیمه‌موفق')).toBeDefined());
        expect(screen.getByText('SEND_REPLY')).toBeDefined();
        expect(screen.getByText('ADD_TAG')).toBeDefined();
        expect(screen.getByText(/Tag not found in this workspace/)).toBeDefined();
    });

    it('retries a partially-succeeded execution', async () => {
        vi.mocked(fetchMacroExecutionHistory).mockResolvedValue({ results: [partialExecution] });
        vi.mocked(retryMacroExecution).mockResolvedValue({ ...partialExecution, status: 'SUCCEEDED' });
        render(<MacroHistoryPage />);
        await waitFor(() => expect(screen.getByText('تلاش دوباره')).toBeDefined());
        fireEvent.click(screen.getByText('تلاش دوباره'));
        await waitFor(() => expect(retryMacroExecution).toHaveBeenCalledWith('exec-1'));
    });

    it('shows an empty state when there is no execution history', async () => {
        vi.mocked(fetchMacroExecutionHistory).mockResolvedValue({ results: [] });
        render(<MacroHistoryPage />);
        await waitFor(() => expect(screen.getByText('اجرایی ثبت نشده است.')).toBeDefined());
    });
});
