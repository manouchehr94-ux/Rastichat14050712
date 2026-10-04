'use client';
import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { fetchKBCategories, createKBCategory, updateKBCategory, deleteKBCategory } from '@/lib/api';

interface Category { id: string; name: string; slug: string; parent: string | null; description: string; is_active: boolean; sort_order: number }

export default function KnowledgeBaseCategoriesPage() {
    const router = useRouter();
    const [categories, setCategories] = useState<Category[]>([]);
    const [name, setName] = useState('');
    const [parent, setParent] = useState('');
    const [denied, setDenied] = useState(false);
    const [toast, setToast] = useState('');

    const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

    const load = useCallback(() => {
        fetchKBCategories().then(setCategories).catch((e: Error) => { if (e.message.startsWith('403')) setDenied(true); });
    }, []);

    useEffect(() => {
        if (!localStorage.getItem('token')) { router.push('/login'); return; }
        load();
    }, [load, router]);

    const handleCreate = async () => {
        if (!name.trim()) return;
        const slug = name.trim().toLowerCase().replace(/\s+/g, '-');
        try {
            await createKBCategory({ name: name.trim(), slug, parent: parent || null });
            setName(''); setParent('');
            load();
        } catch (e) {
            showToast((e as Error).message.includes('403') ? 'فقط مدیران فضای‌کار می‌توانند دسته‌بندی بسازند' : 'ایجاد دسته‌بندی ناموفق بود');
        }
    };

    const handleToggleActive = async (c: Category) => {
        try { await updateKBCategory(c.id, { is_active: !c.is_active }); load(); } catch { showToast('به‌روزرسانی ناموفق بود'); }
    };

    const handleDelete = async (c: Category) => {
        if (!confirm(`دسته‌بندی «${c.name}» حذف شود؟`)) return;
        try { await deleteKBCategory(c.id); load(); } catch { showToast('حذف ناموفق بود'); }
    };

    const roots = categories.filter(c => !c.parent);
    const childrenOf = (id: string) => categories.filter(c => c.parent === id);

    if (denied) {
        return (
            <div className="max-w-2xl mx-auto mt-16 text-center">
                <p className="text-gray-500">دسترسی به دسته‌بندی‌های پایگاه دانش برای شما مجاز نیست.</p>
                <Link href="/knowledge-base" className="inline-block mt-4 text-sm text-terracotta hover:underline">بازگشت</Link>
            </div>
        );
    }

    return (
        <div className="max-w-3xl mx-auto p-4 flex flex-col gap-3" dir="rtl">
            <div className="flex items-center justify-between">
                <h1 className="font-bold text-lg text-gray-800">دسته‌بندی‌های پایگاه دانش</h1>
                <Link href="/knowledge-base" className="text-sm text-terracotta hover:underline">بازگشت به مقاله‌ها</Link>
            </div>
            {toast && <div className="text-xs bg-cream text-gray-700 border border-gray-200 rounded-lg px-3 py-2">{toast}</div>}

            <div className="bg-white border border-gray-200 rounded-xl p-3 flex flex-wrap items-center gap-2">
                <input value={name} onChange={e => setName(e.target.value)} placeholder="نام دسته‌بندی جدید"
                    className="flex-1 min-w-[160px] text-sm border border-gray-200 rounded-lg px-3 py-2" />
                <select value={parent} onChange={e => setParent(e.target.value)} className="text-sm border border-gray-200 rounded-lg px-2 py-2">
                    <option value="">بدون دسته والد</option>
                    {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                <button onClick={handleCreate} className="text-sm bg-terracotta text-white rounded-lg px-3 py-2">+ افزودن</button>
            </div>

            <div className="bg-white border border-gray-200 rounded-xl divide-y divide-gray-50">
                {roots.length === 0 && <div className="py-8 text-center text-xs text-gray-400">دسته‌بندی‌ای وجود ندارد.</div>}
                {roots.map(c => (
                    <div key={c.id}>
                        <CategoryRow category={c} onToggle={handleToggleActive} onDelete={handleDelete} />
                        {childrenOf(c.id).map(child => (
                            <div key={child.id} className="ps-6">
                                <CategoryRow category={child} onToggle={handleToggleActive} onDelete={handleDelete} nested />
                            </div>
                        ))}
                    </div>
                ))}
            </div>
        </div>
    );
}

function CategoryRow({ category, onToggle, onDelete, nested }: {
    category: Category; onToggle: (c: Category) => void; onDelete: (c: Category) => void; nested?: boolean;
}) {
    return (
        <div className="p-3 flex items-center justify-between">
            <div>
                <span className={`text-sm ${nested ? 'text-gray-600' : 'font-medium text-gray-800'}`}>{nested ? '↳ ' : ''}{category.name}</span>
                <span className={`ms-2 text-[10px] px-1.5 py-0.5 rounded-full ${category.is_active ? 'bg-success-soft text-success' : 'bg-gray-100 text-gray-400'}`}>
                    {category.is_active ? 'فعال' : 'غیرفعال'}
                </span>
            </div>
            <div className="flex items-center gap-2 text-xs">
                <button onClick={() => onToggle(category)} className="text-terracotta hover:underline">{category.is_active ? 'غیرفعال‌سازی' : 'فعال‌سازی'}</button>
                <button onClick={() => onDelete(category)} className="text-red-500 hover:underline">حذف</button>
            </div>
        </div>
    );
}
