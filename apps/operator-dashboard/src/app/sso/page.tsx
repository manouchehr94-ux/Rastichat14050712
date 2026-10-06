'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ssoLogin } from '@/lib/api';
import { parseSsoFragment } from '@/lib/sso';

// Landing page for host-application single sign-on: no second login. See lib/sso.ts.
export default function SsoPage() {
    const router = useRouter();
    const [error, setError] = useState('');

    useEffect(() => {
        const parsed = parseSsoFragment(window.location.hash);
        // remove the credential from the address bar/history before anything else happens
        window.history.replaceState(null, '', window.location.pathname);
        const exchange = parsed ? ssoLogin(parsed.assertion).then(() => router.replace(parsed.next)) : Promise.reject(new Error('no assertion'));
        exchange.catch(() => setError('ورود یکپارچه ناموفق بود یا منقضی شده است. لطفاً از برنامهٔ اصلی دوباره وارد شوید.'));
    }, [router]);

    return (
        <div className="min-h-[100dvh] flex items-center justify-center bg-gray-100 px-4 py-6" dir="rtl">
            <div className="bg-white p-5 sm:p-8 rounded-xl shadow-md w-full max-w-sm text-center" role="status" aria-live="polite">
                {error ? <p className="text-red-600">{error}</p> : <p>در حال ورود…</p>}
            </div>
        </div>
    );
}
