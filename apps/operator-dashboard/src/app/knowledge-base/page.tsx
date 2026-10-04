'use client';
import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
    fetchKBArticles, fetchKBCategories, createKBArticle, updateKBArticle, publishKBArticle,
    archiveKBArticle, duplicateKBArticle, fetchKBArticleRevisions, restoreKBArticleRevision,
    fetchKBFeedbackSummary,
} from '@/lib/api';

const STATUS_LABELS: Record<string, string> = {
    DRAFT: 'پیش‌نویس', REVIEW: 'در بازبینی', PUBLISHED: 'منتشرشده', ARCHIVED: 'بایگانی‌شده',
};
const VISIBILITY_LABELS: Record<string, string> = {
    INTERNAL: 'فقط داخلی', CUSTOMER: 'مشتریان این فضای‌کار', PUBLIC: 'عمومی',
};

interface Category { id: string; name: string; slug: string; parent: string | null; is_active: boolean }
interface Revision { id: string; revision_number: number; title: string; excerpt: string; body: string; change_summary: string; created_at: string }
interface Article {
    id: string; title: string; slug: string; excerpt: string; body: string; rendered_body: string;
    status: string; visibility: string; language: string; tags: string[]; is_featured: boolean;
    category: string | null; current_revision_number: number; view_count: number;
    feedback_summary: { helpful: number; not_helpful: number; total: number };
}

type View = { name: 'list' } | { name: 'edit'; article: Article | 'new' } | { name: 'revisions'; article: Article };

