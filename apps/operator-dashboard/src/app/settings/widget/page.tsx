'use client';
import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
    fetchAdminProjects, fetchWidgetConfig, saveWidgetConfig, WidgetConfigError, widgetApiBase, type AdminProject,
} from '@/lib/api';
import {
    CHOICE_TYPES, FIELD_TYPES, docFromForm, embedSnippet, emptyField, formFromEffective, suggestKey,
    type FieldForm, type FieldType, type PreChatMode, type WidgetForm,
} from '@/lib/widgetConfig';

const inputCls = 'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white';
const ICON_OPTIONS = [['chat', '💬 گفتگو'], ['help', '❓ راهنما'], ['headset', '🎧 پشتیبانی'], ['mail', '✉️ پیام'], ['sparkle', '✨ ستاره']];

function Field({ label, error, children, hint }: { label: string; error?: string; children: React.ReactNode; hint?: string }) {
    return (
        <label className="block mb-3">
            <span className="block text-xs font-semibold text-gray-600 mb-1">{label}</span>
            {children}
            {hint && <span className="block text-[11px] text-gray-400 mt-1">{hint}</span>}
            {error && <span role="alert" className="block text-[11.5px] text-red-600 mt-1">{error}</span>}
        </label>
    );
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
    return (
        <label className="flex items-center gap-2 text-sm mb-2">
            <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} /> {label}
        </label>
    );
}

