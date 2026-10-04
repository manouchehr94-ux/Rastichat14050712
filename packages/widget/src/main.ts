export {};

declare global {
    interface Window {
        RastiChat: { init: (config: RastiChatConfig) => void; logout: () => Promise<void> };
    }
}

interface RastiChatConfig {
    projectKey: string;
    position?: 'left' | 'right';
    primaryColor?: string;
    /** Base REST URL, e.g. "https://chat.example.com/api/v1". Defaults to localhost for local development. */
    apiBase?: string;
    /** Base WebSocket URL, e.g. "wss://chat.example.com/ws". Defaults to localhost for local development. */
    wsBase?: string;
}

interface MessageMetadata {
    caption?: string;
    duration?: string | number;
    brand?: string;
    name?: string;
    price?: string | number;
    old_price?: string | number | null;
    rating?: string | number;
    reviews_count?: number;
    image?: string;
    article?: { article_id: string; title: string; excerpt: string; category: string; url: string; image_url?: string };
}

interface WireMessage {
    type?: string;
    id?: string;
    sender_type?: 'VISITOR' | 'USER' | 'SYSTEM';
    content?: string;
    message_type?: string;
    metadata?: MessageMetadata;
    attachment_url?: string | null;
    client_message_id?: string;
    created_at?: string;
    seen?: boolean;
    reader?: string;
    branding?: Branding;
}

interface ConsultantBranding {
    display_name: string;
    avatar_url: string;
    title: string;
    status: 'ONLINE' | 'AWAY' | 'OFFLINE';
    response_time_label: string | null;
    rating: number | null;
}

interface Branding {
    store: { name: string; logo_url: string; subtitle: string };
    consultant: ConsultantBranding | null;
    workspace_online: boolean;
}

const EMOJIS = '😀 😊 😉 😍 🤩 😎 🤔 😴 😢 😅 😇 😂 😘 😋 🤗 🤝 🙏 💪 👌 ✨ 🔥 ❤️ 💯 🎉 🎁 🛒 ⭐ 🌹 🌿 ☕'.split(' ');
const QUICK_REPLIES = ['💎 سوال درباره قیمت', '🚚 شرایط ارسال', '🛡️ گارانتی کالا', '🎁 کدهای تخفیف'];

function escapeHtml(s: string): string {
    const div = document.createElement('div');
    div.textContent = s ?? '';
    return div.innerHTML;
}

function fmtTime(iso?: string): string {
    const d = iso ? new Date(iso) : new Date();
    return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0');
}

function fmtDuration(sec: number): string {
    const s = Math.max(0, Math.round(sec));
    return Math.floor(s / 60) + ':' + (s % 60).toString().padStart(2, '0');
}

class RastiChatWidget {
    private config: RastiChatConfig;
    private container: HTMLElement;
    private panel!: HTMLElement;
    private launcher!: HTMLElement;
    private messagesContainer!: HTMLElement;
    private inputField!: HTMLInputElement;
    private composerBar!: HTMLElement;
    private emojiPop!: HTMLElement;
    private fileInput!: HTMLInputElement;
    private micBtn!: HTMLElement;
    private micCancelBtn!: HTMLElement;
    private badge!: HTMLElement;
    private avatarEl!: HTMLElement;
    private statusDotEl!: HTMLElement;
    private titleEl!: HTMLElement;
    private subtitleEl!: HTMLElement;
    private offlineBanner!: HTMLElement;
    private uploadStatus!: HTMLElement;
    private uploadLabel!: HTMLElement;
    private noticeEl!: HTMLElement;
    private noticeHideTimer: number | undefined;

    private ws: WebSocket | null = null;
    private sessionToken: string | null = null;
    private convId: string | null = null;
    private apiBase = 'http://localhost:8080/api/v1';
    private wsBase = 'ws://localhost:8080/ws';

    private renderedIds = new Set<string>();
    private isOpen = false;
    private unreadCount = 0;
    private typingHideTimer: number | undefined;
    private lastTypingSentAt = 0;

    private mediaRecorder: MediaRecorder | null = null;
    private recordedChunks: BlobPart[] = [];
    private recordStartedAt = 0;
    private recordTimer: number | undefined;
    private recordCancelled = false;
    private branding: Branding | null = null;
    private recoveringSession = false;

    constructor(config: RastiChatConfig) {
        this.config = { position: 'right', primaryColor: '#BC5A38', ...config };
        if (config.apiBase) this.apiBase = config.apiBase.replace(/\/$/, '');
        if (config.wsBase) this.wsBase = config.wsBase.replace(/\/$/, '');
        this.container = document.createElement('div');
        this.container.id = 'rasti-container';
        document.body.appendChild(this.container);

        this.initUI();
        this.initEvents();
        this.initSession();
    }

    private loadFont() {
        // Best-effort: a slow/blocked font request must never break the widget.
        if (document.getElementById('rasti-font-link')) return;
        const link = document.createElement('link');
        link.id = 'rasti-font-link';
        link.rel = 'stylesheet';
        link.href = 'https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;500;600;700;800&display=swap';
        document.head.appendChild(link);
    }

