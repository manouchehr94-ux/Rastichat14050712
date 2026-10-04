'use client';
import { useState, useEffect, useRef, useCallback } from 'react';
import {
    fetchConversations, fetchMessages, sendMessage, connectWebSocket,
    patchConversation, markConversationRead, assignConversation, closeConversation, reopenConversation,
    fetchTeammates, uploadAttachment, shareProduct, requestRating, fetchProducts,
    sendTypingEvent, sendMarkReadEvent,
    fetchTags, fetchConversationTags, attachConversationTag, detachConversationTag,
    fetchConversationNotes, createConversationNote, fetchCustomerContext,
    fetchTeams, fetchQueues, claimConversation, transferConversation, escalateConversation, setConversationPriority,
    fetchAssignmentHistory, createInternalNote, fetchQuickReplies, applyQuickReply,
    fetchNotifications, fetchUnreadNotificationCount, markNotificationRead, markAllNotificationsRead,
    connectNotificationsWebSocket,
    fetchKBArticles, shareKBArticle,
    fetchMacros, previewMacro, executeMacro,
} from '@/lib/api';
import { useRouter } from 'next/navigation';
import Link from 'next/link';

interface Visitor { id: string; name: string | null; email: string | null; mobile: string | null; created_at: string; }
interface LastMessage { content: string; message_type: string; sender_type: string; created_at: string; }
interface Teammate { id: string; display_name: string; email: string; }
interface ConversationSLA {
    first_response_due_at: string | null; next_response_due_at: string | null; resolution_due_at: string | null;
    first_responded_at: string | null; resolved_at: string | null;
    first_response_breached_at: string | null; next_response_breached_at: string | null; resolution_breached_at: string | null;
}
interface Conversation {
    id: string; status: string; subject: string; category: string; notes: string; rating: number | null;
    created_at: string; updated_at: string; closed_at: string | null; unread_count: number;
    visitor: Visitor | null; last_message: LastMessage | null; assigned_to: Teammate | null;
    priority: string; queue: string | null; queue_name: string | null; team: string | null; team_name: string | null;
    sla: ConversationSLA | null;
}
interface Team { id: string; name: string; is_active: boolean; }
interface Queue { id: string; name: string; team: string; is_active: boolean; }
interface AssignmentHistoryEntry {
    id: string; action: string; assigned_to_email: string | null; assigned_by_email: string | null;
    previous_assignee_email: string | null; previous_team_name: string | null; new_team_name: string | null;
    reason: string; created_at: string;
}
interface QuickReply { id: string; scope: string; title: string; body: string; shortcut: string; category: string; usage_count: number; }
interface AppNotification {
    id: string; event_type: string; title: string; payload: Record<string, unknown>; read_at: string | null; created_at: string;
}
interface Tag { id: string; name: string; color: string; }
interface Note { id: string; body: string; created_by_email: string | null; created_at: string; }
interface CustomerOrderSummary { id: string; product_name: string; product_image: string; price: string; status: string; ordered_at: string; }
interface CustomerContext {
    name: string | null; phone: string | null; location: string; customer_since: string;
    order_count: number; total_spent: string; score: string | null; recent_orders: CustomerOrderSummary[];
}
const ORDER_STATUS_LABEL: Record<string, string> = {
    PROCESSING: 'در حال پردازش', SHIPPED: 'در حال ارسال', DELIVERED: 'تحویل شده', CANCELLED: 'لغو شده',
};
interface MessageMetadata {
    caption?: string; duration?: string | number; product_id?: string; brand?: string; name?: string;
    price?: string | number; old_price?: string | number | null; rating?: string | number;
    reviews_count?: number; image?: string;
    article?: { article_id: string; title: string; excerpt: string; category: string; url: string; image_url?: string };
}
interface KBArticleSummary { id: string; title: string; excerpt: string; status: string; visibility: string; }
interface MacroSummary { id: string; name: string; category: string; description: string; is_active: boolean; }
interface MacroPreviewAction { type: string; preview?: string; error?: string; article_title?: string | null; tag_name?: string | null; team_name?: string | null; agent_name?: string | null; }
interface MacroPreviewResult { macro_id: string; macro_name: string; actions: MacroPreviewAction[]; }
interface MacroActionExecutionResult { id: string; action_index: number; action_type: string; status: string; error_summary: string; }
interface MacroExecutionResult { id: string; status: string; action_executions: MacroActionExecutionResult[]; }
interface Message {
    id: string; sender_type: string; content: string; message_type: string; metadata: MessageMetadata;
    attachment_url: string | null; client_message_id: string; created_at: string; seen: boolean;
}
interface Product {
    id: string; brand: string; name: string; price: string; old_price: string | null; currency: string;
    discount_percent: number; rating: string; reviews_count: number; image: string; product_url: string; is_available: boolean;
}
type WsPayload =
    | { type: 'typing'; sender_type: string }
    | { type: 'message.seen'; reader: string }
    | (Message & { type?: 'chat.message' });

function genClientId() {
    return 'msg_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
}
function genIdempotencyKey() {
    return 'macro_' + Date.now() + '_' + Math.random().toString(36).slice(2, 10);
}
function nowMs() {
    return Date.now();
}

const STATUS_LABEL: Record<string, string> = {
    OPEN: 'باز', PENDING: 'در انتظار', CLOSED: 'بسته',
    WAITING_FOR_WORKSPACE: 'در انتظار شما', WAITING_FOR_PLATFORM: 'در انتظار پلتفرم', RESOLVED: 'حل‌شده',
};
const TABS: { key: string; label: string }[] = [
    { key: 'ALL', label: 'همه' }, { key: 'OPEN', label: 'باز' },
    { key: 'PENDING', label: 'در انتظار' }, { key: 'CLOSED', label: 'بسته' },
];
const EMOJIS = '😀 😊 😉 😍 🤩 😎 🤔 😴 😢 😅 🙏 💪 👌 ✨ 🔥 ❤️ 💯 🎉 🎁 ⭐'.split(' ');

const PRIORITY_LABEL: Record<string, string> = { LOW: 'کم', NORMAL: 'عادی', HIGH: 'بالا', URGENT: 'فوری' };

const ACTION_PREVIEW_LABELS: Record<string, string> = {
    SEND_REPLY: 'ارسال پاسخ', SEND_ARTICLE: 'ارسال مقاله', ADD_TAG: 'افزودن برچسب', REMOVE_TAG: 'حذف برچسب',
    SET_PRIORITY: 'تنظیم اولویت', SET_STATUS: 'تنظیم وضعیت', ASSIGN_TO_AGENT: 'واگذاری به کارشناس',
    ASSIGN_TO_TEAM: 'واگذاری به تیم', RETURN_TO_QUEUE: 'بازگشت به صف', TRANSFER_TO_TEAM: 'انتقال به تیم',
    CREATE_INTERNAL_NOTE: 'یادداشت داخلی', REQUEST_RATING: 'درخواست امتیاز', CLOSE_CONVERSATION: 'پایان گفتگو',
    REOPEN_CONVERSATION: 'بازگشایی گفتگو',
};
const PRIORITY_COLOR: Record<string, string> = {
    LOW: 'bg-gray-100 text-gray-500', NORMAL: 'bg-gray-100 text-gray-500',
    HIGH: 'bg-gold-soft text-terracotta-2', URGENT: 'bg-red-100 text-red-600',
};

function slaState(sla: ConversationSLA | null): 'none' | 'breached' | 'approaching' | 'ok' {
    if (!sla) return 'none';
    if (sla.first_response_breached_at || sla.next_response_breached_at || sla.resolution_breached_at) return 'breached';
    const dueDates = [
        !sla.first_responded_at ? sla.first_response_due_at : null,
        sla.next_response_due_at,
        !sla.resolved_at ? sla.resolution_due_at : null,
    ].filter(Boolean) as string[];
    if (dueDates.length === 0) return 'none';
    const soonest = dueDates.map(d => new Date(d).getTime()).sort((a, b) => a - b)[0];
    const minutesLeft = (soonest - Date.now()) / 60000;
    if (minutesLeft <= 15) return 'approaching';
    return 'ok';
}

function slaCountdownLabel(sla: ConversationSLA | null): string | null {
    if (!sla) return null;
    const dueDates = [
        !sla.first_responded_at ? sla.first_response_due_at : null,
        sla.next_response_due_at,
        !sla.resolved_at ? sla.resolution_due_at : null,
    ].filter(Boolean) as string[];
    if (dueDates.length === 0) return null;
    const soonest = dueDates.map(d => new Date(d).getTime()).sort((a, b) => a - b)[0];
    const minutesLeft = Math.round((soonest - Date.now()) / 60000);
    if (minutesLeft <= 0) return 'گذشته';
    if (minutesLeft < 60) return `${minutesLeft} دقیقه`;
    return `${Math.round(minutesLeft / 60)} ساعت`;
}

function initials(name?: string | null) {
    if (!name) return '؟';
    return name.trim().charAt(0);
}
function fmtTime(iso?: string) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0');
}
function fmtDuration(sec: number) {
    const s = Math.max(0, Math.round(sec || 0));
    return Math.floor(s / 60) + ':' + (s % 60).toString().padStart(2, '0');
}
function previewFor(m: LastMessage | null) {
    if (!m) return 'گفتگوی جدید';
    switch (m.message_type) {
        case 'IMAGE': return '📷 عکس';
        case 'VOICE': return '🎙️ پیام صوتی';
        case 'PRODUCT': return '🛍️ معرفی محصول';
        case 'RATING_REQUEST': return '⭐ درخواست امتیاز';
        case 'RATING': return '⭐ امتیازدهی مشتری';
        case 'ARTICLE': return '📚 مقاله پایگاه دانش';
        default: return m.content;
    }
}

