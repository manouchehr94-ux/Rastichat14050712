'use client';
import { useState, useEffect, useRef } from 'react';
import {
    fetchPlatformInbox, fetchPlatformSupportMessages, assignTicket, replyTicket, connectSupportWebSocket, markPlatformRead,
    fetchPlatformWorkspaces, startPlatformThread, closePlatformThread, reopenPlatformThread, type PlatformWorkspace,
} from '@/lib/api';
import { isMine, statusLabel, openedByLabel } from '@/lib/supportThread';
import { useRouter } from 'next/navigation';

interface Conversation { id: string; status: string; subject: string; unread_count?: number; workspace_name?: string; opened_by_side?: string; }
interface Message { id: string; content: string; sender_type: string; sender_side?: string; }

export default function PlatformInboxPage() {
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [selectedConv, setSelectedConv] = useState<Conversation | null>(null);
    const [messages, setMessages] = useState<Message[]>([]);
    const [input, setInput] = useState('');
    const [mobileView, setMobileView] = useState<'list' | 'chat'>('list');
    const [showNew, setShowNew] = useState(false);
    const [workspaces, setWorkspaces] = useState<PlatformWorkspace[]>([]);
    const [newWs, setNewWs] = useState('');
    const [newSubject, setNewSubject] = useState('');
    const [newMsg, setNewMsg] = useState('');
    const [newError, setNewError] = useState('');
    const [starting, setStarting] = useState(false);
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

    const openNewForm = () => {
        setShowNew(true); setNewError('');
        fetchPlatformWorkspaces().then(list => { setWorkspaces(list); if (list[0]) setNewWs(String(list[0].id)); }).catch(() => setNewError('دریافت فهرست سازمان‌ها ناموفق بود.'));
    };

    /** Platform -> tenant: opens (or resumes) the conversation; the tenant never has to write first. */
    const handleStart = async () => {
        if (!newWs || !newMsg.trim() || starting) return;
        setStarting(true); setNewError('');
        try {
            const conv = await startPlatformThread({
                workspace_id: Number(newWs), subject: newSubject.trim() || 'پیام پلتفرم', message: newMsg.trim(),
                client_message_id: 'start_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8),
            });
            setShowNew(false); setNewSubject(''); setNewMsg('');
            await loadInbox();
            await handleSelectConv(conv);
        } catch (e) {
            const status = (e as { status?: number }).status;
            setNewError(status === 404 ? 'این سازمان در دسترس شما نیست.' : status === 409 ? 'این سازمان فعال نیست.' : 'ارسال پیام ناموفق بود. دوباره تلاش کنید.');
        } finally { setStarting(false); }
    };

    const handleToggleClosed = async () => {
        if (!selectedConv) return;
        try {
            const updated = selectedConv.status === 'CLOSED' ? await reopenPlatformThread(selectedConv.id) : await closePlatformThread(selectedConv.id);
            setSelectedConv({ ...selectedConv, status: updated.status });
            loadInbox();
        } catch (e) { /* handle */ }
    };

    const handleReply = async () => {
        if (!selectedConv || !input.trim()) return;
        const clientId = 'msg_' + Date.now();
        const content = input; setInput('');
        setMessages(prev => [...prev, { id: clientId, content, sender_type: 'USER', sender_side: 'platform' }]);
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
                <div className="px-4 py-4 border-b font-bold text-indigo-600 flex-none flex items-center justify-between gap-2">
                    <span>صندوق ورودی پشتیبانی</span>
                    <button type="button" onClick={openNewForm} className="text-xs bg-indigo-600 text-white px-2.5 py-1.5 rounded-lg font-normal">گفتگوی جدید با سازمان</button>
                </div>
                {showNew && (
                    <form className="p-4 border-b bg-gray-50 flex flex-col gap-2" aria-label="گفتگوی جدید با سازمان" onSubmit={e => { e.preventDefault(); void handleStart(); }}>
                        <label className="text-xs font-semibold text-gray-600" htmlFor="new-ws">سازمان</label>
                        <select id="new-ws" value={newWs} onChange={e => setNewWs(e.target.value)} className="w-full p-2 border rounded text-sm bg-white">
                            {workspaces.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
                        </select>
                        <label className="text-xs font-semibold text-gray-600" htmlFor="new-subject">موضوع</label>
                        <input id="new-subject" value={newSubject} onChange={e => setNewSubject(e.target.value)} className="w-full p-2 border rounded text-sm" />
                        <label className="text-xs font-semibold text-gray-600" htmlFor="new-msg">پیام</label>
                        <textarea id="new-msg" value={newMsg} onChange={e => setNewMsg(e.target.value)} className="w-full p-2 border rounded text-sm h-24" />
                        {newError && <p role="alert" className="text-xs text-red-600">{newError}</p>}
                        <div className="flex gap-2">
                            <button type="submit" disabled={starting || !newWs || !newMsg.trim()} className="flex-1 bg-green-600 text-white p-2 rounded text-sm disabled:opacity-60">{starting ? 'در حال ارسال…' : 'ارسال'}</button>
                            <button type="button" onClick={() => setShowNew(false)} className="px-3 border rounded text-sm">انصراف</button>
                        </div>
                    </form>
                )}

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
                                    {conv.workspace_name && <span className="block text-[11px] text-indigo-600">{conv.workspace_name}</span>}
                                    {conv.subject}
                                </span>

                                {!!conv.unread_count && conv.unread_count > 0 && (
                                    <span className="bg-red-500 text-white text-xs rounded-full min-w-6 h-6 px-1.5 flex items-center justify-center flex-none">
                                        {conv.unread_count}
                                    </span>
                                )}
                            </div>

                            <div className="text-xs text-gray-500 mt-1">
                                {statusLabel(conv.status, 'platform')}
                                {conv.opened_by_side === 'PLATFORM' && <span className="mr-2 text-indigo-600">· {openedByLabel('PLATFORM')}</span>}
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
                                    {selectedConv.workspace_name ? `${selectedConv.workspace_name} — ` : ''}#{selectedConv.subject}
                                </span>
                            </div>

                            <div className="flex gap-2 flex-none">
                                <button type="button" onClick={handleToggleClosed} className="text-xs border px-3 py-2 rounded-lg">
                                    {selectedConv.status === 'CLOSED' ? 'بازگشایی' : 'بستن گفتگو'}
                                </button>
                                <button
                                    onClick={handleAssign}
                                    className="text-xs bg-indigo-600 text-white px-3 py-2 rounded-lg"
                                >
                                    تخصیص به من
                                </button>
                            </div>
                        </div>

                        <div className="flex-1 min-h-0 overflow-y-auto px-3 sm:px-4 py-4 space-y-3">
                            {messages.map(msg => (
                                <div
                                    key={msg.id}
                                    className={`flex ${
                                        isMine(msg, 'platform')
                                            ? 'justify-start'
                                            : 'justify-end'
                                    }`}
                                >
                                    <div
                                        className={`p-3 rounded-2xl max-w-[85%] sm:max-w-xs text-sm break-words whitespace-pre-wrap ${
                                            isMine(msg, 'platform')
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