    private initUI() {
        this.loadFont();
        const pos = this.config.position;
        this.container.innerHTML = `
            <style>
                /* Design tokens mirrored from docs/product/DESIGN_TOKENS.md — keep in sync with apps/operator-dashboard/src/app/globals.css */
                #rasti-container * { box-sizing: border-box; font-family: 'Vazirmatn', Tahoma, Arial, sans-serif; }
                #rasti-container { direction: rtl; }
                #rasti-launcher {
                    position: fixed; bottom: 20px; ${pos}: 20px;
                    width: 60px; height: 60px; background: ${this.config.primaryColor};
                    border-radius: 50%; cursor: pointer; box-shadow: 0 6px 18px rgba(0,0,0,0.25);
                    display: flex; align-items: center; justify-content: center; color: white; font-size: 26px; z-index: 9998;
                    transition: transform .15s;
                }
                #rasti-launcher:hover { transform: scale(1.06); }
                #rasti-launcher .rasti-badge {
                    position: absolute; top: -4px; ${pos === 'right' ? 'left' : 'right'}: -4px; background: #C0504A; color: #fff;
                    font-size: 11px; font-weight: 700; min-width: 19px; height: 19px; border-radius: 999px;
                    display: none; align-items: center; justify-content: center; padding: 0 4px; border: 2px solid #fff;
                }
                #rasti-panel {
                    position: fixed; bottom: 92px; ${pos}: 20px;
                    width: 360px; height: 520px; max-height: calc(100vh - 120px); background: #FAF3EA; border-radius: 18px;
                    box-shadow: 0 20px 50px rgba(0,0,0,0.28); display: none; flex-direction: column; z-index: 9999; overflow: hidden;
                    border: 1px solid #ECDCC8;
                }
                #rasti-panel.open { display: flex; }
                #rasti-header {
                    background: linear-gradient(135deg, ${this.config.primaryColor}, #A1492A); color: white; padding: 14px 16px;
                    display: flex; align-items: center; gap: 10px; flex: none;
                }
                #rasti-header .rasti-avatar {
                    width: 36px; height: 36px; border-radius: 50%; background: rgba(255,255,255,.22);
                    display: flex; align-items: center; justify-content: center; font-weight: 700; position: relative; flex: none;
                }
                #rasti-header .rasti-avatar .dot {
                    position: absolute; bottom: -1px; left: -1px; width: 10px; height: 10px; border-radius: 50%;
                    background: #5E8A56; border: 2px solid ${this.config.primaryColor};
                }
                #rasti-header .rasti-htext { min-width: 0; flex: 1; }
                #rasti-header b { display: block; font-size: 13.5px; }
                #rasti-header span { font-size: 11px; opacity: .85; }
                #rasti-close { cursor: pointer; opacity: .85; font-size: 18px; padding: 4px; }
                #rasti-close:hover { opacity: 1; }
                #rasti-messages { flex: 1; padding: 14px; overflow-y: auto; background: #FBF4EB; display: flex; flex-direction: column; gap: 3px; }
                .rasti-msg { display: flex; flex-direction: column; max-width: 82%; }
                .rasti-msg.visitor { align-self: flex-start; align-items: flex-start; }
                .rasti-msg.operator { align-self: flex-end; align-items: flex-end; }
                .rasti-bubble { padding: 9px 12px; border-radius: 16px; font-size: 13px; line-height: 1.7; word-wrap: break-word; box-shadow: 0 4px 12px -8px rgba(0,0,0,.25); }
                .rasti-msg.visitor .rasti-bubble { background: #fff; color: #2C211A; border: 1px solid #ECDCC8; border-top-left-radius: 5px; }
                .rasti-msg.operator .rasti-bubble { background: linear-gradient(135deg, ${this.config.primaryColor}, #9F4427); color: #fff; border-top-right-radius: 5px; }
                .rasti-meta { font-size: 10px; color: #A08C77; margin: 3px 6px 0; display: flex; align-items: center; gap: 3px; }
                .rasti-tick { font-size: 11px; color: #A08C77; }
                .rasti-tick.seen { color: #5E8A56; }
                .rasti-bubble.rasti-img img { max-width: 200px; border-radius: 10px; display: block; cursor: pointer; }
                .rasti-bubble.rasti-img .cap { margin-top: 5px; font-size: 12px; }
                .rasti-bubble.rasti-voice { display: flex; align-items: center; gap: 8px; min-width: 170px; padding: 8px 12px; }
                .rasti-vplay { width: 26px; height: 26px; border-radius: 50%; border: none; background: rgba(0,0,0,.08); cursor: pointer; flex: none; font-size: 11px; }
                .rasti-msg.operator .rasti-vplay { background: rgba(255,255,255,.25); color: #fff; }
                .rasti-vbar { flex: 1; height: 4px; border-radius: 4px; background: rgba(0,0,0,.12); overflow: hidden; }
                .rasti-msg.operator .rasti-vbar { background: rgba(255,255,255,.3); }
                .rasti-vfill { height: 100%; width: 0%; background: currentColor; opacity: .6; }
                .rasti-vdur { font-size: 10.5px; white-space: nowrap; }
                .rasti-bubble.rasti-product { padding: 0; overflow: hidden; width: 210px; background: #fff; color: #2C211A; border: 1px solid #ECDCC8; }
                .rasti-p-img { height: 90px; background: linear-gradient(135deg,#C2954A,${this.config.primaryColor}); display: flex; align-items: center; justify-content: center; color: #fff; font-size: 26px; font-weight: 800; background-size: cover; background-position: center; }
                .rasti-p-body { padding: 9px 11px 11px; }
                .rasti-p-brand { font-size: 10px; color: #A08C77; font-weight: 600; }
                .rasti-p-name { font-size: 12.5px; font-weight: 700; margin-top: 2px; line-height: 1.5; }
                .rasti-p-rate { font-size: 10.5px; color: #C2954A; margin-top: 4px; }
                .rasti-p-price { display: flex; align-items: baseline; gap: 6px; margin-top: 6px; }
                .rasti-p-now { font-weight: 800; font-size: 13.5px; }
                .rasti-p-old { font-size: 10.5px; color: #A08C77; text-decoration: line-through; }
                .rasti-bubble.rasti-rating { width: 220px; text-align: center; background: #fff; color: #2C211A; }
                .rasti-r-title { font-size: 12.5px; font-weight: 600; line-height: 1.7; }
                .rasti-r-stars { display: flex; justify-content: center; gap: 6px; margin-top: 10px; }
                .rasti-r-stars button { background: none; border: none; font-size: 20px; cursor: pointer; color: #ddd2c2; padding: 0; }
                .rasti-r-stars button.on { color: #C2954A; }
                .rasti-r-thanks { font-size: 11.5px; color: #5E8A56; font-weight: 600; margin-top: 8px; }
                .rasti-bubble.rasti-article { padding: 0; overflow: hidden; width: 230px; background: #fff; color: #2C211A; border: 1px solid #ECDCC8; }
                .rasti-a-img { height: 80px; background-color: #F3E8D8; background-size: cover; background-position: center; }
                .rasti-a-body { padding: 9px 11px 11px; }
                .rasti-a-cat { font-size: 10px; color: ${this.config.primaryColor}; font-weight: 600; }
                .rasti-a-title { font-size: 12.5px; font-weight: 700; margin-top: 2px; line-height: 1.5; }
                .rasti-a-excerpt { font-size: 11px; color: #A08C77; margin-top: 4px; line-height: 1.5; }
                .rasti-a-link { display: inline-block; font-size: 11px; color: ${this.config.primaryColor}; margin-top: 6px; text-decoration: none; }
                .rasti-a-link:hover { text-decoration: underline; }
                #rasti-typing { align-self: flex-start; display: none; }
                #rasti-typing .rasti-bubble { display: flex; gap: 4px; align-items: center; padding: 11px 13px; }
                #rasti-typing span { width: 5px; height: 5px; border-radius: 50%; background: #A08C77; animation: rasti-bob 1.2s infinite; }
                #rasti-typing span:nth-child(2) { animation-delay: .15s; }
                #rasti-typing span:nth-child(3) { animation-delay: .3s; }
                @keyframes rasti-bob { 0%,60%,100%{transform:translateY(0);opacity:.5} 30%{transform:translateY(-4px);opacity:1} }
                #rasti-quick { display: flex; gap: 6px; overflow-x: auto; padding: 8px 10px 0; scrollbar-width: none; flex: none; }
                #rasti-quick::-webkit-scrollbar { display: none; }
                .rasti-chip { white-space: nowrap; font-size: 11.5px; padding: 6px 11px; border-radius: 999px; background: #fff; border: 1px solid #ECDCC8; color: #5C4A3A; cursor: pointer; flex: none; }
                .rasti-chip:hover { border-color: ${this.config.primaryColor}; color: ${this.config.primaryColor}; }
                #rasti-input-area { padding: 8px 10px 10px; background: #FAF3EA; border-top: 1px solid #ECDCC8; flex: none; position: relative; }
                #rasti-bar { display: flex; align-items: center; gap: 5px; background: #fff; border: 1.5px solid #ECDCC8; border-radius: 14px; padding: 5px 6px; }
                .rasti-act { width: 32px; height: 32px; border-radius: 9px; border: none; background: none; cursor: pointer; font-size: 16px; display: flex; align-items: center; justify-content: center; color: #5C4A3A; flex: none; }
                .rasti-act:hover { background: #FBF4EB; }
                .rasti-act.recording { color: #C0504A; animation: rasti-pulse 1s infinite; }
                @keyframes rasti-pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
                #rasti-input { flex: 1; border: none; outline: none; font-size: 13px; padding: 6px 2px; min-width: 0; background: transparent; }
                #rasti-send { background: ${this.config.primaryColor}; color: white; border: none; width: 32px; height: 32px; border-radius: 10px; cursor: pointer; flex: none; display: flex; align-items: center; justify-content: center; }
                .rasti-pop { position: absolute; bottom: 58px; ${pos}: 0; background: #fff; border: 1px solid #ECDCC8; border-radius: 14px; padding: 8px; box-shadow: 0 12px 30px rgba(0,0,0,.2); width: 240px; display: none; z-index: 2; }
                .rasti-pop.open { display: block; }
                .rasti-emo-grid { display: grid; grid-template-columns: repeat(7,1fr); gap: 2px; max-height: 160px; overflow: auto; }
                .rasti-emo-grid button { font-size: 17px; border: none; background: none; cursor: pointer; padding: 4px; border-radius: 8px; }
                .rasti-emo-grid button:hover { background: #FBF4EB; }
                #rasti-offline-banner {
                    display: none; background: #F3DCC9; color: #8a4a24; font-size: 11.5px; text-align: center;
                    padding: 6px 10px; flex: none;
                }
                #rasti-offline-banner.show { display: block; }
                #rasti-notice {
                    display: none; background: #F6D9D3; color: #8a2f22; font-size: 11.5px; text-align: center;
                    padding: 6px 10px; flex: none;
                }
                #rasti-notice.show { display: block; }
                #rasti-upload-status { display: none; align-items: center; gap: 6px; font-size: 11px; color: #A08C77; padding: 0 2px 6px; }
                #rasti-upload-status.show { display: flex; }
                .rasti-spinner { width: 11px; height: 11px; border-radius: 50%; border: 2px solid #ECDCC8; border-top-color: ${this.config.primaryColor}; animation: rasti-spin .7s linear infinite; flex: none; }
                @keyframes rasti-spin { to { transform: rotate(360deg); } }
                .rasti-act.rasti-cancel-rec { display: none; color: #C0504A; }
                .rasti-act.rasti-cancel-rec.show { display: flex; }
                @media (max-width: 480px) {
                    #rasti-launcher { bottom: max(16px, env(safe-area-inset-bottom)); }
                    #rasti-panel {
                        width: 100vw; height: 100dvh; max-height: 100dvh; bottom: 0; ${pos}: 0;
                        border-radius: 0; border: none;
                    }
                    #rasti-header { padding-top: max(14px, env(safe-area-inset-top)); }
                    #rasti-input { font-size: 16px; }
                    #rasti-input-area { padding-bottom: max(10px, env(safe-area-inset-bottom)); }
                    .rasti-bubble.rasti-product { width: min(210px, 78vw); }
                    .rasti-bubble.rasti-rating { width: min(220px, 78vw); }
                    .rasti-bubble.rasti-voice { min-width: min(170px, 60vw); }
                }
                .rasti-pop { width: min(240px, calc(100vw - 20px)); }
            </style>
            <div id="rasti-launcher">💬<span class="rasti-badge" id="rasti-badge"></span></div>
            <div id="rasti-panel">
                <div id="rasti-header">
                    <div class="rasti-avatar" id="rasti-avatar">🙂<span class="dot" id="rasti-status-dot"></span></div>
                    <div class="rasti-htext"><b id="rasti-title">پشتیبانی آنلاین</b><span id="rasti-subtitle">معمولاً در چند دقیقه پاسخ می‌دهیم</span></div>
                    <div id="rasti-close">✕</div>
                </div>
                <div id="rasti-offline-banner">در حال اتصال مجدد…</div>
                <div id="rasti-notice"></div>
                <div id="rasti-messages"></div>
                <div id="rasti-quick"></div>
                <div id="rasti-input-area">
                    <div class="rasti-pop" id="rasti-emoji-pop"></div>
                    <div id="rasti-upload-status"><span class="rasti-spinner"></span><span id="rasti-upload-label">در حال ارسال…</span></div>
                    <div id="rasti-bar">
                        <button class="rasti-act" id="rasti-emoji-btn" type="button" title="ایموجی">🙂</button>
                        <button class="rasti-act" id="rasti-attach-btn" type="button" title="ارسال عکس">📎</button>
                        <input id="rasti-input" type="text" placeholder="پیام خود را بنویسید..." autocomplete="off" />
                        <button class="rasti-act rasti-cancel-rec" id="rasti-mic-cancel" type="button" title="لغو ضبط">✕</button>
                        <button class="rasti-act" id="rasti-mic-btn" type="button" title="ارسال پیام صوتی">🎤</button>
                        <button id="rasti-send" type="button" title="ارسال">➤</button>
                    </div>
                    <input type="file" id="rasti-file" accept="image/*" hidden />
                </div>
            </div>
        `;
        this.launcher = document.getElementById('rasti-launcher')!;
        this.panel = document.getElementById('rasti-panel')!;
        this.messagesContainer = document.getElementById('rasti-messages')!;
        this.inputField = document.getElementById('rasti-input') as HTMLInputElement;
        this.composerBar = document.getElementById('rasti-bar')!;
        this.emojiPop = document.getElementById('rasti-emoji-pop')!;
        this.fileInput = document.getElementById('rasti-file') as HTMLInputElement;
        this.micBtn = document.getElementById('rasti-mic-btn')!;
        this.micCancelBtn = document.getElementById('rasti-mic-cancel')!;
        this.badge = document.getElementById('rasti-badge')!;
        this.avatarEl = document.getElementById('rasti-avatar')!;
        this.statusDotEl = document.getElementById('rasti-status-dot')!;
        this.titleEl = document.getElementById('rasti-title')!;
        this.subtitleEl = document.getElementById('rasti-subtitle')!;
        this.offlineBanner = document.getElementById('rasti-offline-banner')!;
        this.uploadStatus = document.getElementById('rasti-upload-status')!;
        this.uploadLabel = document.getElementById('rasti-upload-label')!;
        this.noticeEl = document.getElementById('rasti-notice')!;

        this.emojiPop.innerHTML = `<div class="rasti-emo-grid">${EMOJIS.map(e => `<button type="button">${e}</button>`).join('')}</div>`;

        const quick = document.getElementById('rasti-quick')!;
        quick.innerHTML = QUICK_REPLIES.map(q => `<button type="button" class="rasti-chip">${q}</button>`).join('');
        quick.querySelectorAll('.rasti-chip').forEach(btn => {
            btn.addEventListener('click', () => {
                this.inputField.value = (btn.textContent || '').replace(/^[^؀-ۿ]+/, '').trim();
                this.inputField.focus();
            });
        });

        const typingRow = document.createElement('div');
        typingRow.id = 'rasti-typing';
        typingRow.className = 'rasti-msg visitor';
        typingRow.innerHTML = `<div class="rasti-bubble"><span></span><span></span><span></span></div>`;
        this.messagesContainer.appendChild(typingRow);
    }

