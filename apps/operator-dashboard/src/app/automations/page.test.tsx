import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import AutomationsPage from './page';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock('@/lib/api', () => ({
    fetchAutomationRules: vi.fn(),
    createAutomationRule: vi.fn(),
    updateAutomationRule: vi.fn(),
    deleteAutomationRule: vi.fn(),
    activateAutomationRule: vi.fn(),
    deactivateAutomationRule: vi.fn(),
    duplicateAutomationRule: vi.fn(),
    simulateAutomationDraft: vi.fn(),
    fetchAutomationRegistry: vi.fn(),
    fetchAutomationExecutionHistory: vi.fn(),
    fetchScheduledActions: vi.fn(),
    cancelScheduledAction: vi.fn(),
    fetchTeams: vi.fn(),
    fetchQueues: vi.fn(),
    fetchTags: vi.fn(),
}));

import {
    fetchAutomationRules, createAutomationRule, deleteAutomationRule,
    deactivateAutomationRule, duplicateAutomationRule, simulateAutomationDraft, fetchAutomationRegistry,
    fetchAutomationExecutionHistory, fetchScheduledActions, cancelScheduledAction, fetchTeams, fetchQueues, fetchTags,
} from '@/lib/api';

const registry = {
    triggers: [
        { value: 'CONVERSATION_CREATED', label: 'Conversation created' },
        { value: 'CONVERSATION_CLOSED', label: 'Conversation closed' },
    ],
    condition_fields: {
        'conversation.priority': { type: 'string', operators: ['equals', 'not_equals', 'exists', 'not_exists'] },
        'conversation.rating': { type: 'number', operators: ['equals', 'greater_than', 'exists', 'not_exists'] },
    },
    valueless_operators: ['exists', 'not_exists', 'is_true', 'is_false'],
    actions: {
        SET_PRIORITY: { params: { priority: { kind: 'choice', required: true, choices: ['LOW', 'NORMAL', 'HIGH', 'URGENT'] } } },
        ESCALATE: { params: {} },
    },
};

const oneRule = {
    id: 'rule-1', name: 'قانون تست', description: '', is_active: true, trigger_type: 'CONVERSATION_CREATED',
    conditions: {}, actions: [{ type: 'ESCALATE', params: {} }], priority: 100, stop_processing: false,
    created_by_name: 'مدیر', updated_by_name: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    last_executed_at: null, execution_count: 0, valid_from: null, valid_until: null,
};

function setupDefaults() {
    vi.mocked(fetchAutomationRegistry).mockResolvedValue(registry);
    vi.mocked(fetchAutomationRules).mockResolvedValue([oneRule]);
    vi.mocked(fetchTeams).mockResolvedValue([]);
    vi.mocked(fetchQueues).mockResolvedValue([]);
    vi.mocked(fetchTags).mockResolvedValue([]);
}

