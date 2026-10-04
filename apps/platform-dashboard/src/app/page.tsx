"use client";

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export default function RootPage() {
  const router = useRouter();
  useEffect(() => {
    if (typeof window !== 'undefined' && localStorage.getItem('token')) {
      router.push('/inbox');
    } else {
      router.push('/login');
    }
  }, [router]);

  return (
    <div className="flex h-screen items-center justify-center bg-gray-100">
      <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-indigo-500"></div>
    </div>
  );
}