    private initEvents() {
        this.launcher.addEventListener('click', () => this.togglePanel());
        document.getElementById('rasti-close')!.addEventListener('click', () => this.togglePanel(false));

        document.getElementById('rasti-send')!.addEventListener('click', () => this.sendMessage());
        this.inputField.addEventListener('keypress', (e) => { if (e.key === 'Enter') this.sendMessage(); });
        this.inputField.addEventListener('input', () => this.sendTyping());

        document.getElementById('rasti-emoji-btn')!.addEventListener('click', (e) => {
            e.stopPropagation();
            this.emojiPop.classList.toggle('open');
        });
        this.emojiPop.addEventListener('click', (e) => {
            const btn = (e.target as HTMLElement).closest('button');
            if (!btn) return;
            this.inputField.value += btn.textContent;
            this.inputField.focus();
        });
        document.addEventListener('click', (e) => {
            if (!(e.target as HTMLElement).closest('#rasti-emoji-btn') && !(e.target as HTMLElement).closest('#rasti-emoji-pop')) {
                this.emojiPop.classList.remove('open');
            }
        });

        document.getElementById('rasti-attach-btn')!.addEventListener('click', () => this.fileInput.click());
        this.fileInput.addEventListener('change', () => {
            const file = this.fileInput.files?.[0];
            if (file) this.uploadFile(file, 'IMAGE');
            this.fileInput.value = '';
        });

        this.micBtn.addEventListener('click', () => this.toggleRecording());
        this.micCancelBtn.addEventListener('click', () => this.cancelRecording());
    }