describe('Automations page', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        global.localStorage = { getItem: vi.fn(() => 'token'), setItem: vi.fn(), removeItem: vi.fn() } as unknown as Storage;
    });

    it('renders the rule list with name, trigger, and active status', async () => {
        setupDefaults();
        render(<AutomationsPage />);
        await waitFor(() => expect(screen.getByText('قانون تست')).toBeDefined());
        expect(screen.getByText('ایجاد گفتگو')).toBeDefined();
        expect(screen.getByText('فعال')).toBeDefined();
    });

    it('shows a permission-denied message for a non-admin (403)', async () => {
        vi.mocked(fetchAutomationRegistry).mockRejectedValue(new Error('403: forbidden'));
        vi.mocked(fetchAutomationRules).mockResolvedValue([]);
        vi.mocked(fetchTeams).mockResolvedValue([]);
        vi.mocked(fetchQueues).mockResolvedValue([]);
        vi.mocked(fetchTags).mockResolvedValue([]);
        render(<AutomationsPage />);
        await waitFor(() => expect(screen.getByText('دسترسی محدود شده است')).toBeDefined());
    });

    it('creates a new rule with a condition and an action', async () => {
        setupDefaults();
        vi.mocked(createAutomationRule).mockResolvedValue({ ...oneRule, id: 'rule-2', name: 'قانون جدید' });
        render(<AutomationsPage />);
        await waitFor(() => expect(screen.getByText('قانون تست')).toBeDefined());

        fireEvent.click(screen.getByText('+ قانون جدید'));
        const nameInput = screen.getByLabelText('نام قانون');
        fireEvent.change(nameInput, { target: { value: 'قانون جدید' } });

        fireEvent.click(screen.getByText('+ شرط'));
        fireEvent.click(screen.getByText('+ افزودن اقدام'));

        fireEvent.click(screen.getByText('ایجاد قانون'));
        await waitFor(() => expect(createAutomationRule).toHaveBeenCalled());
        const payload = vi.mocked(createAutomationRule).mock.calls[0][0] as Record<string, unknown>;
        expect(payload.name).toBe('قانون جدید');
        expect((payload.actions as unknown[]).length).toBe(1);
    });

    it('toggles a rule between active and inactive', async () => {
        setupDefaults();
        vi.mocked(deactivateAutomationRule).mockResolvedValue({ ...oneRule, is_active: false });
        render(<AutomationsPage />);
        await waitFor(() => expect(screen.getByText('غیرفعال‌سازی')).toBeDefined());
        fireEvent.click(screen.getByText('غیرفعال‌سازی'));
        await waitFor(() => expect(deactivateAutomationRule).toHaveBeenCalledWith('rule-1'));
    });

    it('duplicates a rule as an inactive copy', async () => {
        setupDefaults();
        vi.mocked(duplicateAutomationRule).mockResolvedValue({ ...oneRule, id: 'rule-3', is_active: false });
        render(<AutomationsPage />);
        await waitFor(() => expect(screen.getByText('کپی')).toBeDefined());
        fireEvent.click(screen.getByText('کپی'));
        await waitFor(() => expect(duplicateAutomationRule).toHaveBeenCalledWith('rule-1'));
    });

    it('deletes a rule after confirmation', async () => {
        setupDefaults();
        vi.mocked(deleteAutomationRule).mockResolvedValue(undefined);
        vi.stubGlobal('confirm', vi.fn(() => true));
        render(<AutomationsPage />);
        await waitFor(() => expect(screen.getByText('حذف')).toBeDefined());
        fireEvent.click(screen.getByText('حذف'));
        await waitFor(() => expect(deleteAutomationRule).toHaveBeenCalledWith('rule-1'));
    });

    it('starter templates populate the builder without auto-activating', async () => {
        setupDefaults();
        render(<AutomationsPage />);
        await waitFor(() => expect(screen.getByText('خوش‌آمدگویی خودکار')).toBeDefined());
        fireEvent.click(screen.getByText('خوش‌آمدگویی خودکار'));
        const nameInput = await screen.findByDisplayValue('خوش‌آمدگویی خودکار');
        expect(nameInput).toBeDefined();
        // The template only ever pre-fills the form — creating the rule is
        // still a separate, explicit action the admin must take.
        expect(createAutomationRule).not.toHaveBeenCalled();
    });

    it('simulate runs a dry-run and shows the match result without saving', async () => {
        setupDefaults();
        vi.mocked(simulateAutomationDraft).mockResolvedValue({
            matched: true, condition_trace: { type: 'empty', result: true },
            would_run_actions: [{ index: 0, type: 'ESCALATE', params: {} }],
        });
        render(<AutomationsPage />);
        await waitFor(() => expect(screen.getByText('قانون تست')).toBeDefined());
        fireEvent.click(screen.getByText('ویرایش'));
        fireEvent.click(await screen.findByText('اجرای شبیه‌سازی'));
        await waitFor(() => expect(screen.getByText('شرط‌ها منطبق شدند ✓')).toBeDefined());
        expect(createAutomationRule).not.toHaveBeenCalled();
    });

    it('shows execution history and expands action details', async () => {
        setupDefaults();
        vi.mocked(fetchAutomationExecutionHistory).mockResolvedValue({
            results: [{
                id: 1, rule: 'rule-1', rule_name: 'قانون تست', trigger_type: 'CONVERSATION_CREATED', conversation: null,
                status: 'SUCCEEDED', error_summary: '', created_at: '2026-01-01T00:00:00Z',
                action_executions: [{ id: 1, action_index: 0, action_type: 'ESCALATE', status: 'SUCCEEDED', result_summary: {}, affected_object_type: '', affected_object_id: '', error_summary: '', created_at: '2026-01-01T00:00:00Z' }],
            }],
        });
        render(<AutomationsPage />);
        await waitFor(() => expect(screen.getByText('قوانین')).toBeDefined());
        fireEvent.click(screen.getByText('تاریخچه اجرا'));
        await waitFor(() => expect(screen.getAllByText('قانون تست').length).toBeGreaterThan(0));
        fireEvent.click(screen.getByText('جزئیات'));
        await waitFor(() => expect(screen.getAllByText('تشدید').length).toBeGreaterThan(0));
    });

    it('shows scheduled actions and can cancel a pending one', async () => {
        setupDefaults();
        vi.mocked(fetchScheduledActions).mockResolvedValue({
            results: [{
                id: 'sched-1', rule: 'rule-1', rule_name: 'قانون تست', conversation: 'conv-1',
                action_definition: { type: 'ESCALATE', params: {} }, execute_at: '2026-01-01T00:10:00Z',
                status: 'PENDING', attempts: 0, max_attempts: 3, last_error: '', created_at: '2026-01-01T00:00:00Z',
            }],
        });
        vi.mocked(cancelScheduledAction).mockResolvedValue({});
        render(<AutomationsPage />);
        await waitFor(() => expect(screen.getByText('قوانین')).toBeDefined());
        fireEvent.click(screen.getByText('اقدامات زمان‌بندی‌شده'));
        await waitFor(() => expect(screen.getByText('در انتظار')).toBeDefined());
        fireEvent.click(screen.getByText('لغو'));
        await waitFor(() => expect(cancelScheduledAction).toHaveBeenCalledWith('sched-1'));
    });
});
