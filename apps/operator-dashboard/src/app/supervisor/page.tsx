'use client';
import { useState, useEffect } from 'react';
import { fetchSupervisorSummary } from '@/lib/api';
import { useRouter } from 'next/navigation';
import Link from 'next/link';

interface QueueLoad { queue_id: string; name: string; active_count: number; }
interface TeamLoad { team_id: string; name: string; active_count: number; }
interface AgentLoad {
    user_id: string; display_name: string; status: string;
    active_conversation_count: number; max_capacity: number; at_capacity: boolean;
}
interface Summary {
    workspace_id: string; unassigned_count: number; waiting_count: number; urgent_count: number;
    approaching_sla_count: number; breached_sla_count: number; by_queue: QueueLoad[]; by_team: TeamLoad[];
    by_agent: AgentLoad[]; agents_at_capacity: number; avg_first_response_minutes: number | null; period_hours: number;
}

const STATUS_LABEL: Record<string, string> = { ONLINE: 'آنلاین', AWAY: 'غایب', BUSY: 'مشغول', DO_NOT_DISTURB: 'مزاحم نشوید', OFFLINE: 'آفلاین' };
const STATUS_COLOR: Record<string, string> = {
    ONLINE: 'bg-success-soft text-success', AWAY: 'bg-gold-soft text-terracotta-2',
    BUSY: 'bg-gold-soft text-terracotta-2', DO_NOT_DISTURB: 'bg-red-100 text-red-600', OFFLINE: 'bg-gray-100 text-gray-500',
};

function StatTile({ label, value, tone }: { label: string; value: string | number; tone?: 'default' | 'danger' | 'warning' }) {
    const toneClass = tone === 'danger' ? 'text-red-600' : tone === 'warning' ? 'text-terracotta-2' : 'text-terracotta';
    return (
        <div className="border border-gray-200 rounded-xl p-4 bg-white text-center">
            <div className={`font-extrabold text-2xl ${toneClass}`}>{value}</div>
            <div className="text-xs text-gray-500 mt-1">{label}</div>
        </div>
    );
}

export default function SupervisorDashboardPage() {
    const [summary, setSummary] = useState<Summary | null>(null);
    const [denied, setDenied] = useState(false);
    const [error, setError] = useState('');
    const router = useRouter();

    useEffect(() => {
        if (!localStorage.getItem('token')) { router.push('/login'); return; }
        fetchSupervisorSummary()
            .then(setSummary)
            .catch((e: Error) => {
                if (e.message.startsWith('403')) setDenied(true);
                else setError('بارگذاری خلاصه عملیاتی ناموفق بود');
            });
    }, []);

    if (denied) {
        return (
            <div className="flex h-screen items-center justify-center bg-gray-100" dir="rtl">
                <div className="text-center">
                    <div className="text-lg font-bold text-gray-700">دسترسی محدود شده است</div>
                    <div className="text-sm text-gray-400 mt-1">این بخش فقط برای مدیران فضای کاری یا سرپرستان تیم قابل مشاهده است.</div>
                    <Link href="/" className="inline-block mt-4 text-sm text-terracotta hover:underline">بازگشت به صندوق ورودی</Link>
                </div>
            </div>
        );
    }

    if (error) return <div className="p-6 text-red-500" dir="rtl">{error}</div>;

    return (
        <div className="min-h-screen bg-gray-100 p-4 md:p-6" dir="rtl">
            <div className="flex items-center justify-between mb-4">
                <h1 className="font-bold text-lg text-gray-800">داشبورد سرپرستی</h1>
                <Link href="/" className="text-sm text-terracotta hover:underline">بازگشت به صندوق ورودی</Link>
            </div>

            {!summary ? (
                <div className="text-center text-gray-400 py-10">در حال بارگذاری…</div>
            ) : (
                <div className="flex flex-col gap-5">
                    <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
                        <StatTile label="واگذارنشده" value={summary.unassigned_count} />
                        <StatTile label="در انتظار" value={summary.waiting_count} />
                        <StatTile label="فوری" value={summary.urgent_count} tone="warning" />
                        <StatTile label="نزدیک به موعد SLA" value={summary.approaching_sla_count} tone="warning" />
                        <StatTile label="نقض SLA" value={summary.breached_sla_count} tone="danger" />
                        <StatTile label="میانگین پاسخ اول (دقیقه)" value={summary.avg_first_response_minutes ?? '—'} />
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        <div className="bg-white border border-gray-200 rounded-xl p-4">
                            <div className="text-sm font-bold text-gray-600 mb-3">بار کاری بر اساس صف</div>
                            <div className="flex flex-col gap-2">
                                {summary.by_queue.length === 0 && <div className="text-xs text-gray-400">صفی ثبت نشده</div>}
                                {summary.by_queue.map(q => (
                                    <div key={q.queue_id} className="flex items-center justify-between text-sm">
                                        <span>{q.name}</span>
                                        <span className="font-bold text-terracotta">{q.active_count}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                        <div className="bg-white border border-gray-200 rounded-xl p-4">
                            <div className="text-sm font-bold text-gray-600 mb-3">بار کاری بر اساس تیم</div>
                            <div className="flex flex-col gap-2">
                                {summary.by_team.length === 0 && <div className="text-xs text-gray-400">تیمی ثبت نشده</div>}
                                {summary.by_team.map(t => (
                                    <div key={t.team_id} className="flex items-center justify-between text-sm">
                                        <span>{t.name}</span>
                                        <span className="font-bold text-terracotta">{t.active_count}</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>

                    <div className="bg-white border border-gray-200 rounded-xl p-4">
                        <div className="text-sm font-bold text-gray-600 mb-3">وضعیت کارشناسان ({summary.agents_at_capacity} نفر در ظرفیت کامل)</div>
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm text-right">
                                <thead>
                                    <tr className="text-xs text-gray-400 border-b border-gray-100">
                                        <th className="py-2 font-medium">کارشناس</th>
                                        <th className="py-2 font-medium">وضعیت</th>
                                        <th className="py-2 font-medium">گفتگوهای فعال</th>
                                        <th className="py-2 font-medium">ظرفیت</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {summary.by_agent.map(a => (
                                        <tr key={a.user_id} className={`border-b border-gray-50 ${a.at_capacity ? 'bg-red-50' : ''}`}>
                                            <td className="py-2">{a.display_name}</td>
                                            <td className="py-2"><span className={`text-[10.5px] font-semibold px-2 py-0.5 rounded-full ${STATUS_COLOR[a.status]}`}>{STATUS_LABEL[a.status] || a.status}</span></td>
                                            <td className="py-2">{a.active_conversation_count}</td>
                                            <td className="py-2">{a.max_capacity} {a.at_capacity && <span className="text-red-600 text-xs mr-1">(کامل)</span>}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