    private togglePanel(force?: boolean) {
        this.isOpen = force ?? !this.isOpen;
        this.panel.classList.toggle('open', this.isOpen);
        if (typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 480px)').matches) {
            document.body.style.overflow = this.isOpen ? 'hidden' : '';
        }
        if (this.isOpen) {
            this.unreadCount = 0;
            this.updateBadge();
            this.sendMarkRead();
            this.scrollToBottom();
        }
    }

    private updateBadge() {
        if (this.unreadCount > 0) {
            this.badge.style.display = 'flex';
            this.badge.textContent = String(this.unreadCount);
        } else {
            this.badge.style.display = 'none';
        }
    }

    private showNotice(text: string) {
        window.clearTimeout(this.noticeHideTimer);
        this.noticeEl.textContent = text;
        this.noticeEl.classList.add('show');
        this.noticeHideTimer = window.setTimeout(() => this.noticeEl.classList.remove('show'), 4000);
    }

    private applyBranding(branding?: Branding | null) {
        if (!branding) return;
        this.branding = branding;
        const store = branding.store;
        const consultant = branding.consultant;

        if (consultant) {
            this.titleEl.textContent = consultant.display_name;
            this.subtitleEl.textContent = consultant.title || store.subtitle || 'پشتیبانی آنلاین';
            this.avatarEl.style.backgroundImage = consultant.avatar_url ? `url('${consultant.avatar_url}')` : '';
            this.avatarEl.style.backgroundSize = 'cover';
            this.avatarEl.style.backgroundPosition = 'center';
            this.avatarEl.firstChild!.textContent = consultant.avatar_url ? '' : (consultant.display_name.trim().charAt(0) || '؟');
            const dotColor = consultant.status === 'ONLINE' ? '#5E8A56' : consultant.status === 'AWAY' ? '#C2954A' : '#A08C77';
            this.statusDotEl.style.background = dotColor;
        } else {
            this.titleEl.textContent = store.name || 'پشتیبانی آنلاین';
            this.subtitleEl.textContent = store.subtitle || 'معمولاً در چند دقیقه پاسخ می‌دهیم';
            this.avatarEl.style.backgroundImage = store.logo_url ? `url('${store.logo_url}')` : '';
            this.avatarEl.style.backgroundSize = 'cover';
            this.avatarEl.style.backgroundPosition = 'center';
            this.avatarEl.firstChild!.textContent = store.logo_url ? '' : (store.name || '؟').trim().charAt(0);
            this.statusDotEl.style.background = branding.workspace_online ? '#5E8A56' : '#A08C77';
        }
    }

    private async initSession() {
        try {
            const stored = localStorage.getItem('rasti_session');
            if (stored) {
                this.sessionToken = stored;
            } else {
                await this.createSession();
            }
            await this.startChat();
        } catch (error) {
            console.error("RastiChat init failed", error);
        }
    }

    private async createSession() {
        const res = await fetch(`${this.apiBase}/widget/init/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_key: this.config.projectKey })
        });
        const data = await res.json();
        this.sessionToken = data.session_token;
        localStorage.setItem('rasti_session', this.sessionToken!);
    }

    /**
     * The server says our session is unknown / expired / revoked (HTTP 401).
     * Drop the dead credential and open a fresh guest session — once, so an
     * unreachable or misconfigured server can never loop us.
     */
    private async recoverFromInvalidSession(): Promise<boolean> {
        if (this.recoveringSession) return false;
        this.recoveringSession = true;
        try {
            localStorage.removeItem('rasti_session');
            this.sessionToken = null;
            this.convId = null;
            this.dropSocket();
            this.renderedIds.clear();
            await this.createSession();
            return true;
        } catch (error) {
            console.error("RastiChat session recovery failed", error);
            return false;
        } finally {
            this.recoveringSession = false;
        }
    }

    /** Close the socket without letting its onclose handler schedule a reconnect. */
    private dropSocket() {
        if (!this.ws) return;
        this.ws.onclose = null;
        this.ws.close();
        this.ws = null;
    }

    /** Swap the stored session token for a fresh one (the server marks it due). */
    private async rotateSession() {
        if (!this.sessionToken) return;
        try {
            const res = await fetch(`${this.apiBase}/widget/session/rotate/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_token: this.sessionToken })
            });
            if (!res.ok) return;
            const data = await res.json();
            if (!data.session_token) return;
            this.sessionToken = data.session_token;
            localStorage.setItem('rasti_session', this.sessionToken!);
        } catch (error) {
            console.error("RastiChat session rotation failed", error);
        }
    }

    /** Customer logout: revoke the session server-side and forget it locally. */
    public async logout() {
        const token = this.sessionToken;
        localStorage.removeItem('rasti_session');
        this.sessionToken = null;
        this.convId = null;
        this.dropSocket();
        if (!token) return;
        try {
            await fetch(`${this.apiBase}/widget/session/revoke/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_token: token })
            });
        } catch (error) {
            console.error("RastiChat logout failed", error);
        }
    }

    private async startChat(allowRecovery = true) {
        try {
            const res = await fetch(`${this.apiBase}/widget/start/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_token: this.sessionToken })
            });
            if (res.status === 401 && allowRecovery) {
                if (await this.recoverFromInvalidSession()) await this.startChat(false);
                return;
            }
            const data = await res.json();
            this.convId = data.id;
            this.applyBranding(data.branding);
            if (data.rotate_session) await this.rotateSession();
            this.connectWebSocket();
            await this.loadHistory();
        } catch (error) {
            console.error("RastiChat start failed", error);
        }
    }

    private async loadHistory() {
        if (!this.convId || !this.sessionToken) return;
        try {
            const res = await fetch(`${this.apiBase}/widget/conversations/${this.convId}/messages/?session_token=${this.sessionToken}`);
            if (res.status === 401) { if (await this.recoverFromInvalidSession()) await this.startChat(false); return; }
            if (!res.ok) return;
            const msgs: WireMessage[] = await res.json();
            msgs.forEach(m => this.renderIncoming(m));
            this.scrollToBottom();
        } catch (error) {
            console.error("RastiChat history load failed", error);
        }
    }

    private connectWebSocket() {
        if (!this.convId || !this.sessionToken) return;

        this.ws = new WebSocket(`${this.wsBase}/widget/${this.sessionToken}/${this.convId}/`);

        this.ws.onopen = () => {
            this.offlineBanner.classList.remove('show');
        };

        this.ws.onmessage = (event) => {
            const data: WireMessage = JSON.parse(event.data);
            if (data.type === 'typing') {
                if (data.sender_type === 'USER') this.showTyping();
                return;
            }
            if (data.type === 'message.seen') {
                if (data.reader === 'USER') this.markAllOutgoingSeen();
                return;
            }
            if (data.type === 'branding.updated') {
                this.applyBranding(data.branding);
                return;
            }
            this.hideTyping();
            this.renderIncoming(data);
            if (data.sender_type === 'USER') {
                if (this.isOpen) {
                    this.sendMarkRead();
                } else {
                    this.unreadCount += 1;
                    this.updateBadge();
                }
            }
            this.scrollToBottom();
        };

        this.ws.onclose = () => {
            this.offlineBanner.classList.add('show');
            setTimeout(() => this.connectWebSocket(), 2000);
        };
    }

    private showTyping() {
        const el = document.getElementById('rasti-typing')!;
        el.style.display = 'flex';
        this.messagesContainer.appendChild(el);
        this.scrollToBottom();
        window.clearTimeout(this.typingHideTimer);
        this.typingHideTimer = window.setTimeout(() => this.hideTyping(), 3000);
    }

    private hideTyping() {
        window.clearTimeout(this.typingHideTimer);
        const el = document.getElementById('rasti-typing');
        if (el) el.style.display = 'none';
    }

    private sendTyping() {
        const now = Date.now();
        if (now - this.lastTypingSentAt < 1500) return;
        this.lastTypingSentAt = now;
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'typing' }));
        }
    }

    private sendMarkRead() {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'mark_read' }));
        }
    }

    private markAllOutgoingSeen() {
        this.messagesContainer.querySelectorAll('.rasti-msg.visitor .rasti-tick').forEach(el => el.classList.add('seen'));
    }

    private sendMessage() {
        const text = this.inputField.value.trim();
        if (!text || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;

        const clientId = 'msg_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
        this.ws.send(JSON.stringify({ message: text, client_message_id: clientId }));

        this.renderIncoming({
            sender_type: 'VISITOR', content: text, message_type: 'TEXT',
            client_message_id: clientId, created_at: new Date().toISOString(), seen: false,
        });
        this.inputField.value = '';
        this.scrollToBottom();
    }

    private async uploadFile(file: File, messageType: 'IMAGE' | 'VOICE', extra?: Record<string, string>) {
        if (!this.convId || !this.sessionToken) return;
        const clientId = 'msg_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
        const form = new FormData();
        form.append('session_token', this.sessionToken);
        form.append('file', file);
        form.append('message_type', messageType);
        form.append('client_message_id', clientId);
        if (extra) Object.entries(extra).forEach(([k, v]) => form.append(k, v));
        this.uploadLabel.textContent = messageType === 'VOICE' ? 'در حال ارسال پیام صوتی…' : 'در حال ارسال تصویر…';
        this.uploadStatus.classList.add('show');
        try {
            const res = await fetch(`${this.apiBase}/widget/conversations/${this.convId}/upload/`, { method: 'POST', body: form });
            if (!res.ok) return;
            const data = await res.json();
            this.renderIncoming(data);
            this.scrollToBottom();
        } catch (error) {
            console.error('RastiChat upload failed', error);
        } finally {
            this.uploadStatus.classList.remove('show');
        }
    }

    private async toggleRecording() {
        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            this.mediaRecorder.stop();
            return;
        }
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this.recordedChunks = [];
            this.recordCancelled = false;
            this.mediaRecorder = new MediaRecorder(stream);
            this.recordStartedAt = Date.now();
            this.mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) this.recordedChunks.push(e.data); };
            this.mediaRecorder.onstop = () => {
                stream.getTracks().forEach(t => t.stop());
                this.micBtn.classList.remove('recording');
                this.micCancelBtn.classList.remove('show');
                window.clearInterval(this.recordTimer);
                this.micBtn.textContent = '🎤';
                if (this.recordCancelled) return;
                const duration = (Date.now() - this.recordStartedAt) / 1000;
                if (duration < 0.6) return; // ignore accidental taps
                const blob = new Blob(this.recordedChunks, { type: 'audio/webm' });
                const file = new File([blob], 'voice.webm', { type: 'audio/webm' });
                this.uploadFile(file, 'VOICE', { duration: String(Math.round(duration)) });
            };
            this.mediaRecorder.start();
            this.micBtn.classList.add('recording');
            this.micCancelBtn.classList.add('show');
            this.recordTimer = window.setInterval(() => {
                const sec = Math.round((Date.now() - this.recordStartedAt) / 1000);
                this.micBtn.textContent = fmtDuration(sec);
            }, 500);
        } catch (error) {
            console.error('RastiChat microphone access denied', error);
            this.showNotice('برای ارسال پیام صوتی، دسترسی به میکروفون لازم است.');
        }
    }

    private cancelRecording() {
        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            this.recordCancelled = true;
            this.mediaRecorder.stop();
        }
    }

    private async submitRating(convId: string, rating: number, bubbleEl: HTMLElement) {
        const clientId = 'msg_' + Date.now() + '_rating';
        this.renderedIds.add(clientId); // suppress the echoed RATING broadcast; we update the UI locally below
        bubbleEl.querySelectorAll('.rasti-r-stars button').forEach((btn, i) => btn.classList.toggle('on', i < rating));
        const thanks = bubbleEl.querySelector('.rasti-r-thanks') as HTMLElement;
        if (thanks) thanks.style.display = 'block';
        try {
            await fetch(`${this.apiBase}/widget/conversations/${convId}/rate/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_token: this.sessionToken, rating, client_message_id: clientId }),
            });
        } catch (error) {
            console.error('RastiChat rating submit failed', error);
        }
    }

    private renderIncoming(data: WireMessage) {
        const cid = data.client_message_id;
        if (cid) {
            if (this.renderedIds.has(cid)) return;
            this.renderedIds.add(cid);
        }
        const html = this.renderMessage(data);
        if (!html) return;
        this.messagesContainer.insertAdjacentHTML('beforeend', html);
        const node = this.messagesContainer.lastElementChild as HTMLElement;
        this.wireBubble(node, data);
    }

    private wireBubble(node: HTMLElement, data: WireMessage) {
        const voice = node.querySelector('.rasti-bubble.rasti-voice');
        if (voice) {
            const audio = voice.querySelector('audio') as HTMLAudioElement;
            const playBtn = voice.querySelector('.rasti-vplay') as HTMLButtonElement;
            const fill = voice.querySelector('.rasti-vfill') as HTMLElement;
            const durEl = voice.querySelector('.rasti-vdur') as HTMLElement;
            const totalDur = Number(data.metadata?.duration) || 0;
            playBtn.addEventListener('click', () => {
                if (audio.paused) { audio.play().catch(() => {}); playBtn.textContent = '⏸'; }
                else { audio.pause(); playBtn.textContent = '▶'; }
            });
            audio.addEventListener('timeupdate', () => {
                if (audio.duration) fill.style.width = (audio.currentTime / audio.duration * 100) + '%';
                durEl.textContent = fmtDuration(audio.currentTime || totalDur);
            });
            audio.addEventListener('ended', () => { playBtn.textContent = '▶'; fill.style.width = '0%'; durEl.textContent = fmtDuration(totalDur); });
        }
        const img = node.querySelector('.rasti-bubble.rasti-img img') as HTMLImageElement;
        if (img) img.addEventListener('click', () => window.open(img.src, '_blank'));

        const ratingCard = node.querySelector('.rasti-bubble.rasti-rating');
        if (ratingCard && this.convId) {
            ratingCard.querySelectorAll('.rasti-r-stars button').forEach((btn) => {
                btn.addEventListener('click', () => {
                    const r = Number((btn as HTMLElement).dataset.r);
                    this.submitRating(this.convId!, r, ratingCard as HTMLElement);
                });
            });
        }
    }

    private renderMessage(data: WireMessage): string {
        const out = data.sender_type === 'VISITOR';
        const cls = out ? 'visitor' : 'operator';
        const time = fmtTime(data.created_at);
        const tick = out ? `<span class="rasti-tick ${data.seen ? 'seen' : ''}">${data.seen ? '✓✓' : '✓'}</span>` : '';
        const meta = `<div class="rasti-meta">${time}${tick}</div>`;
        const type = data.message_type || 'TEXT';
        let inner = '';

        if (type === 'TEXT') {
            inner = `<div class="rasti-bubble">${escapeHtml(data.content || '')}</div>`;
        } else if (type === 'IMAGE') {
            const cap = data.metadata?.caption ? `<div class="cap">${escapeHtml(data.metadata.caption)}</div>` : '';
            inner = `<div class="rasti-bubble rasti-img"><img src="${data.attachment_url}" alt="" />${cap}</div>`;
        } else if (type === 'VOICE') {
            const dur = fmtDuration(Number(data.metadata?.duration) || 0);
            inner = `<div class="rasti-bubble rasti-voice"><button class="rasti-vplay" type="button">▶</button><div class="rasti-vbar"><div class="rasti-vfill"></div></div><span class="rasti-vdur">${dur}</span><audio src="${data.attachment_url}" preload="none"></audio></div>`;
        } else if (type === 'PRODUCT') {
            const m = data.metadata || {};
            const initial = (m.brand || m.name || '؟').trim().charAt(0);
            const img = m.image ? `style="background-image:url('${m.image}')"` : '';
            inner = `<div class="rasti-bubble rasti-product">
                <div class="rasti-p-img" ${img}>${m.image ? '' : initial}</div>
                <div class="rasti-p-body">
                    <div class="rasti-p-brand">${escapeHtml(m.brand || '')}</div>
                    <div class="rasti-p-name">${escapeHtml(m.name || '')}</div>
                    <div class="rasti-p-rate">⭐ ${m.rating ?? ''} (${m.reviews_count ?? 0} نظر)</div>
                    <div class="rasti-p-price"><span class="rasti-p-now">${Number(m.price || 0).toLocaleString('fa-IR')}</span>${m.old_price ? `<span class="rasti-p-old">${Number(m.old_price).toLocaleString('fa-IR')}</span>` : ''}</div>
                </div>
            </div>`;
        } else if (type === 'RATING_REQUEST') {
            inner = `<div class="rasti-bubble rasti-rating">
                <div class="rasti-r-title">از این گفتگو چقدر راضی بودید؟</div>
                <div class="rasti-r-stars">${[1,2,3,4,5].map(i => `<button type="button" data-r="${i}">★</button>`).join('')}</div>
                <div class="rasti-r-thanks" style="display:none">از لطفتون ممنونیم 🌹</div>
            </div>`;
        } else if (type === 'RATING') {
            const r = Number(data.metadata?.rating) || 0;
            inner = `<div class="rasti-bubble">شما به این گفتگو ${'★'.repeat(r)}${'☆'.repeat(5 - r)} امتیاز دادید</div>`;
        } else if (type === 'ARTICLE') {
            const a = data.metadata?.article;
            const img = a?.image_url ? `<div class="rasti-a-img" style="background-image:url('${a.image_url}')"></div>` : '';
            const link = a?.url ? `<a class="rasti-a-link" href="${a.url}" target="_blank" rel="noopener noreferrer">مشاهده مقاله ←</a>` : '';
            inner = `<div class="rasti-bubble rasti-article">
                ${img}
                <div class="rasti-a-body">
                    ${a?.category ? `<div class="rasti-a-cat">📚 ${escapeHtml(a.category)}</div>` : ''}
                    <div class="rasti-a-title">${escapeHtml(a?.title || '')}</div>
                    ${a?.excerpt ? `<div class="rasti-a-excerpt">${escapeHtml(a.excerpt)}</div>` : ''}
                    ${link}
                </div>
            </div>`;
        } else {
            return '';
        }

        return `<div class="rasti-msg ${cls}">${inner}${meta}</div>`;
    }

    private scrollToBottom() {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }
}

let activeWidget: RastiChatWidget | null = null;

window.RastiChat = {
    init: (config: RastiChatConfig) => {
        activeWidget = new RastiChatWidget(config);
    },
    /** Revoke the customer's session (call when the shopper logs out of the store). */
    logout: async () => {
        await activeWidget?.logout();
    }
};
