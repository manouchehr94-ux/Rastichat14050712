'use client';
import { useState, useEffect, useRef } from 'react';
import { fetchPlatformInbox, fetchPlatformSupportMessages, assignTicket, replyTicket, connectSupportWebSocket, markPlatformRead } from '@/lib/api';
import { useRouter } from 'next/navigation';

interface Conversation { id: string; status: string; subject: string; unread_count?: number; }
interface Message { id: string; content: string; sender_type: string; }

export default function PlatformInboxPage() {
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [selectedConv, setSelectedConv] = useState<Conversation | null>(null);
    const [messages, setMessages] = useState<Message[]>([]);
    const [input, setInput] = useState('');
    const [mobileView, setMobileView] = useState<'list' | 'chat'>('list');
    const wsRef = useRef<WebSocket | null>(null);
    const router = useRouter();

    const loadInbox = async () => {
        try { setConversations(await fetchPlatformInbox()); } catch (e) { /* handle */ }
    };

    useEffect(() => {
        if (!localStorage.getItem('token')) { router.push('/login'); return; }
        // Inlined rather than calling the hoisted `loadInbox` (matches the
        // pattern already used by apps/operator-dashboard/src/app/support/page.tsx's
        // equivalent effect) — react-hooks/set-state-in-effect can't verify
        // a same-file async function's timing and flags it defensively;
        // an inline .then() is directly recognizable as a fire-and-forget
        // fetch, not a synchronous state update.
        fetchPlatformInbox().then(setConversations).catch(() => {});
    }, []);

    const handleSelectConv = async (conv: Conversation) => {
        setSelectedConv(conv);
        setMobileView('chat');
        setMobileView('chat');
        setMessages([]); 
        try {
            const history = await fetchPlatformSupportMessages(conv.id);
            setMessages(history);
            await markPlatformRead(conv.id);
            loadInbox(); // Refresh unread badges
        } catch (e) { /* handle */ }
        
        if (wsRef.current) wsRef.current.close();
        wsRef.current = connectSupportWebSocket(conv.id, (data) => {
            setMessages(prev => {
                if (prev.some(m => m.id === data.id)) return prev; // Deduplicate
                return [...prev, data];
            });
        });
    };

    const handleAssign = async () => {
        if (!selectedConv) return;
        await assignTicket(selectedConv.id);
        alert('Assigned to you!');
    };

    const handleReply = async () => {
        if (!selectedConv || !input.trim()) return;
        const clientId = 'msg_' + Date.now();
        const content = input; setInput('');
        setMessages(prev => [...prev, { id: clientId, content, sender_type: 'USER' }]);
        try { await replyTicket(selectedConv.id, content, clientId); } catch (e) { /* handle */ }
    };

    return (
        <div className="flex h-[100dvh] min-h-0 bg-gray-100 overflow-hidden" dir="rtl">

            {/* Inbox list */}
            <div
                className={`w-full md:w-[340px] md:flex-none bg-white border-l border-gray-200 flex-col min-h-0 ${
                    mobileView === 'chat' ? 'hidden md:flex' : 'flex'
                }`}
            >
                <div className="px-4 py-4 border-b font-bold text-indigo-600 flex-none">
                    صندوق ورودی پشتیبانی
                </div>

                <div className="flex-1 min-h-0 overflow-y-auto">
                    {conversations.map(conv => (
                        <button
                            type="button"
                            key={conv.id}
                            onClick={() => handleSelectConv(conv)}
                            className={`w-full text-right p-4 border-b cursor-pointer hover:bg-gray-50 ${
                                selectedConv?.id === conv.id ? 'bg-indigo-50' : 'bg-white'
                            }`}
                        >
                            <div className="flex justify-between items-start gap-3">
                                <span className="font-medium text-sm min-w-0 break-words">
                                    {conv.subject}
                                </span>

                                {!!conv.unread_count && conv.unread_count > 0 && (
                                    <span className="bg-red-500 text-white text-xs rounded-full min-w-6 h-6 px-1.5 flex items-center justify-center flex-none">
                                        {conv.unread_count}
                                    </span>
                                )}
                            </div>

                            <div className="text-xs text-gray-500 mt-1">
                                {conv.status}
                            </div>
                        </button>
                    ))}

                    {conversations.length === 0 && (
                        <div className="p-8 text-center text-sm text-gray-400">
                            تیکتی وجود ندارد
                        </div>
                    )}
                </div>
            </div>

            {/* Chat */}
            <div
                className={`flex-1 min-w-0 min-h-0 flex-col ${
                    mobileView === 'list' ? 'hidden md:flex' : 'flex'
                }`}
            >
                {selectedConv ? (
                    <>
                        <div className="px-3 sm:px-4 py-3 bg-white border-b flex justify-between items-center gap-2 flex-none">
                            <div className="flex items-center gap-2 min-w-0">
                                <button
                                    type="button"
                                    onClick={() => setMobileView('list')}
                                    className="md:hidden w-9 h-9 flex-none rounded-lg bg-gray-100 text-gray-600 flex items-center justify-center text-xl"
                                    aria-label="بازگشت به صندوق ورودی"
                                >
                                    ›
                                </button>

                                <span className="font-bold text-sm truncate">
                                    #{selectedConv.subject}
                                </span>
                            </div>

                            <button
                                onClick={handleAssign}
                                className="text-xs bg-indigo-600 text-white px-3 py-2 rounded-lg flex-none"
                            >
                                تخصیص به من
                            </button>
                        </div>

                        <div className="flex-1 min-h-0 overflow-y-auto px-3 sm:px-4 py-4 space-y-3">
                            {messages.map(msg => (
                                <div
                                    key={msg.id}
                                    className={`flex ${
                                        msg.sender_type === 'USER'
                                            ? 'justify-start'
                                            : 'justify-end'
                                    }`}
                                >
                                    <div
                                        className={`p-3 rounded-2xl max-w-[85%] sm:max-w-xs text-sm break-words whitespace-pre-wrap ${
                                            msg.sender_type === 'USER'
                                                ? 'bg-indigo-600 text-white'
                                                : 'bg-white border border-gray-200'
                                        }`}
                                    >
                                        {msg.content}
                                    </div>
                                </div>
                            ))}

                            {messages.length === 0 && (
                                <div className="text-center text-sm text-gray-400 py-8">
                                    پیامی وجود ندارد
                                </div>
                            )}
                        </div>

                        <div className="p-2 sm:p-4 bg-white border-t flex gap-2 flex-none pb-[max(0.5rem,env(safe-area-inset-bottom))]">
                            <input
                                type="text"
                                value={input}
                                onChange={e => setInput(e.target.value)}
                                onKeyDown={e => {
                                    if (e.key === 'Enter') handleReply();
                                }}
                                className="flex-1 min-w-0 border rounded-xl px-3 py-2.5 text-base sm:text-sm outline-none focus:border-indigo-500"
                                placeholder="پاسخ..."
                            />

                            <button
                                onClick={handleReply}
                                className="bg-indigo-600 text-white px-4 rounded-xl text-sm flex-none"
                            >
                                ارسال
                            </button>
                        </div>
                    </>
                ) : (
                    <div className="flex-1 flex items-center justify-center text-gray-400 text-sm p-4">
                        یک تیکت را انتخاب کنید
                    </div>
                )}
            </div>
        </div>
    );
}