function VoiceBubble({ url, duration, mine }: { url: string; duration: number; mine: boolean }) {
    const audioRef = useRef<HTMLAudioElement>(null);
    const [playing, setPlaying] = useState(false);
    const [progress, setProgress] = useState(0);
    const [time, setTime] = useState(fmtDuration(duration));
    const toggle = () => {
        const a = audioRef.current; if (!a) return;
        if (a.paused) { a.play().catch(() => {}); setPlaying(true); } else { a.pause(); setPlaying(false); }
    };
    return (
        <div className={`flex items-center gap-2 min-w-[170px] rounded-2xl px-3 py-2 ${mine ? 'bg-terracotta text-white' : 'bg-white border border-gray-200'}`}>
            <button onClick={toggle} className={`w-7 h-7 rounded-full flex-none flex items-center justify-center text-xs ${mine ? 'bg-white/25' : 'bg-gray-100'}`}>{playing ? '⏸' : '▶'}</button>
            <div className={`flex-1 h-1 rounded ${mine ? 'bg-white/30' : 'bg-gray-200'}`}>
                <div className={`h-full rounded ${mine ? 'bg-white/80' : 'bg-terracotta'}`} style={{ width: `${progress}%` }} />
            </div>
            <span className="text-[10.5px] whitespace-nowrap">{time}</span>
            <audio
                ref={audioRef} src={url} preload="none"
                onTimeUpdate={(e) => { const a = e.currentTarget; if (a.duration) setProgress((a.currentTime / a.duration) * 100); setTime(fmtDuration(a.currentTime || duration)); }}
                onEnded={() => { setPlaying(false); setProgress(0); setTime(fmtDuration(duration)); }}
            />
        </div>
    );
}

function MessageBubble({ msg }: { msg: Message }) {
    const mine = msg.sender_type === 'USER';
    const align = mine ? 'items-end self-end' : 'items-start self-start';
    const time = fmtTime(msg.created_at);
    const tick = mine ? (msg.seen ? '✓✓' : '✓') : '';

    let body: React.ReactNode = null;
    if (msg.message_type === 'IMAGE') {
        body = (
            <div className={`rounded-2xl overflow-hidden border ${mine ? 'border-terracotta-2' : 'border-gray-200'} max-w-[220px]`}>
                <img src={msg.attachment_url || ''} alt="" className="cursor-pointer" onClick={() => window.open(msg.attachment_url || '', '_blank')} />
                {msg.metadata?.caption && <div className={`text-xs px-2 py-1.5 ${mine ? 'bg-terracotta text-white' : 'bg-white'}`}>{msg.metadata.caption}</div>}
            </div>
        );
    } else if (msg.message_type === 'VOICE') {
        body = <VoiceBubble url={msg.attachment_url || ''} duration={Number(msg.metadata?.duration) || 0} mine={mine} />;
    } else if (msg.message_type === 'PRODUCT') {
        const m = msg.metadata || {};
        body = (
            <div className="w-[210px] rounded-2xl overflow-hidden border border-gray-200 bg-white text-gray-800">
                <div className="h-20 flex items-center justify-center text-white text-xl font-extrabold bg-gradient-to-br from-gold to-terracotta-2 bg-cover bg-center" style={m.image ? { backgroundImage: `url(${m.image})` } : {}}>
                    {!m.image && initials(m.brand || m.name)}
                </div>
                <div className="p-2.5">
                    <div className="text-[10px] text-gray-500 font-semibold">{m.brand}</div>
                    <div className="text-[12.5px] font-bold leading-relaxed mt-0.5">{m.name}</div>
                    <div className="text-[10.5px] text-gold mt-1">⭐ {m.rating} ({m.reviews_count} نظر)</div>
                    <div className="flex items-baseline gap-1.5 mt-1.5">
                        <span className="font-extrabold text-sm">{Number(m.price || 0).toLocaleString('fa-IR')}</span>
                        {m.old_price && <span className="text-[10.5px] text-gray-400 line-through">{Number(m.old_price).toLocaleString('fa-IR')}</span>}
                        <span className="text-[10px] text-gray-400">تومان</span>
                    </div>
                </div>
            </div>
        );
    } else if (msg.message_type === 'RATING_REQUEST') {
        body = <div className="rounded-2xl px-4 py-3 bg-gold-soft border border-gold-soft text-terracotta-2 text-xs">⭐ از مشتری درخواست امتیاز شد</div>;
    } else if (msg.message_type === 'RATING') {
        const r = Number(msg.metadata?.rating) || 0;
        body = <div className="rounded-2xl px-4 py-3 bg-gold-soft border border-gold-soft text-terracotta-2 text-xs">مشتری امتیاز داد: {'★'.repeat(r)}{'☆'.repeat(5 - r)}</div>;
    } else if (msg.message_type === 'ARTICLE') {
        const article: Partial<NonNullable<MessageMetadata['article']>> = msg.metadata?.article || {};
        body = (
            <div className="w-[230px] rounded-2xl overflow-hidden border border-gray-200 bg-white text-gray-800">
                <div className="p-2.5">
                    <div className="text-[10px] text-terracotta font-semibold">📚 {article.category || 'پایگاه دانش'}</div>
                    <div className="text-[12.5px] font-bold leading-relaxed mt-0.5">{article.title}</div>
                    {article.excerpt && <div className="text-[11px] text-gray-500 mt-1 line-clamp-2">{article.excerpt}</div>}
                    {article.url && (
                        <a href={article.url} target="_blank" rel="noreferrer" className="text-[11px] text-terracotta mt-1.5 inline-block hover:underline">
                            مشاهده مقاله ←
                        </a>
                    )}
                </div>
            </div>
        );
    } else if (msg.message_type === 'INTERNAL_NOTE') {
        body = (
            <div className="rounded-2xl px-3.5 py-2 text-sm leading-relaxed whitespace-pre-wrap break-words max-w-md bg-yellow-50 border border-yellow-200 text-yellow-900">
                <div className="text-[10px] font-bold text-yellow-700 mb-0.5">🔒 یادداشت داخلی</div>
                {msg.content}
            </div>
        );
    } else {
        body = (
            <div className={`rounded-2xl px-3.5 py-2 text-sm leading-relaxed whitespace-pre-wrap break-words max-w-md ${mine ? 'bg-terracotta text-white rounded-tl-md' : 'bg-white border border-gray-200 rounded-tr-md'}`}>
                {msg.content}
            </div>
        );
    }

    return (
        <div className={`flex flex-col ${align} max-w-[85%] md:max-w-[80%] min-w-0`}>
            {body}
            <div className="text-[10px] text-gray-400 mt-1 mx-1 flex items-center gap-1">
                {time}{tick && <span className={msg.seen ? 'text-success' : ''}>{tick}</span>}
            </div>
        </div>
    );
}

const ASSIGNMENT_ACTION_LABEL: Record<string, string> = {
    ASSIGN: 'واگذاری', REASSIGN: 'واگذاری مجدد', CLAIM: 'برداشت از صف', TRANSFER: 'انتقال تیم',
    UNASSIGN: 'لغو واگذاری', RETURN_TO_QUEUE: 'بازگشت به صف', AUTO_ASSIGN: 'واگذاری خودکار', ESCALATE: 'تشدید',
};

interface CustomerInfoPanelProps {
    selectedConv: Conversation;
    messages: Message[];
    categoryDraft: string;
    setCategoryDraft: (v: string) => void;
    handleCategoryBlur: () => void;
    workspaceTags: Tag[];
    conversationTags: Tag[];
    onToggleTag: (tag: Tag) => void;
    notes: Note[];
    newNote: string;
    setNewNote: (v: string) => void;
    onAddNote: () => void;
    customerContext: CustomerContext | null;
    assignmentHistory: AssignmentHistoryEntry[];
}