export default function WidgetSettingsPage() {
    const router = useRouter();
    const [projects, setProjects] = useState<AdminProject[]>([]);
    const [projectId, setProjectId] = useState<number | null>(null);
    const [form, setForm] = useState<WidgetForm | null>(null);
    const [errors, setErrors] = useState<Record<string, string>>({});
    const [status, setStatus] = useState<'idle' | 'loading' | 'saving' | 'saved' | 'error'>('loading');
    const [message, setMessage] = useState('');

    useEffect(() => {
        if (!localStorage.getItem('token')) { router.push('/login'); return; }
        fetchAdminProjects()
            .then(list => { setProjects(list); setProjectId(list[0]?.id ?? null); if (!list.length) setStatus('idle'); })
            .catch(() => { setStatus('error'); setMessage('دریافت پروژه‌ها ناموفق بود. این صفحه فقط برای مدیران فروشگاه است.'); });
    }, [router]);

    useEffect(() => {
        if (projectId === null) return;
        fetchWidgetConfig(projectId)
            .then(r => { setForm(formFromEffective(r.effective)); setErrors({}); setStatus('idle'); })
            .catch(() => { setStatus('error'); setMessage('دریافت تنظیمات ناموفق بود.'); });
    }, [projectId]);

    const project = projects.find(p => p.id === projectId) ?? null;
    const set = <K extends keyof WidgetForm>(key: K, value: WidgetForm[K]) => setForm(f => (f ? { ...f, [key]: value } : f));
    const setField = (i: number, patch: Partial<FieldForm>) => setForm(f => (f ? { ...f, fields: f.fields.map((x, j) => (j === i ? { ...x, ...patch } : x)) } : f));
    const addField = () => setForm(f => (f ? { ...f, fields: [...f.fields, { ...emptyField(), key: suggestKey('q', f.fields.map(x => x.key)) }] } : f));
    const removeField = (i: number) => setForm(f => (f ? { ...f, fields: f.fields.filter((_, j) => j !== i) } : f));
    const move = (i: number, d: -1 | 1) => setForm(f => {
        if (!f || i + d < 0 || i + d >= f.fields.length) return f;
        const fields = [...f.fields];
        [fields[i], fields[i + d]] = [fields[i + d], fields[i]];
        return { ...f, fields };
    });
    const setMode = (mode: PreChatMode) => setForm(f => {
        if (!f) return f;
        const fields = mode !== 'none' && f.fields.length === 0 ? [{ ...emptyField(), key: 'help', label: 'چه کمکی از ما برمی‌آید؟', required: true }] : f.fields;
        return { ...f, preChatMode: mode, fields };
    });

    const snippet = useMemo(
        () => (project ? embedSnippet(project.public_key, widgetApiBase(), `${widgetApiBase().replace(/\/api\/v1$/, '')}/widget.js`) : ''),
        [project],
    );

    const save = async () => {
        if (!form || projectId === null) return;
        setStatus('saving'); setErrors({}); setMessage('');
        try {
            const r = await saveWidgetConfig(projectId, docFromForm(form));
            setForm(formFromEffective(r.effective));
            setStatus('saved'); setMessage('تنظیمات ذخیره شد.');
        } catch (e) {
            setStatus('error');
            if (e instanceof WidgetConfigError) { setErrors(e.errors); setMessage('لطفاً خطاهای مشخص‌شده را اصلاح کنید.'); }
            else setMessage('ذخیره‌سازی ناموفق بود. دوباره تلاش کنید.');
        }
    };

    const err = (path: string) => errors[path];
    const fieldErr = (i: number, sub: string) => err(`pre_chat.fields[${i}].${sub}`);

    return (
        <div className="min-h-[100dvh] bg-gray-100 px-3 sm:px-6 py-4" dir="rtl">
            <div className="max-w-3xl mx-auto">
                <div className="flex items-center justify-between mb-4">
                    <h1 className="text-lg font-bold">تنظیمات ویجت گفتگو</h1>
                    <Link href="/" className="text-sm text-terracotta">← بازگشت به صندوق</Link>
                </div>

                {status === 'loading' && <p role="status">در حال بارگذاری…</p>}
                {status === 'error' && !form && <p role="alert" className="text-red-600">{message}</p>}
                {status === 'idle' && !projects.length && <p>پروژه‌ای برای مدیریت وجود ندارد.</p>}

                {projects.length > 1 && (
                    <Field label="پروژه">
                        <select className={inputCls} value={projectId ?? ''} onChange={e => setProjectId(Number(e.target.value))}>
                            {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                        </select>
                    </Field>
                )}

                {form && (
                    <form onSubmit={e => { e.preventDefault(); void save(); }} className="flex flex-col gap-4">
                        <section className="bg-white rounded-xl p-4 border border-gray-200" aria-labelledby="sec-launcher">
                            <h2 id="sec-launcher" className="font-bold mb-3">دکمهٔ گفتگو</h2>
                            <Check label="نمایش دکمهٔ گفتگو" checked={form.launcherEnabled} onChange={v => set('launcherEnabled', v)} />
                            <div className="grid sm:grid-cols-2 gap-x-4">
                                <Field label="حالت نمایش" error={err('launcher.mode')}>
                                    <select className={inputCls} value={form.launcherMode} onChange={e => set('launcherMode', e.target.value as WidgetForm['launcherMode'])}>
                                        <option value="icon">فقط آیکون</option><option value="icon_text">آیکون و متن</option>
                                    </select>
                                </Field>
                                <Field label="آیکون" error={err('launcher.icon')}>
                                    <select className={inputCls} value={form.icon} onChange={e => set('icon', e.target.value)}>
                                        {ICON_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                                    </select>
                                </Field>
                                <Field label="متن دکمه" error={err('launcher.label')}><input className={inputCls} value={form.label} maxLength={40} onChange={e => set('label', e.target.value)} /></Field>
                                <Field label="راهنمای دکمه (tooltip)" error={err('launcher.tooltip')}><input className={inputCls} value={form.tooltip} maxLength={120} onChange={e => set('tooltip', e.target.value)} /></Field>
                                <Field label="موقعیت" error={err('launcher.position')}>
                                    <select className={inputCls} value={form.position} onChange={e => set('position', e.target.value as WidgetForm['position'])}>
                                        <option value="bottom-right">پایین راست</option><option value="bottom-left">پایین چپ</option>
                                    </select>
                                </Field>
                                <Field label="رنگ" error={err('launcher.color')}><input type="color" className="h-10 w-full border border-gray-300 rounded-lg" value={form.color.toLowerCase()} onChange={e => set('color', e.target.value)} /></Field>
                                <Field label="فاصله از لبه (px)" error={err('launcher.offset.x')}><input inputMode="numeric" className={inputCls} value={form.offsetX} onChange={e => set('offsetX', e.target.value)} /></Field>
                                <Field label="فاصله از پایین (px)" error={err('launcher.offset.y')}><input inputMode="numeric" className={inputCls} value={form.offsetY} onChange={e => set('offsetY', e.target.value)} /></Field>
                            </div>
                            <Field label="پیام خوشامد" error={err('launcher.greeting')} hint="یک حباب کوچک بالای دکمه؛ فقط یک بار در هر نشست مرورگر نمایش داده می‌شود."><input className={inputCls} value={form.greeting} maxLength={200} onChange={e => set('greeting', e.target.value)} /></Field>
                            <Check label="باز شدن خودکار (یک بار در هر نشست؛ در موبایل تمام‌صفحه اعمال نمی‌شود)" checked={form.autoOpen} onChange={v => set('autoOpen', v)} />
                            <Check label="در موبایل تمام‌صفحه باز شود" checked={form.mobileFullscreen} onChange={v => set('mobileFullscreen', v)} />
                            <div className="grid sm:grid-cols-3 gap-x-4">
                                <Field label="زمان ساخت گفتگو" hint="«با اولین پیام» از ساخت گفتگوی خالی در صندوق جلوگیری می‌کند." error={err('behavior.start_mode')}>
                                    <select className={inputCls} value={form.startMode} onChange={e => set('startMode', e.target.value as WidgetForm['startMode'])}>
                                        <option value="on_first_message">با اولین پیام (پیشنهادی)</option><option value="on_open">با باز شدن پنل</option><option value="on_load">با بارگذاری صفحه (قدیمی)</option>
                                    </select>
                                </Field>
                                <Field label="زبان" error={err('locale')}>
                                    <select className={inputCls} value={form.locale} onChange={e => set('locale', e.target.value as WidgetForm['locale'])}><option value="fa">فارسی</option><option value="en">English</option></select>
                                </Field>
                                <Field label="جهت" error={err('direction')}>
                                    <select className={inputCls} value={form.direction} onChange={e => set('direction', e.target.value as WidgetForm['direction'])}><option value="rtl">راست‌به‌چپ</option><option value="ltr">چپ‌به‌راست</option></select>
                                </Field>
                            </div>
                        </section>

                        <section className="bg-white rounded-xl p-4 border border-gray-200" aria-labelledby="sec-prechat">
                            <h2 id="sec-prechat" className="font-bold mb-3">پرسش‌های پیش از گفتگو</h2>
                            <fieldset className="mb-3">
                                <legend className="text-xs font-semibold text-gray-600 mb-1">حالت</legend>
                                {([['none', 'بدون پرسش — پنل مستقیماً باز می‌شود'], ['one', 'یک پرسش'], ['form', 'فرم ساختاریافته']] as [PreChatMode, string][]).map(([v, l]) => (
                                    <label key={v} className="flex items-center gap-2 text-sm mb-1"><input type="radio" name="prechat-mode" checked={form.preChatMode === v} onChange={() => setMode(v)} /> {l}</label>
                                ))}
                            </fieldset>
                            {err('pre_chat.fields') && <p role="alert" className="text-[11.5px] text-red-600 mb-2">{err('pre_chat.fields')}</p>}
                            {form.preChatMode !== 'none' && (
                                <>
                                    <div className="grid sm:grid-cols-2 gap-x-4">
                                        <Field label="عنوان فرم" error={err('pre_chat.title')}><input className={inputCls} value={form.preChatTitle} maxLength={120} onChange={e => set('preChatTitle', e.target.value)} /></Field>
                                        <Field label="متن دکمهٔ شروع" error={err('pre_chat.submit_label')}><input className={inputCls} value={form.submitLabel} maxLength={40} onChange={e => set('submitLabel', e.target.value)} /></Field>
                                    </div>
                                    {(form.preChatMode === 'one' ? form.fields.slice(0, 1) : form.fields).map((f, i) => (
                                        <div key={i} className="border border-gray-200 rounded-lg p-3 mb-3 bg-gray-50" data-testid="field-editor">
                                            <div className="grid sm:grid-cols-2 gap-x-4">
                                                <Field label="برچسب پرسش" error={fieldErr(i, 'label')}><input className={inputCls} value={f.label} maxLength={120} onChange={e => setField(i, { label: e.target.value })} /></Field>
                                                <Field label="نوع" error={fieldErr(i, 'type')}>
                                                    <select className={inputCls} value={f.type} onChange={e => setField(i, { type: e.target.value as FieldType })}>
                                                        {FIELD_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                                                    </select>
                                                </Field>
                                                <Field label="کلید (انگلیسی، برای اتوماسیون و گزارش)" error={fieldErr(i, 'key')}><input className={inputCls} dir="ltr" value={f.key} maxLength={40} onChange={e => setField(i, { key: e.target.value })} /></Field>
                                                <Field label="متن راهنما (placeholder)" error={fieldErr(i, 'placeholder')}><input className={inputCls} value={f.placeholder} maxLength={120} onChange={e => setField(i, { placeholder: e.target.value })} /></Field>
                                                {['text', 'textarea'].includes(f.type) && <Field label="حداکثر طول" error={fieldErr(i, 'max_length')}><input inputMode="numeric" className={inputCls} value={f.maxLength} onChange={e => setField(i, { maxLength: e.target.value })} /></Field>}
                                            </div>
                                            {CHOICE_TYPES.includes(f.type) && (
                                                <Field label="گزینه‌ها" hint="هر خط یک گزینه: «مقدار | برچسب»" error={fieldErr(i, 'choices')}>
                                                    <textarea className={inputCls} rows={3} dir="rtl" value={f.choicesText} onChange={e => setField(i, { choicesText: e.target.value })} />
                                                </Field>
                                            )}
                                            {f.type !== 'hidden' && <Check label="الزامی" checked={f.required} onChange={v => setField(i, { required: v })} />}
                                            {f.type === 'hidden' && <p className="text-[11px] text-gray-400 mb-2">مقدار از صفحهٔ میزبان (RastiChat.init → context) گرفته می‌شود و برای اپراتور «تأییدنشده» علامت می‌خورد.</p>}
                                            {form.preChatMode === 'form' && (
                                                <div className="flex gap-2">
                                                    <button type="button" className="text-xs px-2 py-1 border rounded" onClick={() => move(i, -1)} aria-label="بالا">↑</button>
                                                    <button type="button" className="text-xs px-2 py-1 border rounded" onClick={() => move(i, 1)} aria-label="پایین">↓</button>
                                                    <button type="button" className="text-xs px-2 py-1 border rounded text-red-600" onClick={() => removeField(i)}>حذف</button>
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                    {form.preChatMode === 'form' && form.fields.length < 12 && <button type="button" className="text-sm text-terracotta" onClick={addField}>+ افزودن پرسش</button>}
                                </>
                            )}
                        </section>

                        <section className="bg-white rounded-xl p-4 border border-gray-200" aria-labelledby="sec-identity">
                            <h2 id="sec-identity" className="font-bold mb-3">هویت مشتری</h2>
                            <Check label="مهمان (بدون ورود) می‌تواند گفتگو را شروع کند" checked={form.guestAllowed && !form.authenticatedOnly} onChange={v => { set('guestAllowed', v); if (v) set('authenticatedOnly', false); }} />
                            <Check label="فقط مشتریان واردشده در برنامهٔ اصلی (نیازمند اتصال یکپارچه)" checked={form.authenticatedOnly} onChange={v => { set('authenticatedOnly', v); if (v) set('guestAllowed', false); }} />
                            {err('identity') && <p role="alert" className="text-[11.5px] text-red-600">{err('identity')}</p>}
                        </section>

                        {project && (
                            <section className="bg-white rounded-xl p-4 border border-gray-200" aria-labelledby="sec-embed">
                                <h2 id="sec-embed" className="font-bold mb-2">کد جاسازی</h2>
                                <p className="text-[11.5px] text-gray-500 mb-2">شناسهٔ پروژه عمومی است و محرمانه نیست. دامنه‌های مجاز: {project.allowed_domains || 'بدون محدودیت'}.</p>
                                <pre className="text-[11.5px] bg-gray-900 text-gray-100 rounded-lg p-3 overflow-x-auto" dir="ltr">{snippet}</pre>
                            </section>
                        )}

                        <div className="flex items-center gap-3 sticky bottom-0 bg-gray-100 py-3">
                            <button type="submit" disabled={status === 'saving'} className="bg-terracotta text-white px-5 py-2 rounded-lg font-semibold disabled:opacity-60">{status === 'saving' ? 'در حال ذخیره…' : 'ذخیره'}</button>
                            {message && <p role={status === 'error' ? 'alert' : 'status'} className={status === 'error' ? 'text-red-600 text-sm' : 'text-green-700 text-sm'}>{message}</p>}
                        </div>
                    </form>
                )}
            </div>
        </div>
    );
}
