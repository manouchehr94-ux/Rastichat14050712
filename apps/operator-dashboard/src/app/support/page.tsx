'use client';
import { useState, useEffect, useRef } from 'react';
import { fetchSupportConversations, fetchSupportMessages, sendSupportMessage, connectSupportWebSocket, createSupportRequest } from '@/lib/api';
import { useRouter } from 'next/navigation';

interface Conversation { id: string; status: string; subject: string; unread_count?: number; }
interface Message { id: string; content: string; sender_type: string; }

export default function SupportPage() {
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [selectedConv, setSelectedConv] = useState<Conversation | null>(null);
    const [messages, setMessages] = useState<Message[]>([]);
    const [input, setInput] = useState('');
    const [showCreate, setShowCreate] = useState(false);
    const [newSubject, setNewSubject] = useState('');
    const [newMsg, setNewMsg] = useState('');
    const wsRef = useRef<WebSocket | null>(null);
    const router = useRouter();

    useEffect(() => {
        if (!localStorage.getItem('token')) { router.push('/login'); return; }
        fetchSupportConversations().then(setConversations).catch(console.error);
    }, []);

    const handleSelectConv = async (conv: Conversation) => {
        setSelectedConv(conv);
        const msgs = await fetchSupportMessages(conv.id);
        setMessages(msgs);
        if (wsRef.current) wsRef.current.close();
        wsRef.current = connectSupportWebSocket(conv.id, (data) => {
            setMessages(prev => {
                if (prev.some(m => m.id === data.id)) return prev; // Deduplicate
                return [...prev, data];
            });
        });
    };

    const handleSend = async () => {
        if (!selectedConv || !input.trim()) return;
        const clientId = 'msg_' + Date.now();
        const content = input; setInput('');
        setMessages(prev => [...prev, { id: clientId, content, sender_type: 'USER' }]);
        try { await sendSupportMessage(selectedConv.id, content, clientId); } catch (e) { console.error(e); }
    };

    const handleCreate = async () => {
        if (!newSubject.trim() || !newMsg.trim()) return;
        await createSupportRequest(newSubject, newMsg);
        setShowCreate(false); setNewSubject(''); setNewMsg('');
        fetchSupportConversations().then(setConversations);
    };

    return (
        <div className="flex h-screen bg-gray-100" dir="rtl">
            <div className="w-1/3 bg-white border-l border-gray-200 overflow-y-auto">
                <div className="p-4 border-b border-gray-200 flex justify-between items-center">
                    <span className="font-bold text-purple-600">پشتیبانی RastiChat</span>
                    <button onClick={() => setShowCreate(!showCreate)} className="text-sm bg-purple-600 text-white px-2 py-1 rounded">تیکت جدید</button>
                </div>
                {showCreate && (
                    <div className="p-4 border-b bg-gray-50">
                        <input value={newSubject} onChange={e => setNewSubject(e.target.value)} placeholder="موضوع" className="w-full p-2 border rounded mb-2 text-sm" />
                        <textarea value={newMsg} onChange={e => setNewMsg(e.target.value)} placeholder="پیام اولیه" className="w-full p-2 border rounded mb-2 text-sm h-20" />
                        <button onClick={handleCreate} className="w-full bg-green-600 text-white p-2 rounded text-sm">ارسال درخواست</button>
                    </div>
                )}
                {conversations.map(conv => (
                    <div key={conv.id} onClick={() => handleSelectConv(conv)} className={`p-4 border-b cursor-pointer hover:bg-gray-50 ${selectedConv?.id === conv.id ? 'bg-purple-50' : ''}`}>
                        <div className="flex justify-between">
                            <span className="font-medium text-sm">{conv.subject}</span>
                            {conv.unread_count && conv.unread_count > 0 && (
                                <span className="bg-red-500 text-white text-xs rounded-full px-2">{conv.unread_count}</span>
                            )}
                        </div>
                        <div className="text-xs text-gray-500">{conv.status}</div>
                    </div>
                ))}
            </div>
            <div className="flex-1 flex flex-col">
                {selectedConv ? (
                    <>
                        <div className="p-4 bg-white border-b font-bold text-sm">#{selectedConv.subject}</div>
                        <div className="flex-1 overflow-y-auto p-4 space-y-3">
                            {messages.map(msg => (
                                <div key={msg.id} className={`flex ${msg.sender_type === 'USER' ? 'justify-start' : 'justify-end'}`}>
                                    <div className={`p-3 rounded-lg max-w-xs text-sm ${msg.sender_type === 'USER' ? 'bg-purple-600 text-white' : 'bg-gray-200'}`}>{msg.content}</div>
                                </div>
                            ))}
                        </div>
                        <div className="p-4 bg-white border-t flex">
                            <input type="text" value={input} onChange={e => setInput(e.target.value)} className="flex-1 border rounded-r p-2 text-sm" placeholder="پاسخ..." />
                            <button onClick={handleSend} className="bg-purple-600 text-white px-4 rounded-l text-sm">ارسال</button>
                        </div>
                    </>
                ) : <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">یک تیکت را انتخاب کنید یا تیکت جدید بسازید</div>}
            </div>
        </div>
    );
}
