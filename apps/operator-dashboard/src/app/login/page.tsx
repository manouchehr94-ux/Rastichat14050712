'use client';
import { useState } from 'react';
import { login } from '@/lib/api';
import { useRouter } from 'next/navigation';

export default function LoginPage() {
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const router = useRouter();

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        try {
            await login(email, password);
            router.push('/');
        } catch (err) {
            setError('Invalid credentials');
        }
    };

    return (
        <div className="min-h-[100dvh] flex items-center justify-center bg-gray-100 px-4 py-6" dir="rtl">
            <div className="bg-white p-5 sm:p-8 rounded-xl shadow-md w-full max-w-sm">
                <h1 className="text-2xl font-bold mb-6 text-center">ورود اپراتور</h1>
                {error && <p className="text-red-500 mb-4 text-center">{error}</p>}
                <form onSubmit={handleSubmit}>
                    <div className="mb-4">
                        <label className="block mb-1">نام کاربری</label>
                        <input type="text" value={email} onChange={(e) => setEmail(e.target.value)} className="w-full p-2 border rounded" required />
                    </div>
                    <div className="mb-6">
                        <label className="block mb-1">رمز عبور</label>
                        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} className="w-full p-2 border rounded" required />
                    </div>
                    <button type="submit" className="w-full bg-blue-600 text-white p-2 rounded hover:bg-blue-700">ورود</button>
                </form>
            </div>
        </div>
    );
}
