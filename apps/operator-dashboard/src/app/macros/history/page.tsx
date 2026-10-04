'use client';
import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { fetchMacroExecutionHistory, retryMacroExecution } from '@/lib/api';

const STATUS_LABELS: Record<string, string> = {
    PENDING: 'در حال اجرا', SUCCEEDED: 'موفق', PARTIALLY_SUCCEEDED: 'نیمه‌موفق', FAILED: 'ناموفق', CANCELLED: 'لغو شده',
};

interface ActionExecution { id: string; action_index: number; action_type: string; status: string; error_summary: string }
interface Execution {
    id: string; macro: string; status: string; started_at: string; completed_at: string | null;
    action_executions: ActionExecution[];
}

export default function MacroHistoryPage() {
    const router = useRouter();
    const [executions, setExecutions] = useState<Execution[]>([]);
    const [statusFilter, setStatusFilter] = useState('');
    const [toast, setToast] = useState('');

    const load = useCallback(() => {
        fetchMacroExecutionHistory({ status: statusFilter || undefined })
            .then(d => setExecutions(d.results ?? d))
            .catch(() => setExecutions([]));
    }, [statusFilter]);

    useEffect(() => {
        if (!localStorage.getItem('token')) { router.push('/login'); return; }
        load();
    }, [load, router]);

    const handleRetry = async (id: string) => {
        try { await retryMacroExecution(id); setToast('تلاش دوباره انجام شد'); load(); }
        catch (e) { setToast((e as Error).message); }
        setTimeout(() => setToast(''), 3000);
    };

    return (
        <div className="max-w-4xl mx-auto p-4 flex flex-col gap-3" dir="rtl">
            <div className="flex items-center justify-between">
                <h1 className="font-bold text-lg text-gray-800">تاریخچه اجرای ماکروها</h1>
                <Link href="/macros" className="text-sm text-terracotta hover:underline">بازگشت به ماکروها</Link>
            </div>
            {toast && <div className="text-xs bg-cream text-gray-700 border border-gray-200 rounded-lg px-3 py-2">{toast}</div>}

            <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} className="text-sm border border-gray-200 rounded-lg px-2 py-2 self-start">
                <option value="">همه وضعیت‌ها</option>
                {Object.entries(STATUS_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>

            <div className="flex flex-col gap-2">
                {executions.length === 0 && <div className="text-xs text-gray-400 text-center py-8">اجرایی ثبت نشده است.</div>}
                {executions.map(exec => (
                    <div key={exec.id} className="bg-white border border-gray-200 rounded-xl p-3">
                        <div className="flex items-center justify-between">
                            <div className="text-sm font-medium text-gray-800">{STATUS_LABELS[exec.status] || exec.status}</div>
                            <div className="flex items-center gap-2">
                                <span className="text-[11px] text-gray-400">{new Date(exec.started_at).toLocaleString('fa-IR')}</span>
                                {(exec.status === 'FAILED' || exec.status === 'PARTIALLY_SUCCEEDED') && (
                                    <button onClick={() => handleRetry(exec.id)} className="text-xs text-terracotta hover:underline">تلاش دوباره</button>
                                )}
                            </div>
                        </div>
                        {exec.status === 'PARTIALLY_SUCCEEDED' || exec.status === 'FAILED' ? (
                            <div className="mt-2 flex flex-col gap-1">
                                {exec.action_executions.map(ae => (
                                    <div key={ae.id} className={`text-[11px] flex items-center gap-1.5 ${ae.status === 'FAILED' ? 'text-red-500' : 'text-gray-400'}`}>
                                        <span>{ae.status === 'SUCCEEDED' ? '✓' : ae.status === 'FAILED' ? '✕' : '—'}</span>
                                        <span>{ae.action_type}</span>
                                        {ae.error_summary && <span className="text-gray-400">({ae.error_summary})</span>}
                                    </div>
                                ))}
                            </div>
                        ) : null}
                    </div>
                ))}
            </div>
        </div>
    );
}