export default function KnowledgeBasePage() {
    const router = useRouter();
    const [articles, setArticles] = useState<Article[]>([]);
    const [categories, setCategories] = useState<Category[]>([]);
    const [view, setView] = useState<View>({ name: 'list' });
    const [query, setQuery] = useState('');
    const [statusFilter, setStatusFilter] = useState('');
    const [denied, setDenied] = useState(false);
    const [toast, setToast] = useState('');
    const [revisions, setRevisions] = useState<Revision[]>([]);

    const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

    const loadArticles = useCallback(() => {
        fetchKBArticles({ q: query || undefined, status: statusFilter || undefined })
            .then(setArticles)
            .catch((e: Error) => { if (e.message.startsWith('403')) setDenied(true); });
    }, [query, statusFilter]);

    useEffect(() => {
        if (!localStorage.getItem('token')) { router.push('/login'); return; }
        loadArticles();
        fetchKBCategories().then(setCategories).catch(() => setCategories([]));
    }, [loadArticles, router]);

    const handlePublish = async (a: Article) => {
        try { await publishKBArticle(a.id); showToast('مقاله منتشر شد'); loadArticles(); } catch { showToast('انتشار ناموفق بود'); }
    };
    const handleArchive = async (a: Article) => {
        try { await archiveKBArticle(a.id); showToast('مقاله بایگانی شد'); loadArticles(); } catch { showToast('بایگانی ناموفق بود'); }
    };
    const handleDuplicate = async (a: Article) => {
        try { await duplicateKBArticle(a.id); showToast('کپی مقاله ایجاد شد'); loadArticles(); } catch { showToast('کپی‌سازی ناموفق بود'); }
    };
    const openRevisions = async (a: Article) => {
        try { const revs = await fetchKBArticleRevisions(a.id); setRevisions(revs); setView({ name: 'revisions', article: a }); }
        catch { showToast('بارگذاری تاریخچه ناموفق بود'); }
    };
    const handleRestore = async (a: Article, revisionNumber: number) => {
        if (!confirm(`نسخه ${revisionNumber} بازگردانی شود؟ یک نسخه جدید ایجاد خواهد شد.`)) return;
        try {
            await restoreKBArticleRevision(a.id, revisionNumber);
            showToast('نسخه بازگردانی شد (یک نسخه جدید ایجاد شد)');
            const revs = await fetchKBArticleRevisions(a.id);
            setRevisions(revs);
            loadArticles();
        } catch { showToast('بازگردانی ناموفق بود'); }
    };

    if (denied) {
        return (
            <div className="max-w-2xl mx-auto mt-16 text-center">
                <p className="text-gray-500">دسترسی به پایگاه دانش برای شما مجاز نیست.</p>
                <Link href="/" className="inline-block mt-4 text-sm text-terracotta hover:underline">بازگشت به صندوق ورودی</Link>
            </div>
        );
    }

    return (
        <div className="max-w-5xl mx-auto p-4 flex flex-col gap-3" dir="rtl">
            <div className="flex items-center justify-between">
                <h1 className="font-bold text-lg text-gray-800">پایگاه دانش</h1>
                <div className="flex items-center gap-3 text-sm">
                    <Link href="/knowledge-base/categories" className="text-terracotta hover:underline">دسته‌بندی‌ها</Link>
                    <Link href="/" className="text-terracotta hover:underline">بازگشت به صندوق ورودی</Link>
                </div>
            </div>
            {toast && <div className="text-xs bg-cream text-gray-700 border border-gray-200 rounded-lg px-3 py-2">{toast}</div>}

            {view.name === 'list' && (
                <>
                    <div className="flex flex-wrap items-center gap-2">
                        <input
                            value={query} onChange={e => setQuery(e.target.value)} placeholder="جستجو در عنوان، خلاصه و متن مقاله…"
                            className="flex-1 min-w-[220px] text-sm border border-gray-200 rounded-lg px-3 py-2 outline-none focus:border-terracotta"
                        />
                        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} className="text-sm border border-gray-200 rounded-lg px-2 py-2">
                            <option value="">همه وضعیت‌ها</option>
                            {Object.entries(STATUS_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                        </select>
                        <button onClick={() => setView({ name: 'edit', article: 'new' })} className="text-sm bg-terracotta text-white rounded-lg px-3 py-2">+ مقاله جدید</button>
                    </div>
                    <div className="bg-white border border-gray-200 rounded-xl overflow-x-auto">
                        <table className="w-full text-sm text-right">
                            <thead>
                                <tr className="text-xs text-gray-400 border-b border-gray-100">
                                    <th className="py-2 px-3 font-medium">عنوان</th>
                                    <th className="py-2 px-3 font-medium">وضعیت</th>
                                    <th className="py-2 px-3 font-medium">نمایانی</th>
                                    <th className="py-2 px-3 font-medium">بازدید</th>
                                    <th className="py-2 px-3 font-medium">بازخورد</th>
                                    <th className="py-2 px-3 font-medium">عملیات</th>
                                </tr>
                            </thead>
                            <tbody>
                                {articles.length === 0 && <tr><td colSpan={6} className="py-8 text-center text-xs text-gray-400">مقاله‌ای یافت نشد.</td></tr>}
                                {articles.map(a => (
                                    <tr key={a.id} className="border-b border-gray-50">
                                        <td className="py-2 px-3">
                                            <button onClick={() => setView({ name: 'edit', article: a })} className="text-gray-800 hover:text-terracotta font-medium">{a.title}</button>
                                        </td>
                                        <td className="py-2 px-3 text-xs">{STATUS_LABELS[a.status] || a.status}</td>
                                        <td className="py-2 px-3 text-xs text-gray-500">{VISIBILITY_LABELS[a.visibility] || a.visibility}</td>
                                        <td className="py-2 px-3 text-xs text-gray-400">{a.view_count}</td>
                                        <td className="py-2 px-3 text-xs text-gray-400">{a.feedback_summary ? `${a.feedback_summary.helpful}/${a.feedback_summary.total}` : '—'}</td>
                                        <td className="py-2 px-3 text-xs">
                                            <div className="flex items-center gap-2">
                                                {a.status !== 'PUBLISHED' && <button onClick={() => handlePublish(a)} className="text-green-600 hover:underline">انتشار</button>}
                                                {a.status !== 'ARCHIVED' && <button onClick={() => handleArchive(a)} className="text-gray-500 hover:underline">بایگانی</button>}
                                                <button onClick={() => openRevisions(a)} className="text-terracotta hover:underline">تاریخچه</button>
                                                <button onClick={() => handleDuplicate(a)} className="text-gray-500 hover:underline">کپی</button>
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </>
            )}

            {view.name === 'edit' && (
                <ArticleEditor
                    article={view.article} categories={categories}
                    onCancel={() => setView({ name: 'list' })}
                    onSaved={() => { setView({ name: 'list' }); loadArticles(); showToast('مقاله ذخیره شد'); }}
                />
            )}

            {view.name === 'revisions' && (
                <div className="flex flex-col gap-3">
                    <div className="flex items-center justify-between">
                        <h2 className="font-bold text-gray-800">تاریخچه نسخه‌های «{view.article.title}»</h2>
                        <button onClick={() => setView({ name: 'list' })} className="text-sm text-terracotta hover:underline">بازگشت به فهرست</button>
                    </div>
                    <div className="bg-white border border-gray-200 rounded-xl divide-y divide-gray-50">
                        {revisions.map(r => (
                            <div key={r.id} className="p-3 flex items-center justify-between">
                                <div>
                                    <div className="text-sm font-medium text-gray-800">نسخه {r.revision_number} — {r.title}</div>
                                    <div className="text-xs text-gray-400">{new Date(r.created_at).toLocaleString('fa-IR')}{r.change_summary ? ` — ${r.change_summary}` : ''}</div>
                                </div>
                                {r.revision_number !== view.article.current_revision_number && (
                                    <button onClick={() => handleRestore(view.article, r.revision_number)} className="text-xs text-terracotta hover:underline">بازگردانی این نسخه</button>
                                )}
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
}

function ArticleEditor({ article, categories, onCancel, onSaved }: {
    article: Article | 'new'; categories: Category[]; onCancel: () => void; onSaved: () => void;
}) {
    const isNew = article === 'new';
    const [title, setTitle] = useState(isNew ? '' : article.title);
    const [excerpt, setExcerpt] = useState(isNew ? '' : article.excerpt);
    const [body, setBody] = useState(isNew ? '' : article.body);
    const [category, setCategory] = useState(isNew ? '' : (article.category || ''));
    const [visibility, setVisibility] = useState(isNew ? 'INTERNAL' : article.visibility);
    const [tags, setTags] = useState(isNew ? '' : article.tags.join('، '));
    const [saving, setSaving] = useState(false);
    const [feedback, setFeedback] = useState<{ helpful: number; not_helpful: number; total: number } | null>(null);

    useEffect(() => {
        if (!isNew) fetchKBFeedbackSummary(article.id).then(setFeedback).catch(() => setFeedback(null));
    }, [isNew, article]);

    const handleSave = async () => {
        setSaving(true);
        const payload = {
            title, excerpt, body, category: category || null, visibility,
            tags: tags.split('،').map(t => t.trim()).filter(Boolean),
        };
        try {
            if (isNew) await createKBArticle(payload);
            else await updateKBArticle(article.id, payload);
            onSaved();
        } catch (e) {
            alert((e as Error).message);
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="flex flex-col gap-2.5 bg-white border border-gray-200 rounded-xl p-4">
                <label className="text-xs text-gray-500">عنوان</label>
                <input value={title} onChange={e => setTitle(e.target.value)} className="text-sm border border-gray-200 rounded-lg px-3 py-2" />
                <label className="text-xs text-gray-500">خلاصه کوتاه</label>
                <input value={excerpt} onChange={e => setExcerpt(e.target.value)} className="text-sm border border-gray-200 rounded-lg px-3 py-2" />
                <label className="text-xs text-gray-500">دسته‌بندی</label>
                <select value={category} onChange={e => setCategory(e.target.value)} className="text-sm border border-gray-200 rounded-lg px-3 py-2">
                    <option value="">بدون دسته‌بندی</option>
                    {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                <label className="text-xs text-gray-500">نمایانی</label>
                <select value={visibility} onChange={e => setVisibility(e.target.value)} className="text-sm border border-gray-200 rounded-lg px-3 py-2">
                    {Object.entries(VISIBILITY_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
                <label className="text-xs text-gray-500">برچسب‌ها (با «،» جدا کنید)</label>
                <input value={tags} onChange={e => setTags(e.target.value)} className="text-sm border border-gray-200 rounded-lg px-3 py-2" />
                <label className="text-xs text-gray-500">متن مقاله (Markdown)</label>
                <textarea value={body} onChange={e => setBody(e.target.value)} rows={14}
                    className="text-sm border border-gray-200 rounded-lg px-3 py-2 font-mono" placeholder="# عنوان&#10;&#10;متن با نشانه‌گذاری Markdown…" />
                {feedback && (
                    <div className="text-xs text-gray-400">بازخورد بازدیدکنندگان: {feedback.helpful} مفید از {feedback.total}</div>
                )}
                <div className="flex items-center gap-2 pt-2">
                    <button onClick={handleSave} disabled={saving || !title.trim()} className="text-sm bg-terracotta text-white rounded-lg px-4 py-2 disabled:opacity-50">
                        {saving ? 'در حال ذخیره…' : 'ذخیره'}
                    </button>
                    <button onClick={onCancel} className="text-sm text-gray-500 hover:underline">انصراف</button>
                </div>
            </div>
            <div className="bg-white border border-gray-200 rounded-xl p-4">
                <div className="text-xs text-gray-400 mb-2">پیش‌نمایش</div>
                <div className="prose prose-sm max-w-none text-sm text-gray-700" dangerouslySetInnerHTML={{ __html: renderPreviewMarkdown(body) }} />
            </div>
        </div>
    );
}

// A tiny client-side preview only — the SAFE rendering (allowlist-only,
// escaped text) happens server-side via knowledge_base.markdown_renderer
// and is what's actually stored/served; this is just a rough live preview
// while typing, so it deliberately keeps things simple (escape + linebreaks
// + heading lines) rather than reimplementing the full renderer in JS.
function renderPreviewMarkdown(source: string): string {
    const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return source.split('\n').map(line => {
        const heading = line.match(/^(#{1,6})\s+(.*)$/);
        if (heading) { const level = heading[1].length; return `<h${level}>${esc(heading[2])}</h${level}>`; }
        if (!line.trim()) return '<br/>';
        return `<p>${esc(line)}</p>`;
    }).join('');
}