function CustomerInfoPanel({
    selectedConv, messages, categoryDraft, setCategoryDraft, handleCategoryBlur,
    workspaceTags, conversationTags, onToggleTag, notes, newNote, setNewNote, onAddNote, customerContext,
    assignmentHistory,
}: CustomerInfoPanelProps) {
    const attachedIds = new Set(conversationTags.map(t => t.id));
    return (
        <>
            <div className="border border-gray-200 rounded-xl p-4 text-center">
                <div className="w-16 h-16 mx-auto rounded-full bg-gradient-to-br from-gold to-terracotta-2 text-white flex items-center justify-center font-bold text-xl">{initials(selectedConv.visitor?.name)}</div>
                <div className="font-bold mt-2">{selectedConv.visitor?.name || 'مهمان'}</div>
                <div className="text-xs text-gray-400 mt-0.5">{customerContext?.phone || selectedConv.visitor?.mobile || selectedConv.visitor?.email || '—'}</div>
                {customerContext?.location && <div className="text-[11px] text-gray-400 mt-0.5">📍 {customerContext.location}</div>}
                {selectedConv.visitor?.created_at && <div className="text-[11px] text-gray-400 mt-2">عضویت از {new Date(selectedConv.visitor.created_at).toLocaleDateString('fa-IR')}</div>}
            </div>

            <div className="border border-gray-200 rounded-xl p-4">
                <div className="text-xs font-bold text-gray-500 mb-2">خلاصه مشتری</div>
                <div className="grid grid-cols-2 gap-2">
                    <div className="bg-gray-50 rounded-lg p-2 text-center">
                        <div className="font-extrabold text-terracotta">{messages.length}</div>
                        <div className="text-[10.5px] text-gray-400">پیام</div>
                    </div>
                    <div className="bg-gray-50 rounded-lg p-2 text-center">
                        <div className="font-extrabold text-terracotta">{selectedConv.rating ? `${selectedConv.rating}★` : '—'}</div>
                        <div className="text-[10.5px] text-gray-400">امتیاز گفتگو</div>
                    </div>
                    <div className="bg-gray-50 rounded-lg p-2 text-center">
                        <div className="font-extrabold text-terracotta">{customerContext ? customerContext.order_count : '—'}</div>
                        <div className="text-[10.5px] text-gray-400">سفارش</div>
                    </div>
                    <div className="bg-gray-50 rounded-lg p-2 text-center">
                        <div className="font-extrabold text-terracotta">{customerContext ? Number(customerContext.total_spent).toLocaleString('fa-IR') : '—'}</div>
                        <div className="text-[10.5px] text-gray-400">مجموع خرید (تومان)</div>
                    </div>
                </div>
            </div>

            {customerContext && customerContext.recent_orders.length > 0 && (
                <div className="border border-gray-200 rounded-xl p-4">
                    <div className="text-xs font-bold text-gray-500 mb-2">سفارش‌های اخیر</div>
                    <div className="flex flex-col gap-2">
                        {customerContext.recent_orders.map(o => (
                            <div key={o.id} className="flex items-center gap-2 text-xs">
                                <div className="w-8 h-8 rounded-lg bg-gray-100 flex-none bg-cover bg-center" style={o.product_image ? { backgroundImage: `url(${o.product_image})` } : {}} />
                                <div className="flex-1 min-w-0">
                                    <div className="truncate font-medium">{o.product_name}</div>
                                    <div className="text-[10.5px] text-gray-400">{Number(o.price).toLocaleString('fa-IR')} تومان</div>
                                </div>
                                <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 flex-none">{ORDER_STATUS_LABEL[o.status] || o.status}</span>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            <div className="border border-gray-200 rounded-xl p-4">
                <div className="text-xs font-bold text-gray-500 mb-2">تاریخچه واگذاری</div>
                <div className="flex flex-col gap-2 max-h-52 overflow-y-auto">
                    {assignmentHistory.length === 0 && <div className="text-[11px] text-gray-400">تاریخچه‌ای ثبت نشده</div>}
                    {assignmentHistory.map(entry => (
                        <div key={entry.id} className="text-[11px] bg-gray-50 rounded-lg p-2">
                            <div className="font-semibold">{ASSIGNMENT_ACTION_LABEL[entry.action] || entry.action}</div>
                            {entry.assigned_to_email && <div className="text-gray-500">به: {entry.assigned_to_email}</div>}
                            {entry.previous_team_name && entry.new_team_name && (
                                <div className="text-gray-500">تیم: {entry.previous_team_name} ← {entry.new_team_name}</div>
                            )}
                            {entry.reason && <div className="text-gray-400 mt-0.5">{entry.reason}</div>}
                            <div className="text-[10px] text-gray-400 mt-1">{new Date(entry.created_at).toLocaleString('fa-IR')}</div>
                        </div>
                    ))}
                </div>
            </div>

            <div className="border border-gray-200 rounded-xl p-4">
                <div className="text-xs font-bold text-gray-500 mb-2">دسته‌بندی</div>
                <input value={categoryDraft} onChange={e => setCategoryDraft(e.target.value)} onBlur={handleCategoryBlur}
                       placeholder="مثلاً: سوال قیمت" className="w-full text-xs border border-gray-200 rounded-lg px-2.5 py-2 outline-none focus:border-terracotta" />
            </div>

            <div className="border border-gray-200 rounded-xl p-4">
                <div className="text-xs font-bold text-gray-500 mb-2">برچسب‌ها</div>
                <div className="flex flex-wrap gap-1.5">
                    {workspaceTags.length === 0 && <span className="text-[11px] text-gray-400">برچسبی در این فضای کاری ثبت نشده</span>}
                    {workspaceTags.map(tag => (
                        <button key={tag.id} onClick={() => onToggleTag(tag)}
                                className={`text-[10.5px] font-semibold px-2.5 py-1 rounded-full border ${attachedIds.has(tag.id) ? 'bg-terracotta-tint text-terracotta-2 border-terracotta-soft' : 'bg-gray-50 text-gray-500 border-gray-200 hover:border-terracotta'}`}>
                            {tag.name}
                        </button>
                    ))}
                </div>
            </div>

            <div className="border border-gray-200 rounded-xl p-4 flex-1">
                <div className="text-xs font-bold text-gray-500 mb-2">یادداشت‌های اپراتور</div>
                <div className="flex flex-col gap-2 mb-2">
                    <textarea value={newNote} onChange={e => setNewNote(e.target.value)}
                              placeholder="یادداشت خصوصی درباره این مشتری…"
                              className="w-full min-h-[60px] text-xs border border-gray-200 rounded-lg px-2.5 py-2 outline-none focus:border-terracotta resize-none" />
                    <button onClick={onAddNote} disabled={!newNote.trim()} className="self-start text-[11px] font-semibold px-3 py-1.5 rounded-lg bg-terracotta text-white disabled:opacity-40">افزودن یادداشت</button>
                </div>
                <div className="flex flex-col gap-2 max-h-52 overflow-y-auto">
                    {notes.length === 0 && <div className="text-[11px] text-gray-400">یادداشتی ثبت نشده</div>}
                    {notes.map(note => (
                        <div key={note.id} className="text-xs bg-gray-50 rounded-lg p-2">
                            <div className="whitespace-pre-wrap break-words">{note.body}</div>
                            <div className="text-[10px] text-gray-400 mt-1">{note.created_by_email || 'اپراتور'} • {new Date(note.created_at).toLocaleString('fa-IR')}</div>
                        </div>
                    ))}
                </div>
            </div>
        </>
    );
}

export default function DashboardPage() {
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [selectedConv, setSelectedConv] = useState<Conversation | null>(null);
    const [messages, setMessages] = useState<Message[]>([]);
    const [input, setInput] = useState('');
    const [search, setSearch] = useState('');
    const [tab, setTab] = useState('ALL');
    const [error, setError] = useState('');
    const [toast, setToast] = useState<{ type: 'error' | 'success'; message: string } | null>(null);
    const [visitorTyping, setVisitorTyping] = useState(false);
    const [showEmoji, setShowEmoji] = useState(false);
    const [showProducts, setShowProducts] = useState(false);
    const [products, setProducts] = useState<Product[]>([]);
    const [productSearch, setProductSearch] = useState('');
    const [showKB, setShowKB] = useState(false);
    const [kbSearch, setKbSearch] = useState('');
    const [kbResults, setKbResults] = useState<KBArticleSummary[]>([]);
    const [showMacros, setShowMacros] = useState(false);
    const [macroSearch, setMacroSearch] = useState('');
    const [macroList, setMacroList] = useState<MacroSummary[]>([]);
    const [macroPreview, setMacroPreview] = useState<{ macro: MacroSummary; result: MacroPreviewResult } | null>(null);
    const [macroRunning, setMacroRunning] = useState(false);
    const [categoryDraft, setCategoryDraft] = useState('');
    const [recording, setRecording] = useState(false);
    const [recordDuration, setRecordDuration] = useState(0);
    const [uploading, setUploading] = useState(false);
    const [mobileView, setMobileView] = useState<'list' | 'chat'>('list');
    const [showMobileInfo, setShowMobileInfo] = useState(false);
const [showMobileActions, setShowMobileActions] = useState(false);
    const [workspaceTags, setWorkspaceTags] = useState<Tag[]>([]);
    const [conversationTags, setConversationTags] = useState<Tag[]>([]);
    const [notes, setNotes] = useState<Note[]>([]);
    const [newNote, setNewNote] = useState('');
    const [customerContext, setCustomerContext] = useState<CustomerContext | null>(null);
    const [teammates, setTeammates] = useState<Teammate[]>([]);
    const [assignmentHistory, setAssignmentHistory] = useState<AssignmentHistoryEntry[]>([]);

    const [teams, setTeams] = useState<Team[]>([]);
    const [queues, setQueues] = useState<Queue[]>([]);
    const [queueFilter, setQueueFilter] = useState('');
    const [teamFilter, setTeamFilter] = useState('');
    const [priorityFilter, setPriorityFilter] = useState('');
    const [slaFilter, setSlaFilter] = useState('');
    const [unassignedOnly, setUnassignedOnly] = useState(false);
    const [mineOnly, setMineOnly] = useState(false);
    const [showFilters, setShowFilters] = useState(false);
    const [showTransferPicker, setShowTransferPicker] = useState(false);

    const [noteMode, setNoteMode] = useState(false);
    const [showMentionPicker, setShowMentionPicker] = useState(false);
    const [pendingMentions, setPendingMentions] = useState<Teammate[]>([]);

    const [quickReplies, setQuickReplies] = useState<QuickReply[]>([]);
    const [showQuickReplies, setShowQuickReplies] = useState(false);
    const [quickReplySearch, setQuickReplySearch] = useState('');

    const [notifications, setNotifications] = useState<AppNotification[]>([]);
    const [unreadNotifCount, setUnreadNotifCount] = useState(0);
    const [showNotifications, setShowNotifications] = useState(false);
    const notifWsRef = useRef<WebSocket | null>(null);

    const wsRef = useRef<WebSocket | null>(null);
    const renderedIds = useRef<Set<string>>(new Set());
    const typingHideTimer = useRef<number | undefined>(undefined);
    const lastTypingSentAt = useRef(0);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const mediaRecorderRef = useRef<MediaRecorder | null>(null);
    const recordedChunksRef = useRef<BlobPart[]>([]);
    const recordCancelledRef = useRef(false);
    const recordStartedAt = useRef(0);
    const recordTimerRef = useRef<number | undefined>(undefined);
    const router = useRouter();

    useEffect(() => {
        if (!localStorage.getItem('token')) { router.push('/login'); return; }
        fetchConversations().then(setConversations).catch(() => setError('Failed to load conversations'));
        fetchProducts().then(setProducts).catch(() => {});
        fetchTags().then(setWorkspaceTags).catch(() => {});
        fetchTeams().then(setTeams).catch(() => {});
        fetchQueues().then(setQueues).catch(() => {});
        fetchQuickReplies().then(setQuickReplies).catch(() => {});
        fetchNotifications().then(setNotifications).catch(() => {});
        fetchUnreadNotificationCount().then(d => setUnreadNotifCount(d.count)).catch(() => {});

        notifWsRef.current = connectNotificationsWebSocket<{ type: string; notification?: AppNotification }>((data) => {
            if (data.type === 'notification.created' && data.notification) {
                setNotifications(prev => [data.notification as AppNotification, ...prev]);
                setUnreadNotifCount(prev => prev + 1);
            }
        });
        return () => { notifWsRef.current?.close(); };
    }, []);

    useEffect(() => { messagesEndRef.current?.scrollIntoView({ block: 'end' }); }, [messages, visitorTyping]);

    useEffect(() => {
        if (!showProducts) return;
        const t = window.setTimeout(() => {
            fetchProducts(productSearch).then(setProducts).catch(() => {});
        }, 250);
        return () => window.clearTimeout(t);
    }, [productSearch, showProducts]);

    useEffect(() => {
        if (!showKB) return;
        const t = window.setTimeout(() => {
            fetchKBArticles({ q: kbSearch || undefined, status: 'PUBLISHED' }).then(setKbResults).catch(() => setKbResults([]));
        }, 250);
        return () => window.clearTimeout(t);
    }, [kbSearch, showKB]);

    useEffect(() => {
        if (!showMacros) return;
        fetchMacros({ is_active: true }).then(setMacroList).catch(() => setMacroList([]));
    }, [showMacros]);

    const filteredMacros = macroList.filter(m =>
        !macroSearch.trim() || m.name.includes(macroSearch.trim()) || (m.category || '').includes(macroSearch.trim())
    );

    const selectedConvId = useRef<string | null>(null);

    // A message can arrive twice: once as the optimistic/REST-response render,
    // once as the live websocket echo of that same message (order is not
    // guaranteed). Both paths funnel through here so only the first write wins.
    const addIfNew = useCallback((msg: Message): boolean => {
        if (renderedIds.current.has(msg.client_message_id)) return false;
        renderedIds.current.add(msg.client_message_id);
        setMessages(prev => [...prev, msg]);
        return true;
    }, []);

    const onWsMessage = useCallback((data: WsPayload) => {
        if (data.type === 'typing') {
            if (data.sender_type === 'VISITOR') {
                setVisitorTyping(true);
                window.clearTimeout(typingHideTimer.current);
                typingHideTimer.current = window.setTimeout(() => setVisitorTyping(false), 3000);
            }
            return;
        }
        if (data.type === 'message.seen') {
            if (data.reader === 'VISITOR') {
                setMessages(prev => prev.map(m => m.sender_type === 'USER' ? { ...m, seen: true } : m));
            }
            return;
        }
        const msg = data as Message;
        if (!addIfNew(msg)) return;
        setVisitorTyping(false);
        if (msg.sender_type === 'VISITOR') {
            sendMarkReadEvent(wsRef.current);
        }
        setConversations(prev => prev.map(c => c.id === selectedConvId.current ? {
            ...c, last_message: { content: msg.content, message_type: msg.message_type, sender_type: msg.sender_type, created_at: msg.created_at },
        } : c));
    }, [addIfNew]);

    const handleSelectConv = async (conv: Conversation) => {
        selectedConvId.current = conv.id;
        setSelectedConv(conv);
        setCategoryDraft(conv.category || '');
        setMobileView('chat');
        setShowMobileInfo(false);
        setShowEmoji(false); setShowProducts(false); setVisitorTyping(false);
        setNoteMode(false); setShowMentionPicker(false); setPendingMentions([]);
        setShowQuickReplies(false); setShowTransferPicker(false);
        renderedIds.current = new Set();
        try {
            const msgs: Message[] = await fetchMessages(conv.id);
            msgs.forEach(m => renderedIds.current.add(m.client_message_id));
            setMessages(msgs);
        } catch { setMessages([]); }

        if (wsRef.current) wsRef.current.close();
        wsRef.current = connectWebSocket(conv.id, onWsMessage);

        markConversationRead(conv.id).catch(() => {});
        fetchConversationTags(conv.id).then(setConversationTags).catch(() => setConversationTags([]));
        fetchConversationNotes(conv.id).then(setNotes).catch(() => setNotes([]));
        fetchCustomerContext(conv.id).then(setCustomerContext).catch(() => setCustomerContext(null));
        fetchTeammates(conv.id).then(setTeammates).catch(() => setTeammates([]));
        fetchAssignmentHistory(conv.id).then(setAssignmentHistory).catch(() => setAssignmentHistory([]));
        setConversations(prev => prev.map(c => c.id === conv.id ? { ...c, unread_count: 0 } : c));
    };

    const toastTimer = useRef<number | undefined>(undefined);
    const notify = (type: 'error' | 'success', message: string) => {
        setToast({ type, message });
        window.clearTimeout(toastTimer.current);
        toastTimer.current = window.setTimeout(() => setToast(null), 4000);
    };

    const handleSend = async (text?: string) => {
        const content = (text ?? input).trim();
        if (!selectedConv || !content) return;
        const clientId = genClientId();
        renderedIds.current.add(clientId);
        setInput('');
        setMessages(prev => [...prev, { id: clientId, content, sender_type: 'USER', message_type: 'TEXT', metadata: {}, attachment_url: null, client_message_id: clientId, created_at: new Date().toISOString(), seen: false }]);
        try {
            await sendMessage(selectedConv.id, content, clientId);
        } catch {
            notify('error', 'ارسال پیام ناموفق بود');
        }
    };

    const handleSendNote = async () => {
        const content = input.trim();
        if (!selectedConv || !content) return;
        const clientId = genClientId();
        setInput('');
        const mentionIds = pendingMentions.map(m => m.id);
        setPendingMentions([]);
        try {
            const note = await createInternalNote(selectedConv.id, content, clientId, mentionIds);
            setMessages(prev => [...prev, { ...note, id: note.id || clientId } as Message]);
        } catch {
            notify('error', 'ثبت یادداشت داخلی ناموفق بود');
        }
    };

    const handleSendComposer = () => {
        if (noteMode) handleSendNote(); else handleSend();
    };

    const toggleMention = (teammate: Teammate) => {
        setPendingMentions(prev => prev.some(m => m.id === teammate.id)
            ? prev.filter(m => m.id !== teammate.id)
            : [...prev, teammate]);
    };

    const handleClaim = async () => {
        if (!selectedConv) return;
        try {
            const updated = await claimConversation(selectedConv.id);
            setSelectedConv(prev => prev ? { ...prev, ...updated } : prev);
            setConversations(prev => prev.map(c => c.id === updated.id ? { ...c, ...updated } : c));
        } catch (e) { notify('error', e instanceof Error ? e.message : 'برداشت گفتگو ناموفق بود'); }
    };

    const handleTransfer = async (teamId: string) => {
        if (!selectedConv) return;
        try {
            const updated = await transferConversation(selectedConv.id, teamId);
            setSelectedConv(prev => prev ? { ...prev, ...updated } : prev);
            setConversations(prev => prev.map(c => c.id === updated.id ? { ...c, ...updated } : c));
            setShowTransferPicker(false);
        } catch { notify('error', 'انتقال گفتگو ناموفق بود'); }
    };

    const handleEscalate = async () => {
        if (!selectedConv) return;
        try {
            const updated = await escalateConversation(selectedConv.id);
            setSelectedConv(prev => prev ? { ...prev, ...updated } : prev);
            setConversations(prev => prev.map(c => c.id === updated.id ? { ...c, ...updated } : c));
            notify('success', 'گفتگو تشدید شد');
        } catch { notify('error', 'تشدید گفتگو ناموفق بود'); }
    };

    const handleSetPriority = async (priority: string) => {
        if (!selectedConv) return;
        try {
            const updated = await setConversationPriority(selectedConv.id, priority);
            setSelectedConv(prev => prev ? { ...prev, ...updated } : prev);
            setConversations(prev => prev.map(c => c.id === updated.id ? { ...c, ...updated } : c));
        } catch { notify('error', 'تغییر اولویت ناموفق بود'); }
    };

    const handleUseQuickReply = async (reply: QuickReply) => {
        try {
            const res = await applyQuickReply(reply.id, selectedConv?.id);
            setInput(prev => (prev ? prev + ' ' : '') + res.body);
            setQuickReplies(prev => prev.map(q => q.id === reply.id ? { ...q, usage_count: res.usage_count } : q));
        } catch {
            setInput(prev => (prev ? prev + ' ' : '') + reply.body);
        }
        setShowQuickReplies(false);
    };

    const handleInputChange = (v: string) => {
        setInput(v);
        const t = nowMs();
        if (t - lastTypingSentAt.current > 1500) { lastTypingSentAt.current = t; sendTypingEvent(wsRef.current); }
    };

    const handleAttach = async (file: File) => {
        if (!selectedConv) return;
        const clientId = genClientId();
        setUploading(true);
        try {
            const msg = await uploadAttachment(selectedConv.id, file, 'IMAGE', clientId);
            addIfNew(msg);
        } catch { notify('error', 'آپلود عکس ناموفق بود'); } finally { setUploading(false); }
    };

    const handleShareProduct = async (p: Product) => {
        if (!selectedConv) return;
        const clientId = genClientId();
        try {
            const msg = await shareProduct(selectedConv.id, p.id, clientId);
            addIfNew(msg);
            setShowProducts(false);
        } catch { notify('error', 'ارسال محصول ناموفق بود'); }
    };

    const handleShareArticle = async (article: KBArticleSummary) => {
        if (!selectedConv) return;
        const clientId = genClientId();
        try {
            const msg = await shareKBArticle(article.id, selectedConv.id, clientId);
            addIfNew(msg);
            setShowKB(false);
        } catch { notify('error', 'ارسال مقاله ناموفق بود'); }
    };

    const handleInsertArticleLink = (article: KBArticleSummary) => {
        setInput(prev => (prev ? `${prev} ` : '') + `[${article.title}]`);
        setShowKB(false);
    };

    const handleOpenMacroPreview = async (macro: MacroSummary) => {
        if (!selectedConv) return;
        try {
            const result = await previewMacro(macro.id, selectedConv.id);
            setMacroPreview({ macro, result });
        } catch (e) { notify('error', (e as Error).message || 'پیش‌نمایش ماکرو ناموفق بود'); }
    };

    const handleConfirmMacro = async () => {
        if (!selectedConv || !macroPreview) return;
        setMacroRunning(true);
        const idempotencyKey = genIdempotencyKey();
        try {
            const execution: MacroExecutionResult = await executeMacro(macroPreview.macro.id, selectedConv.id, idempotencyKey);
            if (execution.status === 'SUCCEEDED') {
                notify('success', `ماکرو «${macroPreview.macro.name}» با موفقیت اجرا شد`);
            } else if (execution.status === 'PARTIALLY_SUCCEEDED') {
                const failed = execution.action_executions.filter(a => a.status === 'FAILED').map(a => a.action_type).join('، ');
                notify('error', `ماکرو به‌صورت ناقص اجرا شد — ناموفق: ${failed}`);
            } else {
                notify('error', 'اجرای ماکرو ناموفق بود');
            }
            setMacroPreview(null);
            setShowMacros(false);
            // Macro actions may have changed messages, priority, status, team,
            // or assignment — refresh both, mirroring the pattern every other
            // conversation-mutating action in this page already uses.
            fetchMessages(selectedConv.id).then(setMessages).catch(() => {});
            fetchConversations().then((all: Conversation[]) => {
                setConversations(all);
                const fresh = all.find(c => c.id === selectedConv.id);
                if (fresh) setSelectedConv(prev => (prev ? { ...prev, ...fresh } : prev));
            }).catch(() => {});
        } catch (e) {
            notify('error', (e as Error).message || 'اجرای ماکرو ناموفق بود');
        } finally {
            setMacroRunning(false);
        }
    };

    const handleRequestRating = async () => {
        if (!selectedConv) return;
        try {
            const msg = await requestRating(selectedConv.id);
            addIfNew(msg);
        } catch { notify('error', 'درخواست امتیاز ناموفق بود (شاید قبلاً ارسال شده)'); }
    };

    const handleToggleRecording = async () => {
        if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
            mediaRecorderRef.current.stop();
            return;
        }
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            recordedChunksRef.current = [];
            recordCancelledRef.current = false;
            const rec = new MediaRecorder(stream);
            recordStartedAt.current = nowMs();
            rec.ondataavailable = (e) => { if (e.data.size > 0) recordedChunksRef.current.push(e.data); };
            rec.onstop = async () => {
                stream.getTracks().forEach(t => t.stop());
                setRecording(false);
                setRecordDuration(0);
                window.clearInterval(recordTimerRef.current);
                if (recordCancelledRef.current) return;
                const duration = (nowMs() - recordStartedAt.current) / 1000;
                if (duration < 0.6 || !selectedConv) return;
                const blob = new Blob(recordedChunksRef.current, { type: 'audio/webm' });
                const file = new File([blob], 'voice.webm', { type: 'audio/webm' });
                const clientId = genClientId();
                setUploading(true);
                try {
                    const msg = await uploadAttachment(selectedConv.id, file, 'VOICE', clientId, { duration: String(Math.round(duration)) });
                    addIfNew(msg);
                } catch { notify('error', 'ارسال پیام صوتی ناموفق بود'); } finally { setUploading(false); }
            };
            rec.start();
            mediaRecorderRef.current = rec;
            setRecording(true);
            recordTimerRef.current = window.setInterval(() => {
                setRecordDuration(Math.round((nowMs() - recordStartedAt.current) / 1000));
            }, 500);
        } catch {
            notify('error', 'برای ارسال پیام صوتی، دسترسی به میکروفون لازم است.');
        }
    };

    const handleCancelRecording = () => {
        if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
            recordCancelledRef.current = true;
            mediaRecorderRef.current.stop();
        }
    };

    const handleAssign = async () => {
        if (!selectedConv) return;
        try {
            const updated = await assignConversation(selectedConv.id);
            setSelectedConv(prev => prev ? { ...prev, ...updated } : prev);
            setConversations(prev => prev.map(c => c.id === updated.id ? { ...c, ...updated } : c));
        } catch { notify('error', 'واگذاری گفتگو ناموفق بود'); }
    };
    const handleReassign = async (operatorId: string) => {
        if (!selectedConv || !operatorId) return;
        try {
            const updated = await assignConversation(selectedConv.id, operatorId);
            setSelectedConv(prev => prev ? { ...prev, ...updated } : prev);
            setConversations(prev => prev.map(c => c.id === updated.id ? { ...c, ...updated } : c));
        } catch { notify('error', 'واگذاری گفتگو ناموفق بود'); }
    };
    const handleClose = async () => {
        if (!selectedConv) return;
        try {
            const updated = await closeConversation(selectedConv.id);
            setSelectedConv(prev => prev ? { ...prev, ...updated } : prev);
            setConversations(prev => prev.map(c => c.id === updated.id ? { ...c, ...updated } : c));
        } catch { notify('error', 'پایان گفتگو ناموفق بود'); }
    };
    const handleReopen = async () => {
        if (!selectedConv) return;
        try {
            const updated = await reopenConversation(selectedConv.id);
            setSelectedConv(prev => prev ? { ...prev, ...updated } : prev);
            setConversations(prev => prev.map(c => c.id === updated.id ? { ...c, ...updated } : c));
            notify('success', 'گفتگو دوباره باز شد');
        } catch { notify('error', 'بازگشایی گفتگو ناموفق بود'); }
    };
    const handleCategoryBlur = async () => {
        if (!selectedConv || categoryDraft === selectedConv.category) return;
        try {
            const updated = await patchConversation(selectedConv.id, { category: categoryDraft });
            setSelectedConv(prev => prev ? { ...prev, ...updated } : prev);
            setConversations(prev => prev.map(c => c.id === updated.id ? { ...c, ...updated } : c));
        } catch { /* keep the local draft; user can retry by editing again */ }
    };

    const handleLogout = () => {
        localStorage.removeItem('token'); localStorage.removeItem('user');
        router.push('/login');
    };

    const handleToggleTag = async (tag: Tag) => {
        if (!selectedConv) return;
        const alreadyOn = conversationTags.some(t => t.id === tag.id);
        try {
            const updated = alreadyOn
                ? await detachConversationTag(selectedConv.id, tag.id)
                : await attachConversationTag(selectedConv.id, tag.id);
            setConversationTags(updated);
        } catch { notify('error', 'بروزرسانی برچسب ناموفق بود'); }
    };

    const handleAddNote = async () => {
        if (!selectedConv || !newNote.trim()) return;
        try {
            const note = await createConversationNote(selectedConv.id, newNote.trim());
            setNotes(prev => [note, ...prev]);
            setNewNote('');
        } catch { notify('error', 'ثبت یادداشت ناموفق بود'); }
    };

    const handleMarkNotificationRead = async (id: string) => {
        try {
            await markNotificationRead(id);
            setNotifications(prev => prev.map(n => n.id === id ? { ...n, read_at: new Date().toISOString() } : n));
            setUnreadNotifCount(prev => Math.max(0, prev - 1));
        } catch { /* non-critical */ }
    };

    const handleMarkAllNotificationsRead = async () => {
        try {
            await markAllNotificationsRead();
            setNotifications(prev => prev.map(n => ({ ...n, read_at: n.read_at || new Date().toISOString() })));
            setUnreadNotifCount(0);
        } catch { /* non-critical */ }
    };

    if (error) return <div className="p-4 text-red-500" dir="rtl">{error}</div>;

    const currentUserId = (() => {
        try { return JSON.parse(localStorage.getItem('user') || '{}').id as string | undefined; } catch { return undefined; }
    })();

    const filtered = conversations.filter(c => {
        if (tab !== 'ALL' && c.status !== tab) return false;
        if (queueFilter && c.queue !== queueFilter) return false;
        if (teamFilter && c.team !== teamFilter) return false;
        if (priorityFilter && c.priority !== priorityFilter) return false;
        if (unassignedOnly && c.assigned_to) return false;
        if (mineOnly && c.assigned_to?.id !== currentUserId) return false;
        if (slaFilter && slaState(c.sla) !== slaFilter) return false;
        if (!search.trim()) return true;
        const q = search.trim();
        return (c.visitor?.name || '').includes(q) || (c.subject || '').includes(q) || (c.last_message?.content || '').includes(q);
    });

    return (
        <div className="flex h-screen bg-gray-100 rastichat-mobile-viewport" dir="rtl">
            {/* Sidebar / Inbox */}
            <div className={`w-full md:w-[320px] bg-white border-l border-gray-200 flex-col ${mobileView === 'chat' ? 'hidden md:flex' : 'flex'}`}>
                <div className="p-4 border-b border-gray-200 font-bold flex items-center justify-between">
                    <span>گفتگوهای مشتریان</span>
                    <div className="flex items-center gap-2">
                        <div className="relative">
                            <button onClick={() => setShowNotifications(v => !v)} className="relative w-8 h-8 rounded-lg hover:bg-gray-100 flex items-center justify-center text-gray-600" title="اعلان‌ها">
                                🔔
                                {unreadNotifCount > 0 && <span className="absolute -top-1 -left-1 bg-terracotta text-white text-[9px] font-bold rounded-full min-w-[16px] h-4 flex items-center justify-center px-1">{unreadNotifCount}</span>}
                            </button>
                            {showNotifications && (
                                <div className="absolute left-0 top-9 bg-white border border-gray-200 rounded-xl shadow-lg w-72 max-h-80 overflow-y-auto z-20 font-normal">
                                    <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100">
                                        <span className="text-xs font-bold text-gray-600">اعلان‌ها</span>
                                        <button onClick={handleMarkAllNotificationsRead} className="text-[11px] text-terracotta hover:underline">علامت‌گذاری همه</button>
                                    </div>
                                    {notifications.length === 0 && <div className="p-4 text-center text-xs text-gray-400">اعلانی وجود ندارد</div>}
                                    {notifications.map(n => (
                                        <div key={n.id} onClick={() => !n.read_at && handleMarkNotificationRead(n.id)}
                                             className={`px-3 py-2.5 border-b border-gray-50 text-xs cursor-pointer ${n.read_at ? '' : 'bg-terracotta-tint/40'}`}>
                                            <div className="font-medium">{n.title}</div>
                                            <div className="text-[10px] text-gray-400 mt-0.5">{new Date(n.created_at).toLocaleString('fa-IR')}</div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                        <Link href="/supervisor" className="text-xs text-gray-400 hover:text-terracotta" title="داشبورد سرپرستی">📊</Link>
                        <Link href="/knowledge-base" className="text-xs text-gray-400 hover:text-terracotta" title="پایگاه دانش">📚</Link>
                        <Link href="/macros" className="text-xs text-gray-400 hover:text-terracotta" title="ماکروها">⚡</Link>
                        <Link href="/automations" className="text-xs text-gray-400 hover:text-terracotta" title="اتوماسیون‌ها">⚙️</Link>
                        <button onClick={handleLogout} className="text-xs text-gray-400 hover:text-red-500">خروج</button>
                    </div>
                </div>
                <div className="p-3 border-b border-gray-100 flex gap-2">
                    <input value={search} onChange={e => setSearch(e.target.value)} placeholder="جستجوی مشتری یا گفتگو…" className="flex-1 min-w-0 text-sm border border-gray-200 rounded-lg px-3 py-2 outline-none focus:border-terracotta" />
                    <button onClick={() => setShowFilters(v => !v)} className={`text-xs font-semibold px-2.5 rounded-lg border flex-none ${showFilters ? 'bg-terracotta text-white border-terracotta' : 'border-gray-200 text-gray-500'}`} title="فیلترها">⚙️</button>
                </div>
                {showFilters && (
                    <div className="p-3 border-b border-gray-100 grid grid-cols-2 gap-2 text-xs">
                        <select value={queueFilter} onChange={e => setQueueFilter(e.target.value)} className="border border-gray-200 rounded-lg px-2 py-1.5">
                            <option value="">همه صف‌ها</option>
                            {queues.map(q => <option key={q.id} value={q.id}>{q.name}</option>)}
                        </select>
                        <select value={teamFilter} onChange={e => setTeamFilter(e.target.value)} className="border border-gray-200 rounded-lg px-2 py-1.5">
                            <option value="">همه تیم‌ها</option>
                            {teams.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
                        </select>
                        <select value={priorityFilter} onChange={e => setPriorityFilter(e.target.value)} className="border border-gray-200 rounded-lg px-2 py-1.5">
                            <option value="">همه اولویت‌ها</option>
                            {Object.entries(PRIORITY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                        </select>
                        <select value={slaFilter} onChange={e => setSlaFilter(e.target.value)} className="border border-gray-200 rounded-lg px-2 py-1.5">
                            <option value="">وضعیت SLA</option>
                            <option value="approaching">نزدیک به موعد</option>
                            <option value="breached">نقض‌شده</option>
                        </select>
                        <label className="flex items-center gap-1.5 col-span-1">
                            <input type="checkbox" checked={unassignedOnly} onChange={e => setUnassignedOnly(e.target.checked)} />
                            واگذارنشده
                        </label>
                        <label className="flex items-center gap-1.5 col-span-1">
                            <input type="checkbox" checked={mineOnly} onChange={e => setMineOnly(e.target.checked)} />
                            واگذارشده به من
                        </label>
                    </div>
                )}
                <div className="flex gap-1 px-3 py-2 border-b border-gray-100 overflow-x-auto">
                    {TABS.map(t => (
                        <button key={t.key} onClick={() => setTab(t.key)} className={`text-xs font-semibold px-2.5 py-1.5 rounded-lg whitespace-nowrap ${tab === t.key ? 'bg-terracotta text-white' : 'text-gray-500 hover:bg-gray-100'}`}>{t.label}</button>
                    ))}
                </div>
                <div className="flex-1 overflow-y-auto">
                    {filtered.map(conv => {
                        const sla = slaState(conv.sla);
                        return (
                        <div key={conv.id} onClick={() => handleSelectConv(conv)}
                             className={`p-3 border-b border-gray-100 cursor-pointer hover:bg-gray-50 flex gap-2.5 ${selectedConv?.id === conv.id ? 'bg-terracotta-tint' : ''}`}>
                            <div className="w-10 h-10 rounded-full bg-gradient-to-br from-gold to-terracotta-2 text-white flex items-center justify-center font-bold text-sm flex-none">{initials(conv.visitor?.name)}</div>
                            <div className="flex-1 min-w-0">
                                <div className="flex items-center justify-between gap-2">
                                    <span className="font-semibold text-sm truncate">{conv.visitor?.name || 'مهمان'}</span>
                                    <span className="text-[10.5px] text-gray-400 flex-none">{fmtTime(conv.last_message?.created_at || conv.updated_at)}</span>
                                </div>
                                <div className="flex items-center justify-between gap-2 mt-0.5">
                                    <span className="text-xs text-gray-500 truncate">{previewFor(conv.last_message)}</span>
                                    {conv.unread_count > 0 && <span className="bg-terracotta text-white text-[10px] font-bold rounded-full min-w-[18px] h-[18px] flex items-center justify-center px-1 flex-none">{conv.unread_count}</span>}
                                </div>
                                <div className="mt-1 flex gap-1 flex-wrap">
                                    <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">{STATUS_LABEL[conv.status] || conv.status}</span>
                                    {conv.priority && conv.priority !== 'NORMAL' && (
                                        <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${PRIORITY_COLOR[conv.priority]}`}>{PRIORITY_LABEL[conv.priority]}</span>
                                    )}
                                    {!conv.assigned_to && <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">واگذارنشده</span>}
                                    {sla === 'breached' && <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-red-100 text-red-600">⏰ نقض SLA</span>}
                                    {sla === 'approaching' && <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-gold-soft text-terracotta-2">⏳ {slaCountdownLabel(conv.sla)}</span>}
                                    {conv.category && <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-gold-soft text-terracotta-2 border border-gold-soft truncate">{conv.category}</span>}
                                </div>
                            </div>
                        </div>
                        );
                    })}
                    {filtered.length === 0 && <div className="p-6 text-center text-sm text-gray-400">گفتگویی یافت نشد</div>}
                </div>
            </div>

            {/* Chat Area */}
            <div className={`flex-1 min-h-0 min-w-0 w-full max-w-full overflow-hidden flex-col ${mobileView === 'list' ? 'hidden md:flex' : 'flex'}`}>
                {selectedConv ? (
                    <>
                        <div className="p-2.5 md:p-3 bg-white border-b border-gray-200 flex flex-col gap-2 min-w-0 w-full max-w-full overflow-hidden">
                            <div className="flex items-center justify-between gap-1.5 min-w-0 w-full">
                                <div className="flex items-center gap-2.5 min-w-0">
                                    <button
                                        onClick={() => setMobileView('list')}
                                        className="md:hidden w-9 h-9 rounded-lg bg-gray-100 text-gray-600 flex items-center justify-center flex-none text-xl"
                                        aria-label="بازگشت به گفتگوها"
                                    >
                                        ›
                                    </button>
                                    <div className="w-8 h-8 md:w-9 md:h-9 rounded-full bg-gradient-to-br from-gold to-terracotta-2 text-white flex items-center justify-center font-bold text-sm flex-none">{initials(selectedConv.visitor?.name)}</div>
                                    <div className="min-w-0">
                                        <div className="font-bold text-sm truncate">{selectedConv.visitor?.name || 'مهمان'}</div>
                                        <div className="text-[11px] text-gray-500 flex items-center gap-1 flex-wrap">
                                            <span>{STATUS_LABEL[selectedConv.status] || selectedConv.status}</span>
                                            {selectedConv.rating && <span className="text-gold">• امتیاز {selectedConv.rating}★</span>}
                                            {selectedConv.team_name && <span>• تیم {selectedConv.team_name}</span>}
                                            {slaState(selectedConv.sla) === 'breached' && <span className="text-red-600 font-semibold">• نقض SLA</span>}
                                            {slaState(selectedConv.sla) === 'approaching' && <span className="text-terracotta-2 font-semibold">• {slaCountdownLabel(selectedConv.sla)} تا موعد</span>}
                                        </div>
                                    </div>
                                </div>
                                <div className="lg:hidden flex items-center gap-1 flex-none">
                                    <button
                                        onClick={() => setShowMobileInfo(true)}
                                        className="w-8 h-8 rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-600 flex items-center justify-center"
                                        title="اطلاعات مشتری"
                                    >
                                        ⓘ
                                    </button>
                                    <button
                                        onClick={() => setShowMobileActions(v => !v)}
                                        className="md:hidden w-8 h-8 rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-600 flex items-center justify-center text-xl leading-none"
                                        title="عملیات گفتگو"
                                        aria-label="عملیات گفتگو"
                                    >
                                        ⋮
                                    </button>
                                </div>
                            </div>
                            <div className="hidden md:flex items-center gap-2 flex-wrap justify-end">
                                <select
                                    value={selectedConv.priority}
                                    onChange={(e) => handleSetPriority(e.target.value)}
                                    className={`text-xs font-semibold px-2 py-1.5 rounded-lg border-none outline-none ${PRIORITY_COLOR[selectedConv.priority] || 'bg-gray-100 text-gray-700'}`}
                                    title="اولویت"
                                >
                                    {Object.entries(PRIORITY_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                                </select>
                                {!selectedConv.assigned_to && (
                                    <button onClick={handleClaim} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-terracotta text-white hover:bg-terracotta-2">برداشتن</button>
                                )}
                                <select
                                    value={selectedConv.assigned_to?.id || ''}
                                    onChange={(e) => handleReassign(e.target.value)}
                                    className="text-xs font-semibold px-2 py-1.5 rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-700 border-none outline-none max-w-[120px]"
                                    title="واگذاری به همکار"
                                >
                                    <option value="">واگذار نشده</option>
                                    {teammates.map(t => <option key={t.id} value={t.id}>{t.display_name}</option>)}
                                </select>
                                <button onClick={handleAssign} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-700">واگذاری به من</button>
                                <div className="relative">
                                    <button onClick={() => setShowTransferPicker(v => !v)} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-700">انتقال تیم</button>
                                    {showTransferPicker && (
                                        <div className="absolute left-0 top-9 bg-white border border-gray-200 rounded-xl shadow-lg w-44 z-20 p-1.5">
                                            {teams.filter(t => t.is_active).map(t => (
                                                <button key={t.id} onClick={() => handleTransfer(t.id)} className="w-full text-right text-xs px-2 py-1.5 rounded-lg hover:bg-gray-50">{t.name}</button>
                                            ))}
                                            {teams.length === 0 && <div className="text-[11px] text-gray-400 p-2">تیمی ثبت نشده</div>}
                                        </div>
                                    )}
                                </div>
                                <button onClick={handleEscalate} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-red-50 hover:bg-red-100 text-red-600" title="تشدید به سرپرست">🚨 تشدید</button>
                                <button onClick={handleRequestRating} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-gold-soft hover:bg-gold-soft/70 text-terracotta-2 border border-gold-soft">⭐ درخواست امتیاز</button>
                                {selectedConv.status === 'CLOSED' ? (
                                    <button onClick={handleReopen} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-success-soft hover:bg-success-soft/70 text-success">بازگشایی</button>
                                ) : (
                                    <button onClick={handleClose} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-red-50 hover:bg-red-100 text-red-600">پایان گفتگو</button>
                                )}
                            </div>

                        </div>

                        {toast && (
                            <div className={`mx-4 mt-2 text-xs rounded-lg px-3 py-2 flex items-center justify-between gap-2 border ${toast.type === 'error' ? 'bg-red-50 text-red-600 border-red-200' : 'bg-success-soft text-success border-success-soft'}`}>
                                <span>{toast.message}</span>
                                <button onClick={() => setToast(null)} className="opacity-60 hover:opacity-100">✕</button>
                            </div>
                        )}

                        <div className="flex-1 min-h-0 min-w-0 w-full max-w-full overflow-y-auto overflow-x-hidden overscroll-contain p-2.5 md:p-4 flex flex-col gap-2 bg-gray-50">
                            {messages.map(msg => <MessageBubble key={msg.id || msg.client_message_id} msg={msg} />)}
                            {visitorTyping && (
                                <div className="flex items-center gap-1 self-start bg-white border border-gray-200 rounded-2xl px-3.5 py-2.5">
                                    <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce" style={{ animationDelay: '0ms' }} />
                                    <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce" style={{ animationDelay: '150ms' }} />
                                    <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce" style={{ animationDelay: '300ms' }} />
                                </div>
                            )}
                            <div ref={messagesEndRef} />
                        </div>

                        <div className="bg-white border-t border-gray-200 p-2 md:p-3 relative sticky bottom-0 z-20 flex-none min-w-0 w-full max-w-full overflow-x-hidden pb-[max(0.5rem,env(safe-area-inset-bottom))]">
                            <div className="flex items-center gap-2 mb-2">
                                <button
                                    onClick={() => setNoteMode(v => !v)}
                                    className={`max-w-full truncate text-[11px] font-semibold px-2.5 py-1 rounded-full border ${noteMode ? 'bg-yellow-100 border-yellow-300 text-yellow-800' : 'border-gray-200 text-gray-500 hover:border-terracotta'}`}
                                >
                                    {noteMode ? '🔒 حالت یادداشت داخلی (کلیک برای بازگشت به پاسخ)' : '💬 پاسخ به مشتری (کلیک برای یادداشت داخلی)'}
                                </button>
                            </div>

                            {noteMode && pendingMentions.length > 0 && (
                                <div className="flex gap-1.5 flex-wrap mb-2">
                                    {pendingMentions.map(m => (
                                        <span key={m.id} className="text-[10.5px] font-semibold px-2 py-1 rounded-full bg-terracotta-tint text-terracotta-2 flex items-center gap-1">
                                            @{m.display_name}
                                            <button onClick={() => toggleMention(m)} className="opacity-70 hover:opacity-100">✕</button>
                                        </span>
                                    ))}
                                </div>
                            )}

                            <div className="hidden md:flex gap-1.5 overflow-x-auto pb-2">
                                {quickReplies.slice(0, 5).map(q => (
                                    <button key={q.id} onClick={() => handleUseQuickReply(q)} className="text-xs whitespace-nowrap px-2.5 py-1.5 rounded-full border border-gray-200 text-gray-600 hover:border-terracotta hover:text-terracotta">{q.title}</button>
                                ))}
                                <button onClick={() => setShowQuickReplies(v => !v)} className="text-xs whitespace-nowrap px-2.5 py-1.5 rounded-full border border-dashed border-gray-300 text-gray-500 hover:border-terracotta">همه پاسخ‌های آماده…</button>
                            </div>

                            {showQuickReplies && (
                                <div className="absolute bottom-24 right-2 left-2 md:left-auto md:right-3 bg-white border border-gray-200 rounded-xl shadow-lg p-2 w-auto md:w-80 max-h-72 overflow-y-auto z-10">
                                    <input
                                        value={quickReplySearch} onChange={(e) => setQuickReplySearch(e.target.value)} autoFocus
                                        placeholder="جستجوی پاسخ آماده…"
                                        className="w-full text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 mb-2 outline-none focus:border-terracotta"
                                    />
                                    {quickReplies.filter(q => !quickReplySearch.trim() || q.title.includes(quickReplySearch) || q.body.includes(quickReplySearch)).map(q => (
                                        <button key={q.id} onClick={() => handleUseQuickReply(q)} className="w-full text-right p-2 rounded-lg hover:bg-gray-50">
                                            <div className="text-xs font-semibold">{q.title}</div>
                                            <div className="text-[11px] text-gray-400 truncate">{q.body}</div>
                                        </button>
                                    ))}
                                    {quickReplies.length === 0 && <div className="text-xs text-gray-400 px-1 py-2">پاسخ آماده‌ای ثبت نشده</div>}
                                </div>
                            )}

                            {showMentionPicker && (
                                <div className="absolute bottom-24 right-2 left-2 md:left-auto md:right-3 bg-white border border-gray-200 rounded-xl shadow-lg p-2 w-auto md:w-64 max-h-60 overflow-y-auto z-10">
                                    <div className="text-xs font-bold text-gray-500 px-1 pb-2">اشاره به همکار</div>
                                    {teammates.map(t => (
                                        <button key={t.id} onClick={() => toggleMention(t)} className={`w-full text-right px-2 py-1.5 rounded-lg text-xs hover:bg-gray-50 ${pendingMentions.some(m => m.id === t.id) ? 'bg-terracotta-tint text-terracotta-2' : ''}`}>
                                            @{t.display_name}
                                        </button>
                                    ))}
                                </div>
                            )}

                            {showEmoji && (
                                <div className="absolute bottom-16 right-2 left-2 md:left-auto md:right-3 bg-white border border-gray-200 rounded-xl shadow-lg p-2 grid grid-cols-8 gap-1 w-auto md:w-72 z-10">
                                    {EMOJIS.map(e => (
                                        <button key={e} onClick={() => setInput(prev => prev + e)} className="text-lg p-1 rounded hover:bg-gray-100">{e}</button>
                                    ))}
                                </div>
                            )}
                            {showProducts && (
                                <div className="absolute bottom-16 right-2 left-2 md:left-auto md:right-3 bg-white border border-gray-200 rounded-xl shadow-lg p-2 w-auto md:w-80 max-h-80 overflow-y-auto z-10">
                                    <div className="text-xs font-bold text-gray-500 px-1 pb-2">معرفی محصول در گفتگو</div>
                                    <input
                                        value={productSearch} onChange={(e) => setProductSearch(e.target.value)} autoFocus
                                        placeholder="جستجوی محصول یا برند…"
                                        className="w-full text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 mb-2 outline-none focus:border-terracotta"
                                    />
                                    {products.length === 0 && <div className="text-xs text-gray-400 px-1 py-2">محصولی یافت نشد</div>}
                                    {products.map(p => (
                                        <button key={p.id} onClick={() => handleShareProduct(p)} disabled={!p.is_available} className="w-full flex items-center gap-2.5 p-2 rounded-lg hover:bg-gray-50 text-right disabled:opacity-50 disabled:cursor-not-allowed">
                                            <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-gold to-terracotta-2 flex-none flex items-center justify-center text-white text-xs font-bold bg-cover bg-center" style={p.image ? { backgroundImage: `url(${p.image})` } : {}}>{!p.image && initials(p.brand || p.name)}</div>
                                            <div className="min-w-0 flex-1">
                                                <div className="flex items-center gap-1.5">
                                                    <span className="text-xs font-semibold truncate">{p.name}</span>
                                                    {p.discount_percent > 0 && <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-success-soft text-success flex-none">٪{p.discount_percent}-</span>}
                                                </div>
                                                <div className="flex items-center gap-1.5 text-[11px] text-gray-400">
                                                    <span>{Number(p.price).toLocaleString('fa-IR')} {p.currency === 'IRT' ? 'تومان' : p.currency}</span>
                                                    {p.old_price && <span className="line-through">{Number(p.old_price).toLocaleString('fa-IR')}</span>}
                                                </div>
                                                {!p.is_available && <div className="text-[10px] text-red-500 mt-0.5">ناموجود</div>}
                                            </div>
                                        </button>
                                    ))}
                                </div>
                            )}

                            {showKB && (
                                <div className="absolute bottom-16 right-2 left-2 md:left-auto md:right-3 bg-white border border-gray-200 rounded-xl shadow-lg p-2 w-auto md:w-80 max-h-80 overflow-y-auto z-10">
                                    <div className="text-xs font-bold text-gray-500 px-1 pb-2">جستجوی پایگاه دانش</div>
                                    <input
                                        value={kbSearch} onChange={(e) => setKbSearch(e.target.value)} autoFocus
                                        placeholder="جستجو در عنوان، خلاصه و متن…"
                                        className="w-full text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 mb-2 outline-none focus:border-terracotta"
                                    />
                                    {kbResults.length === 0 && <div className="text-xs text-gray-400 px-1 py-2">مقاله‌ای یافت نشد</div>}
                                    {kbResults.map(a => (
                                        <div key={a.id} className="p-2 rounded-lg hover:bg-gray-50">
                                            <div className="text-xs font-semibold">{a.title}</div>
                                            {a.excerpt && <div className="text-[11px] text-gray-400 truncate">{a.excerpt}</div>}
                                            <div className="flex items-center gap-2 mt-1">
                                                <button onClick={() => handleShareArticle(a)} className="text-[11px] text-terracotta hover:underline">ارسال کارت مقاله</button>
                                                <button onClick={() => handleInsertArticleLink(a)} className="text-[11px] text-gray-500 hover:underline">درج در متن</button>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}

                            {showMacros && (
                                <div className="absolute bottom-16 right-2 left-2 md:left-auto md:right-3 bg-white border border-gray-200 rounded-xl shadow-lg p-2 w-auto md:w-80 max-h-80 overflow-y-auto z-10">
                                    <div className="text-xs font-bold text-gray-500 px-1 pb-2">اجرای ماکرو</div>
                                    <input
                                        value={macroSearch} onChange={(e) => setMacroSearch(e.target.value)} autoFocus
                                        placeholder="جستجوی ماکرو…"
                                        className="w-full text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 mb-2 outline-none focus:border-terracotta"
                                    />
                                    {filteredMacros.length === 0 && <div className="text-xs text-gray-400 px-1 py-2">ماکرویی یافت نشد</div>}
                                    {filteredMacros.map(m => (
                                        <button key={m.id} onClick={() => handleOpenMacroPreview(m)} className="w-full text-right p-2 rounded-lg hover:bg-gray-50">
                                            <div className="text-xs font-semibold">{m.name}</div>
                                            {m.category && <div className="text-[11px] text-gray-400">{m.category}</div>}
                                        </button>
                                    ))}
                                </div>
                            )}

                            {macroPreview && (
                                <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-20" onClick={() => !macroRunning && setMacroPreview(null)}>
                                    <div className="bg-white rounded-xl shadow-xl p-4 w-[calc(100%-2rem)] md:w-96 max-w-full max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                                        <div className="text-sm font-bold text-gray-800 mb-1">اجرای ماکرو «{macroPreview.macro.name}»</div>
                                        <div className="text-xs text-gray-400 mb-3">پیش‌نمایش اقدامات — هنوز هیچ تغییری اعمال نشده است</div>
                                        <div className="flex flex-col gap-2 mb-3">
                                            {macroPreview.result.actions.map((a, i) => (
                                                <div key={i} className="text-xs border border-gray-100 rounded-lg p-2 bg-cream/30">
                                                    <div className="font-semibold text-gray-700">{i + 1}. {ACTION_PREVIEW_LABELS[a.type] || a.type}</div>
                                                    {a.preview && <div className="text-gray-500 mt-0.5">{a.preview}</div>}
                                                    {a.article_title && <div className="text-gray-500 mt-0.5">مقاله: {a.article_title}</div>}
                                                    {a.tag_name && <div className="text-gray-500 mt-0.5">برچسب: {a.tag_name}</div>}
                                                    {a.team_name && <div className="text-gray-500 mt-0.5">تیم: {a.team_name}</div>}
                                                    {a.agent_name && <div className="text-gray-500 mt-0.5">کارشناس: {a.agent_name}</div>}
                                                </div>
                                            ))}
                                        </div>
                                        <div className="flex items-center gap-2">
                                            <button onClick={handleConfirmMacro} disabled={macroRunning} className="text-sm bg-terracotta text-white rounded-lg px-4 py-2 disabled:opacity-50">
                                                {macroRunning ? 'در حال اجرا…' : 'تأیید و اجرا'}
                                            </button>
                                            <button onClick={() => setMacroPreview(null)} disabled={macroRunning} className="text-sm text-gray-500 hover:underline">انصراف</button>
                                        </div>
                                    </div>
                                </div>
                            )}

                            {uploading && (
                                <div className="flex items-center gap-1.5 px-1 pb-1.5 text-[11px] text-gray-400">
                                    <span className="w-2.5 h-2.5 rounded-full border-2 border-gray-200 border-t-terracotta animate-spin" />
                                    <span>در حال ارسال…</span>
                                </div>
                            )}
                            <div className={`flex items-center gap-1 md:gap-1.5 border rounded-xl px-1.5 md:px-2 py-1.5 focus-within:border-terracotta min-w-0 w-full ${noteMode ? 'bg-yellow-50 border-yellow-200' : 'bg-gray-50 border-gray-200'}`}>
                                <button
                                    onClick={() => setShowQuickReplies(v => !v)}
                                    className="md:hidden w-9 h-9 rounded-lg flex items-center justify-center bg-white text-gray-600 flex-none text-xl"
                                    title="ابزارهای گفتگو"
                                    aria-label="ابزارهای گفتگو"
                                >
                                    ＋
                                </button>
                                {noteMode ? (
                                    <button onClick={() => { setShowMentionPicker(v => !v); setShowQuickReplies(false); }} className="hidden md:flex w-8 h-8 rounded-lg items-center justify-center hover:bg-white" title="اشاره به همکار">@</button>
                                ) : (
                                    <button onClick={() => { setShowProducts(v => !v); setShowEmoji(false); }} className="hidden md:flex w-8 h-8 rounded-lg items-center justify-center hover:bg-white" title="معرفی محصول">🛍️</button>
                                )}
                                <button onClick={() => { setShowEmoji(v => !v); setShowProducts(false); }} className="hidden md:flex w-8 h-8 rounded-lg items-center justify-center hover:bg-white" title="ایموجی">🙂</button>
                                <button onClick={() => { setShowKB(v => !v); setShowProducts(false); setShowEmoji(false); setShowMacros(false); }} className="hidden md:flex w-8 h-8 rounded-lg items-center justify-center hover:bg-white" title="جستجوی پایگاه دانش">📚</button>
                                <button onClick={() => { setShowMacros(v => !v); setShowProducts(false); setShowEmoji(false); setShowKB(false); }} className="hidden md:flex w-8 h-8 rounded-lg items-center justify-center hover:bg-white" title="اجرای ماکرو">⚡</button>
                                {!noteMode && <button onClick={() => fileInputRef.current?.click()} className="w-8 h-8 rounded-lg flex items-center justify-center hover:bg-white" title="پیوست عکس">📎</button>}
                                <input ref={fileInputRef} type="file" accept="image/*" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) handleAttach(f); e.target.value = ''; }} />
                                <input type="text" value={input} onChange={(e) => handleInputChange(e.target.value)}
                                       onKeyDown={(e) => { if (e.key === 'Enter') handleSendComposer(); }}
                                       className="flex-1 min-w-0 w-0 bg-transparent outline-none text-sm px-1" placeholder={noteMode ? 'یادداشت داخلی (فقط برای همکاران)…' : 'پاسخ به مشتری…'} />
                                {!noteMode && recording && (
                                    <button onClick={handleCancelRecording} className="w-8 h-8 rounded-lg flex items-center justify-center hover:bg-white text-red-500" title="لغو ضبط">✕</button>
                                )}
                                {!noteMode && (
                                    <button onClick={handleToggleRecording} className={`hidden md:flex w-8 h-8 rounded-lg items-center justify-center hover:bg-white text-xs font-semibold ${recording ? 'text-red-500 animate-pulse' : ''}`} title="پیام صوتی">
                                        {recording ? fmtDuration(recordDuration) : '🎤'}
                                    </button>
                                )}
                                <button onClick={handleSendComposer} className={`w-9 h-9 rounded-lg text-white flex items-center justify-center flex-none ${noteMode ? 'bg-yellow-600' : 'bg-terracotta'}`}>➤</button>
                            </div>
                        </div>
                    </>
                ) : (
                    <div className="flex-1 flex items-center justify-center text-gray-400">یک گفتگو را انتخاب کنید</div>
                )}
            </div>

            {selectedConv && showMobileActions && (
                <div
                    className="md:hidden fixed inset-0 z-50 bg-black/40 flex items-end"
                    onClick={() => setShowMobileActions(false)}
                >
                    <div
                        className="w-full bg-white rounded-t-3xl p-4 max-h-[78dvh] overflow-y-auto pb-[max(1rem,env(safe-area-inset-bottom))]"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="w-10 h-1 rounded-full bg-gray-300 mx-auto mb-4" />

                        <div className="flex items-center justify-between mb-4">
                            <span className="font-bold text-base">عملیات گفتگو</span>

                            <button
                                onClick={() => setShowMobileActions(false)}
                                className="w-9 h-9 rounded-full bg-gray-100 flex items-center justify-center"
                            >
                                ✕
                            </button>
                        </div>

                        <div className="grid grid-cols-2 gap-2">

                            <select
                                value={selectedConv.assigned_to?.id || ''}
                                onChange={(e) => {
                                    handleReassign(e.target.value);
                                    setShowMobileActions(false);
                                }}
                                className="col-span-2 h-12 bg-gray-100 rounded-xl px-3"
                            >
                                <option value="">واگذار نشده</option>

                                {teammates.map(t => (
                                    <option key={t.id} value={t.id}>
                                        واگذاری به {t.display_name}
                                    </option>
                                ))}
                            </select>

                            <button
                                onClick={() => {
                                    handleAssign();
                                    setShowMobileActions(false);
                                }}
                                className="h-12 rounded-xl bg-gray-100 text-sm font-semibold"
                            >
                                واگذاری به من
                            </button>

                            <button
                                onClick={() => {
                                    setShowMobileInfo(true);
                                    setShowMobileActions(false);
                                }}
                                className="h-12 rounded-xl bg-gray-100 text-sm font-semibold"
                            >
                                اطلاعات مشتری
                            </button>

                            <button
                                onClick={() => {
                                    handleEscalate();
                                    setShowMobileActions(false);
                                }}
                                className="h-12 rounded-xl bg-red-50 text-red-600 text-sm font-semibold"
                            >
                                🚨 تشدید
                            </button>

                            <button
                                onClick={() => {
                                    handleRequestRating();
                                    setShowMobileActions(false);
                                }}
                                className="h-12 rounded-xl bg-gold-soft text-terracotta-2 text-sm font-semibold"
                            >
                                ⭐ درخواست امتیاز
                            </button>

                            {selectedConv.status === 'CLOSED' ? (
                                <button
                                    onClick={() => {
                                        handleReopen();
                                        setShowMobileActions(false);
                                    }}
                                    className="col-span-2 h-12 rounded-xl bg-success-soft text-success font-semibold"
                                >
                                    بازگشایی گفتگو
                                </button>
                            ) : (
                                <button
                                    onClick={() => {
                                        handleClose();
                                        setShowMobileActions(false);
                                    }}
                                    className="col-span-2 h-12 rounded-xl bg-red-50 text-red-600 font-semibold"
                                >
                                    پایان گفتگو
                                </button>
                            )}

                        </div>

                        {teams.filter(t => t.is_active).length > 0 && (
                            <div className="mt-4">
                                <div className="text-xs font-bold text-gray-400 mb-2">
                                    انتقال به تیم
                                </div>

                                <div className="flex flex-col gap-2">
                                    {teams.filter(t => t.is_active).map(t => (
                                        <button
                                            key={t.id}
                                            onClick={() => {
                                                handleTransfer(t.id);
                                                setShowMobileActions(false);
                                            }}
                                            className="h-11 rounded-xl bg-gray-50 text-sm text-right px-4"
                                        >
                                            انتقال به {t.name}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Customer info panel — desktop/tablet-large: always-visible side panel */}
            {selectedConv && (
                <div className="hidden lg:flex w-[300px] border-r border-gray-200 bg-white flex-col overflow-y-auto p-4 gap-3">
                    <CustomerInfoPanel
                        selectedConv={selectedConv} messages={messages}
                        categoryDraft={categoryDraft} setCategoryDraft={setCategoryDraft} handleCategoryBlur={handleCategoryBlur}
                        workspaceTags={workspaceTags} conversationTags={conversationTags} onToggleTag={handleToggleTag}
                        notes={notes} newNote={newNote} setNewNote={setNewNote} onAddNote={handleAddNote}
                        customerContext={customerContext} assignmentHistory={assignmentHistory}
                    />
                </div>
            )}

            {/* Customer info panel — mobile/tablet-narrow: full-screen overlay with back control */}
            {selectedConv && showMobileInfo && (
                <div className="lg:hidden fixed inset-0 z-40 bg-white flex flex-col overflow-y-auto p-4 gap-3">
                    <div className="flex items-center justify-between border-b border-gray-200 pb-3 mb-1">
                        <span className="font-bold text-sm">پروفایل مشتری</span>
                        <button onClick={() => setShowMobileInfo(false)} className="w-8 h-8 rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-600 flex items-center justify-center" title="بازگشت">✕</button>
                    </div>
                    <CustomerInfoPanel
                        selectedConv={selectedConv} messages={messages}
                        categoryDraft={categoryDraft} setCategoryDraft={setCategoryDraft} handleCategoryBlur={handleCategoryBlur}
                        workspaceTags={workspaceTags} conversationTags={conversationTags} onToggleTag={handleToggleTag}
                        notes={notes} newNote={newNote} setNewNote={setNewNote} onAddNote={handleAddNote}
                        customerContext={customerContext} assignmentHistory={assignmentHistory}
                    />
                </div>
            )}
        </div>
    );
}
