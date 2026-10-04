import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import SupervisorDashboardPage from './page';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock('@/lib/api', () => ({
  fetchSupervisorSummary: vi.fn(),
}));

import { fetchSupervisorSummary } from '@/lib/api';

const summary = {
  workspace_id: 'ws1', unassigned_count: 3, waiting_count: 3, urgent_count: 7,
  approaching_sla_count: 1, breached_sla_count: 1, avg_first_response_minutes: 4.5, period_hours: 24,
  agents_at_capacity: 1,
  by_queue: [{ queue_id: 'q1', name: 'صف فروش', active_count: 5 }],
  by_team: [{ team_id: 't1', name: 'فروش', active_count: 5 }],
  by_agent: [
    { user_id: 'u1', display_name: 'سارا', status: 'ONLINE', active_conversation_count: 10, max_capacity: 10, at_capacity: true },
    { user_id: 'u2', display_name: 'علی', status: 'AWAY', active_conversation_count: 2, max_capacity: 10, at_capacity: false },
  ],
};

describe('Supervisor operational dashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.localStorage = { getItem: vi.fn(() => 'token'), setItem: vi.fn(), removeItem: vi.fn() } as unknown as Storage;
  });

  it('renders the operational summary tiles and tables', async () => {
    vi.mocked(fetchSupervisorSummary).mockResolvedValue(summary);
    render(<SupervisorDashboardPage />);
    await waitFor(() => expect(screen.getByText('7')).toBeDefined()); // urgent_count tile
    expect(screen.getByText('صف فروش')).toBeDefined();
    expect(screen.getByText('فروش')).toBeDefined();
    expect(screen.getByText('سارا')).toBeDefined();
    expect(screen.getByText('علی')).toBeDefined();
    expect(screen.getByText('(کامل)')).toBeDefined();
  });

  it('shows a permission-denied message for a regular operator (403)', async () => {
    vi.mocked(fetchSupervisorSummary).mockRejectedValue(new Error('403: Not authorized to view the supervisor summary'));
    render(<SupervisorDashboardPage />);
    await waitFor(() => expect(screen.getByText('دسترسی محدود شده است')).toBeDefined());
  });

  it('shows a generic error for a non-403 failure without claiming access is denied', async () => {
    vi.mocked(fetchSupervisorSummary).mockRejectedValue(new Error('Failed to fetch supervisor summary'));
    render(<SupervisorDashboardPage />);
    await waitFor(() => expect(screen.getByText('بارگذاری خلاصه عملیاتی ناموفق بود')).toBeDefined());
    expect(screen.queryByText('دسترسی محدود شده است')).toBeNull();
  });
});
