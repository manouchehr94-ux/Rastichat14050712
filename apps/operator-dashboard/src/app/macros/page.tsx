'use client';
import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
    fetchMacros, createMacro, updateMacro, deleteMacro, activateMacro, deactivateMacro,
    duplicateMacro, fetchMacroRegistry, fetchTeams,
} from '@/lib/api';

const VISIBILITY_LABELS: Record<string, string> = { PRIVATE: 'خصوصی', TEAM: 'تیمی', WORKSPACE: 'کل فضای‌کار' };

const ACTION_LABELS: Record<string, string> = {
    SEND_REPLY: 'ارسال پاسخ', SEND_ARTICLE: 'ارسال مقاله پایگاه دانش', ADD_TAG: 'افزودن برچسب', REMOVE_TAG: 'حذف برچسب',
    SET_PRIORITY: 'تنظیم اولویت', SET_STATUS: 'تنظیم وضعیت', ASSIGN_TO_AGENT: 'واگذاری به کارشناس',
    ASSIGN_TO_TEAM: 'واگذاری به تیم', RETURN_TO_QUEUE: 'بازگشت به صف', TRANSFER_TO_TEAM: 'انتقال به تیم',
    CREATE_INTERNAL_NOTE: 'یادداشت داخلی', REQUEST_RATING: 'درخواست امتیاز', CLOSE_CONVERSATION: 'پایان گفتگو',
    REOPEN_CONVERSATION: 'بازگشایی گفتگو',
};

interface RegistryParam { kind: string; required: boolean; choices?: string[]; ref?: string; max_len?: number }
interface Registry { actions: Record<string, { params: Record<string, RegistryParam> }> }
interface MacroAction { type: string; params: Record<string, string> }
interface NamedRef { id: string; name: string }
interface Macro {
    id: string; name: string; description: string; is_active: boolean; visibility: string; owner: string | null;
    team: string | null; category: string; actions: MacroAction[]; execution_count: number; last_executed_at: string | null;
}

