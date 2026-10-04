import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import MacrosPage from './page';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock('@/lib/api', () => ({
    fetchMacros: vi.fn(),
    createMacro: vi.fn(),
    updateMacro: vi.fn(),
    deleteMacro: vi.fn(),
    activateMacro: vi.fn(),
    deactivateMacro: vi.fn(),
    duplicateMacro: vi.fn(),
    fetchMacroRegistry: vi.fn(),
    fetchTeams: vi.fn(),
}));

import {
    fetchMacros, createMacro, deleteMacro, activateMacro, deactivateMacro, duplicateMacro,
    fetchMacroRegistry, fetchTeams,
} from '@/lib/api';

const registry = {
    actions: {
        SEND_REPLY: { params: { template: { kind: 'string', required: true, max_len: 2000 } } },
        CLOSE_CONVERSATION: { params: {} },
    },
};

const oneMacro = {
    id: 'macro-1', name: 'درخواست مرجوعی', description: '', is_active: false, visibility: 'WORKSPACE',
    owner: null, team: null, category: 'مرجوعی', actions: [{ type: 'SEND_REPLY', params: { template: 'سلام' } }],
    execution_count: 0, last_executed_at: null,
};

function setupDefaults() {
    vi.mocked(fetchMacroRegistry).mockResolvedValue(registry);
    vi.mocked(fetchMacros).mockResolvedValue([oneMacro]);
    vi.mocked(fetchTeams).mockResolvedValue([]);
}

describe('Macros page', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        global.localStorage = { getItem: vi.fn(() => 'token'), setItem: vi.fn(), removeItem: vi.fn() } as unknown as Storage;
    });

    it('renders the macro list with name and inactive status', async () => {
        setupDefaults();
        render(<MacrosPage />);
        await waitFor(() => expect(screen.getByText('درخواست مرجوعی')).toBeDefined());
        expect(screen.getByText('غیرفعال')).toBeDefined();
    });

    it('shows a permission-denied message on 403', async () => {
        vi.mocked(fetchMacroRegistry).mockRejectedValue(new Error('403: forbidden'));
        vi.mocked(fetchMacros).mockResolvedValue([]);
        vi.mocked(fetchTeams).mockResolvedValue([]);
        render(<MacrosPage />);
        await waitFor(() => expect(screen.getByText(/دسترسی به ماکروها/)).toBeDefined());
    });

    it('activates an inactive macro', async () => {
        setupDefaults();
        vi.mocked(activateMacro).mockResolvedValue({ ...oneMacro, is_active: true });
        render(<MacrosPage />);
        await waitFor(() => expect(screen.getByText('غیرفعال')).toBeDefined());
        fireEvent.click(screen.getByText('غیرفعال'));
        await waitFor(() => expect(activateMacro).toHaveBeenCalledWith('macro-1'));
    });

    it('deactivates an active macro', async () => {
        setupDefaults();
        vi.mocked(fetchMacros).mockResolvedValue([{ ...oneMacro, is_active: true }]);
        vi.mocked(deactivateMacro).mockResolvedValue({ ...oneMacro, is_active: false });
        render(<MacrosPage />);
        await waitFor(() => expect(screen.getByText('فعال')).toBeDefined());
        fireEvent.click(screen.getByText('فعال'));
        await waitFor(() => expect(deactivateMacro).toHaveBeenCalledWith('macro-1'));
    });

    it('duplicates a macro as an inactive copy', async () => {
        setupDefaults();
        vi.mocked(duplicateMacro).mockResolvedValue({ ...oneMacro, id: 'macro-2', is_active: false });
        render(<MacrosPage />);
        await waitFor(() => expect(screen.getByText('کپی')).toBeDefined());
        fireEvent.click(screen.getByText('کپی'));
        await waitFor(() => expect(duplicateMacro).toHaveBeenCalledWith('macro-1'));
    });

    it('deletes a macro after confirmation', async () => {
        setupDefaults();
        vi.mocked(deleteMacro).mockResolvedValue(undefined);
        vi.stubGlobal('confirm', vi.fn(() => true));
        render(<MacrosPage />);
        await waitFor(() => expect(screen.getByText('حذف')).toBeDefined());
        fireEvent.click(screen.getByText('حذف'));
        await waitFor(() => expect(deleteMacro).toHaveBeenCalledWith('macro-1'));
    });

    it('creates a new macro with an action via the builder', async () => {
        setupDefaults();
        vi.mocked(createMacro).mockResolvedValue({ ...oneMacro, id: 'macro-3', name: 'ماکرو جدید' });
        render(<MacrosPage />);
        await waitFor(() => expect(screen.getByText('درخواست مرجوعی')).toBeDefined());

        fireEvent.click(screen.getByText('+ ماکرو جدید'));
        const nameInput = document.querySelectorAll('input')[0] as HTMLInputElement;
        fireEvent.change(nameInput, { target: { value: 'ماکرو جدید' } });
        fireEvent.click(screen.getByText('ذخیره'));

        await waitFor(() => expect(createMacro).toHaveBeenCalled());
        const payload = vi.mocked(createMacro).mock.calls[0][0] as Record<string, unknown>;
        expect(payload.name).toBe('ماکرو جدید');
        expect((payload.actions as unknown[]).length).toBeGreaterThan(0);
    });

    it('validation errors from the server are shown in the editor', async () => {
        setupDefaults();
        vi.mocked(createMacro).mockRejectedValue(new Error('{"actions":["Too many actions"]}'));
        render(<MacrosPage />);
        await waitFor(() => expect(screen.getByText('درخواست مرجوعی')).toBeDefined());
        fireEvent.click(screen.getByText('+ ماکرو جدید'));
        const nameInput = document.querySelectorAll('input')[0] as HTMLInputElement;
        fireEvent.change(nameInput, { target: { value: 'نامعتبر' } });
        fireEvent.click(screen.getByText('ذخیره'));
        await waitFor(() => expect(screen.getByText(/Too many actions/)).toBeDefined());
    });
});