export default function MacrosPage() {
    const router = useRouter();
    const [macros, setMacros] = useState<Macro[]>([]);
    const [registry, setRegistry] = useState<Registry | null>(null);
    const [teams, setTeams] = useState<NamedRef[]>([]);
    const [editing, setEditing] = useState<'new' | Macro | null>(null);
    const [denied, setDenied] = useState(false);
    const [toast, setToast] = useState('');

    const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

    const loadAll = useCallback(() => {
        Promise.all([fetchMacroRegistry(), fetchMacros(), fetchTeams()])
            .then(([reg, macroList, teamList]) => { setRegistry(reg); setMacros(macroList); setTeams(teamList); })
            .catch((e: Error) => { if (e.message.startsWith('403')) setDenied(true); });
    }, []);

    useEffect(() => {
        if (!localStorage.getItem('token')) { router.push('/login'); return; }
        loadAll();
    }, [loadAll, router]);

    const handleToggleActive = async (m: Macro) => {
        try { if (m.is_active) await deactivateMacro(m.id); else await activateMacro(m.id); loadAll(); }
        catch { showToast('عملیات ناموفق بود'); }
    };
    const handleDuplicate = async (m: Macro) => {
        try { await duplicateMacro(m.id); showToast('کپی غیرفعال از ماکرو ایجاد شد'); loadAll(); } catch { showToast('کپی‌سازی ناموفق بود'); }
    };
    const handleDelete = async (m: Macro) => {
        if (!confirm(`ماکرو «${m.name}» حذف شود؟`)) return;
        try { await deleteMacro(m.id); loadAll(); } catch { showToast('حذف ناموفق بود'); }
    };

    if (denied) {
        return (
            <div className="max-w-2xl mx-auto mt-16 text-center">
                <p className="text-gray-500">دسترسی به ماکروها برای شما مجاز نیست.</p>
                <Link href="/" className="inline-block mt-4 text-sm text-terracotta hover:underline">بازگشت به صندوق ورودی</Link>
            </div>
        );
    }

    return (
        <div className="max-w-5xl mx-auto p-4 flex flex-col gap-3" dir="rtl">
            <div className="flex items-center justify-between">
                <h1 className="font-bold text-lg text-gray-800">ماکروها</h1>
                <div className="flex items-center gap-3 text-sm">
                    <Link href="/macros/history" className="text-terracotta hover:underline">تاریخچه اجرا</Link>
                    <Link href="/" className="text-terracotta hover:underline">بازگشت به صندوق ورودی</Link>
                </div>
            </div>
            {toast && <div className="text-xs bg-cream text-gray-700 border border-gray-200 rounded-lg px-3 py-2">{toast}</div>}

            {!editing && (
                <>
                    <div><button onClick={() => setEditing('new')} className="text-sm bg-terracotta text-white rounded-lg px-3 py-2">+ ماکرو جدید</button></div>
                    <div className="bg-white border border-gray-200 rounded-xl overflow-x-auto">
                        <table className="w-full text-sm text-right">
                            <thead>
                                <tr className="text-xs text-gray-400 border-b border-gray-100">
                                    <th className="py-2 px-3 font-medium">نام</th>
                                    <th className="py-2 px-3 font-medium">نمایانی</th>
                                    <th className="py-2 px-3 font-medium">تعداد اقدامات</th>
                                    <th className="py-2 px-3 font-medium">اجراها</th>
                                    <th className="py-2 px-3 font-medium">وضعیت</th>
                                    <th className="py-2 px-3 font-medium">عملیات</th>
                                </tr>
                            </thead>
                            <tbody>
                                {macros.length === 0 && <tr><td colSpan={6} className="py-8 text-center text-xs text-gray-400">ماکرویی وجود ندارد.</td></tr>}
                                {macros.map(m => (
                                    <tr key={m.id} className="border-b border-gray-50">
                                        <td className="py-2 px-3">
                                            <button onClick={() => setEditing(m)} className="text-gray-800 hover:text-terracotta font-medium">{m.name}</button>
                                            {m.category && <span className="ms-2 text-[10px] text-gray-400">({m.category})</span>}
                                        </td>
                                        <td className="py-2 px-3 text-xs text-gray-500">{VISIBILITY_LABELS[m.visibility] || m.visibility}</td>
                                        <td className="py-2 px-3 text-xs text-gray-400">{m.actions?.length ?? 0}</td>
                                        <td className="py-2 px-3 text-xs text-gray-400">{m.execution_count}</td>
                                        <td className="py-2 px-3 text-xs">
                                            <button onClick={() => handleToggleActive(m)} className={m.is_active ? 'text-success' : 'text-gray-400'}>
                                                {m.is_active ? 'فعال' : 'غیرفعال'}
                                            </button>
                                        </td>
                                        <td className="py-2 px-3 text-xs">
                                            <div className="flex items-center gap-2">
                                                <button onClick={() => handleDuplicate(m)} className="text-gray-500 hover:underline">کپی</button>
                                                <button onClick={() => handleDelete(m)} className="text-red-500 hover:underline">حذف</button>
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </>
            )}

            {editing && registry && (
                <MacroEditor
                    macro={editing} registry={registry} teams={teams}
                    onCancel={() => setEditing(null)}
                    onSaved={() => { setEditing(null); loadAll(); showToast('ماکرو ذخیره شد'); }}
                />
            )}
        </div>
    );
}

function emptyAction(registry: Registry): MacroAction {
    const type = Object.keys(registry.actions)[0] || 'SEND_REPLY';
    return { type, params: {} };
}

function MacroEditor({ macro, registry, teams, onCancel, onSaved }: {
    macro: 'new' | Macro; registry: Registry; teams: NamedRef[]; onCancel: () => void; onSaved: () => void;
}) {
    const isNew = macro === 'new';
    const [name, setName] = useState(isNew ? '' : macro.name);
    const [description, setDescription] = useState(isNew ? '' : macro.description);
    const [visibility, setVisibility] = useState(isNew ? 'WORKSPACE' : macro.visibility);
    const [team, setTeam] = useState(isNew ? '' : (macro.team || ''));
    const [category, setCategory] = useState(isNew ? '' : macro.category);
    const [actions, setActions] = useState<MacroAction[]>(isNew ? [emptyAction(registry)] : macro.actions);
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);

    const handleSave = async () => {
        setError('');
        setSaving(true);
        const payload: Record<string, unknown> = { name, description, visibility, category, actions };
        if (visibility === 'TEAM') payload.team = team || null;
        try {
            if (isNew) await createMacro(payload);
            else await updateMacro(macro.id, payload);
            onSaved();
        } catch (e) {
            setError((e as Error).message);
        } finally {
            setSaving(false);
        }
    };

    const updateAction = (index: number, next: MacroAction) => {
        setActions(actions.map((a, i) => (i === index ? next : a)));
    };
    const removeAction = (index: number) => setActions(actions.filter((_, i) => i !== index));
    const moveAction = (index: number, dir: -1 | 1) => {
        const target = index + dir;
        if (target < 0 || target >= actions.length) return;
        const next = [...actions];
        [next[index], next[target]] = [next[target], next[index]];
        setActions(next);
    };

    return (
        <div className="bg-white border border-gray-200 rounded-xl p-4 flex flex-col gap-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                <div>
                    <label className="text-xs text-gray-500 block mb-1">نام ماکرو</label>
                    <input value={name} onChange={e => setName(e.target.value)} className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2" />
                </div>
                <div>
                    <label className="text-xs text-gray-500 block mb-1">گروه/دسته</label>
                    <input value={category} onChange={e => setCategory(e.target.value)} className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2" />
                </div>
                <div>
                    <label className="text-xs text-gray-500 block mb-1">نمایانی</label>
                    <select value={visibility} onChange={e => setVisibility(e.target.value)} className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2">
                        {Object.entries(VISIBILITY_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                </div>
                {visibility === 'TEAM' && (
                    <div>
                        <label className="text-xs text-gray-500 block mb-1">تیم</label>
                        <select value={team} onChange={e => setTeam(e.target.value)} className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2">
                            <option value="">انتخاب تیم</option>
                            {teams.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                        </select>
                    </div>
                )}
                <div className="md:col-span-2">
                    <label className="text-xs text-gray-500 block mb-1">توضیح</label>
                    <input value={description} onChange={e => setDescription(e.target.value)} className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2" />
                </div>
            </div>

            <div className="flex flex-col gap-2">
                <div className="text-xs text-gray-500 font-medium">اقدامات (به همین ترتیب اجرا می‌شوند)</div>
                {actions.map((action, i) => (
                    <div key={i} className="border border-gray-200 rounded-lg p-2.5 flex flex-col gap-2 bg-cream/30">
                        <div className="flex items-center gap-2">
                            <span className="text-xs text-gray-400 w-5">{i + 1}.</span>
                            <select value={action.type} onChange={e => updateAction(i, { type: e.target.value, params: {} })} className="text-xs border border-gray-200 rounded px-1.5 py-1 flex-1">
                                {Object.keys(registry.actions).map(t => <option key={t} value={t}>{ACTION_LABELS[t] || t}</option>)}
                            </select>
                            <button onClick={() => moveAction(i, -1)} disabled={i === 0} className="text-xs text-gray-400 disabled:opacity-30" title="بالا">▲</button>
                            <button onClick={() => moveAction(i, 1)} disabled={i === actions.length - 1} className="text-xs text-gray-400 disabled:opacity-30" title="پایین">▼</button>
                            <button onClick={() => removeAction(i)} className="text-xs text-red-500" title="حذف">✕</button>
                        </div>
                        <ActionParamsEditor action={action} spec={registry.actions[action.type]?.params || {}} onChange={p => updateAction(i, { ...action, params: p })} />
                    </div>
                ))}
                <button onClick={() => setActions([...actions, emptyAction(registry)])} className="text-xs text-terracotta hover:underline self-start">+ افزودن اقدام</button>
            </div>

            {error && <div className="text-xs text-red-500 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>}

            <div className="flex items-center gap-2 pt-1">
                <button onClick={handleSave} disabled={saving || !name.trim() || actions.length === 0} className="text-sm bg-terracotta text-white rounded-lg px-4 py-2 disabled:opacity-50">
                    {saving ? 'در حال ذخیره…' : 'ذخیره'}
                </button>
                <button onClick={onCancel} className="text-sm text-gray-500 hover:underline">انصراف</button>
            </div>
        </div>
    );
}

function ActionParamsEditor({ action, spec, onChange }: {
    action: MacroAction; spec: Record<string, RegistryParam>; onChange: (params: Record<string, string>) => void;
}) {
    if (Object.keys(spec).length === 0) return <div className="text-[11px] text-gray-400">این اقدام پارامتری ندارد.</div>;
    return (
        <div className="flex flex-col gap-1.5">
            {Object.entries(spec).map(([paramName, paramSpec]) => (
                <div key={paramName} className="flex items-center gap-2">
                    <label className="text-[11px] text-gray-500 w-28 flex-none">{paramName}{paramSpec.required ? ' *' : ''}</label>
                    {paramSpec.kind === 'choice' ? (
                        <select value={action.params[paramName] || ''} onChange={e => onChange({ ...action.params, [paramName]: e.target.value })}
                            className="text-xs border border-gray-200 rounded px-1.5 py-1 flex-1">
                            <option value="">انتخاب کنید</option>
                            {(paramSpec.choices || []).map(c => <option key={c} value={c}>{c}</option>)}
                        </select>
                    ) : (
                        <input value={action.params[paramName] || ''} onChange={e => onChange({ ...action.params, [paramName]: e.target.value })}
                            placeholder={paramSpec.kind === 'reference' ? `شناسه ${paramSpec.ref}` : ''}
                            className="text-xs border border-gray-200 rounded px-1.5 py-1 flex-1" />
                    )}
                </div>
            ))}
        </div>
    );
}
