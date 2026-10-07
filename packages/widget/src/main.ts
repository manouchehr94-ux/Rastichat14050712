export {};

declare global {
    interface Window {
        RastiChat: {
            init: (config: RastiChatConfig) => void; logout: () => Promise<void>;
            open: () => void; close: () => void; refreshVisibility: () => void; destroy: () => void;
        };
    }
}

interface RastiChatConfig {
    projectKey: string;
    /** Explicit overrides of the remotely managed launcher configuration (leave unset to follow the project's configuration). */
    position?: 'left' | 'right';
    primaryColor?: string;
    /**
     * Trusted identity bootstrap. Called by the widget whenever it needs a session; it must ask THIS host's own backend for
     * a fresh short-lived identity assertion (Integration Contract v1 §7) and return it, or return null for a guest.
     * Never put an assertion, user id or role in markup: the browser only relays what the host backend signed.
     */
    bootstrap?: () => Promise<string | null | undefined>;
    /** Page context for the project's `hidden` pre-chat fields (e.g. `{ page: location.pathname }`). Untrusted by the server. */
    context?: Record<string, string>;
    /** Base REST URL, e.g. "https://chat.example.com/api/v1". Defaults to localhost for local development. */
    apiBase?: string;
    /** Base WebSocket URL, e.g. "wss://chat.example.com/ws". Defaults to localhost for local development. */
    wsBase?: string;
}

type PreChatFieldType = 'text' | 'textarea' | 'email' | 'phone' | 'select' | 'radio' | 'checkbox' | 'consent' | 'hidden';

interface PreChatField {
    key: string;
    type: PreChatFieldType;
    label: string;
    placeholder?: string;
    required?: boolean;
    max_length?: number | null;
    choices?: { value: string; label: string }[];
}

/** `GET /widget/config/` (version 1). Anything unrecognised makes the widget fall back to its legacy behaviour. */
interface RemoteConfig {
    version: 1;
    launcher: {
        enabled: boolean; mode: 'icon' | 'icon_text'; position: 'bottom-right' | 'bottom-left'; offset: { x: number; y: number };
        label: string; tooltip: string; icon: string; color: string; greeting: string; auto_open: boolean; mobile: { fullscreen: boolean };
    };
    visibility: { hide_on_paths: string[]; show_on_paths: string[] };
    behavior: { start_mode: 'on_load' | 'on_open' | 'on_first_message' };
    pre_chat: { enabled: boolean; title: string; submit_label: string; fields: PreChatField[] };
    identity: { guest_allowed: boolean; authenticated_only: boolean };
    locale: 'fa' | 'en';
    direction: 'rtl' | 'ltr';
    capabilities: { attachments: boolean; voice: boolean; emoji: boolean; rating: boolean };
}

const ICONS: Record<string, string> = { chat: '💬', help: '❓', headset: '🎧', mail: '✉️', sparkle: '✨' };

// Strings for the parts of the widget that are driven by remote configuration (the original chat strings stay as they were).
const STRINGS = {
    fa: {
        startChat: 'شروع گفتگو', required: 'این فیلد الزامی است.', invalidEmail: 'ایمیل معتبر وارد کنید.', invalidPhone: 'شماره تلفن معتبر وارد کنید.',
        unavailable: 'پشتیبانی موقتاً در دسترس نیست. کمی بعد دوباره تلاش کنید.', signIn: 'برای گفتگو ابتدا وارد حساب کاربری خود شوید.',
        preChatTitle: 'قبل از شروع گفتگو', choose: 'انتخاب کنید…', launcherLabel: 'گفتگوی آنلاین', close: 'بستن', formError: 'لطفاً خطاهای فرم را اصلاح کنید.',
    },
    en: {
        startChat: 'Start chat', required: 'This field is required.', invalidEmail: 'Enter a valid email address.', invalidPhone: 'Enter a valid phone number.',
        unavailable: 'Support is temporarily unavailable. Please try again shortly.', signIn: 'Please sign in to chat with us.',
        preChatTitle: 'Before we start', choose: 'Choose…', launcherLabel: 'Chat with us', close: 'Close', formError: 'Please fix the errors in the form.',
    },
};

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
    private wsReady = false;
    private reconnectTimer: number | undefined;
    private sessionToken: string | null = null;
    private convId: string | null = null;
    private apiBase = 'http://localhost:8080/api/v1';
    private wsBase = 'ws://localhost:8080/ws';

    private renderedIds = new Set<string>();
    /** Messages typed before the socket was authenticated: shown as pending, sent on `auth.ok` (the server de-duplicates on client_message_id). */
    private pendingSends: { clientId: string; text: string }[] = [];
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

    // --- remotely managed configuration (launcher, start behaviour, pre-chat, identity policy) ---
    private offsetX = 20;
    private offsetY = 20;
    private direction: 'rtl' | 'ltr' = 'rtl';
    private strings = STRINGS.fa;
    private remote: RemoteConfig | null = null;
    private explicit = { position: false, primaryColor: false };
    private startMode: 'on_load' | 'on_open' | 'on_first_message' = 'on_load';
    private styleEl!: HTMLStyleElement;
    private greetingEl!: HTMLElement;
    private prechatEl!: HTMLElement;
    private preChatAnswers: Record<string, string | boolean> | null = null;
    private preChatDone = false;
    private startingChat: Promise<void> | null = null;
    /** false while the first session/conversation setup (initSession) is still running; a send during that window is queued, never dropped */
    private initDone = false;
    private unavailable = false;
    private destroyed = false;
    private listeners: { target: EventTarget; type: string; fn: EventListener }[] = [];

    constructor(config: RastiChatConfig) {
        this.explicit = { position: !!config.position, primaryColor: !!config.primaryColor };
        this.config = { position: 'right', primaryColor: '#BC5A38', ...config };
        if (config.apiBase) this.apiBase = config.apiBase.replace(/\/$/, '');
        if (config.wsBase) this.wsBase = config.wsBase.replace(/\/$/, '');
        this.container = document.createElement('div');
        this.container.id = 'rasti-container';
        document.body.appendChild(this.container);

        this.initUI();
        this.initEvents();
        void this.boot();
    }

    // ------------------------------------------------------------------------------------ remote configuration
    private async boot() {
        this.remote = await this.fetchRemoteConfig();
        if (this.destroyed) return;
        this.applyRemoteConfig();
        if (this.remote && !this.remote.launcher.enabled) return; // disabled for this project: no session, no network
        await this.initSession();
    }

    private async fetchRemoteConfig(): Promise<RemoteConfig | null> {
        try {
            const res = await fetch(`${this.apiBase}/widget/config/?project_key=${encodeURIComponent(this.config.projectKey)}`);
            if (!res.ok) return null;
            const data = await res.json();
            return data && data.version === 1 && data.launcher && data.behavior && data.pre_chat && data.identity ? (data as RemoteConfig) : null;
        } catch {
            return null; // an unreachable/old server must never break the widget: legacy behaviour applies
        }
    }

    private applyRemoteConfig() {
        const r = this.remote;
        if (!r) return;
        const l = r.launcher;
        this.strings = STRINGS[r.locale] ?? STRINGS.fa;
        this.direction = r.direction === 'ltr' ? 'ltr' : 'rtl';
        if (!this.explicit.position) this.config.position = l.position === 'bottom-left' ? 'left' : 'right';
        if (!this.explicit.primaryColor) this.config.primaryColor = l.color;
        this.offsetX = l.offset.x;
        this.offsetY = l.offset.y;
        this.styleEl.textContent = this.css();
        this.startMode = r.pre_chat.enabled && r.behavior.start_mode === 'on_load' ? 'on_first_message' : r.behavior.start_mode;

        const label = l.mode === 'icon_text' ? (l.label || this.strings.launcherLabel) : '';
        this.launcher.querySelector('.rasti-l-icon')!.textContent = ICONS[l.icon] ?? ICONS.chat;
        const labelEl = this.launcher.querySelector('.rasti-l-label') as HTMLElement;
        labelEl.textContent = label;
        this.launcher.classList.toggle('has-label', !!label);
        const name = l.tooltip || l.label || this.strings.launcherLabel;
        this.launcher.setAttribute('aria-label', name);
        this.launcher.title = l.tooltip || '';
        this.panel.setAttribute('aria-label', name);
        this.panel.classList.toggle('rasti-sheet', !l.mobile.fullscreen);
        if (!r.capabilities.attachments) document.getElementById('rasti-attach-btn')!.style.display = 'none';
        if (!r.capabilities.voice) this.micBtn.style.display = 'none';
        if (!r.capabilities.emoji) document.getElementById('rasti-emoji-btn')!.style.display = 'none';
        this.inputField.placeholder = r.locale === 'en' ? 'Type your message…' : 'پیام خود را بنویسید...';

        this.updateVisibility();
        this.showGreeting(l.greeting);
        const mobile = typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 480px)').matches;
        if (l.auto_open && !(mobile && l.mobile.fullscreen) && this.safeSession('get', 'rasti_autoopened') !== '1') {
            this.safeSession('set', 'rasti_autoopened', '1');
            this.togglePanel(true);
        }
    }

    private safeSession(op: 'get' | 'set', key: string, value = ''): string | null {
        try {
            if (op === 'set') { sessionStorage.setItem(key, value); return null; }
            return sessionStorage.getItem(key);
        } catch { return null; } // storage can be blocked (private mode, sandboxed frames)
    }

    private pathMatches(pattern: string, path: string): boolean {
        const re = new RegExp('^' + pattern.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*') + '$');
        return re.test(path);
    }

    /** Hide/show rules (`visibility.*_paths`) — re-evaluated on navigation (`popstate`) and by `RastiChat.refreshVisibility()`. */
    public updateVisibility() {
        const r = this.remote;
        let visible = true;
        if (r) {
            const path = window.location.pathname;
            const show = r.visibility?.show_on_paths ?? [];
            const hide = r.visibility?.hide_on_paths ?? [];
            visible = r.launcher.enabled && (show.length === 0 || show.some(p => this.pathMatches(p, path))) && !hide.some(p => this.pathMatches(p, path));
        }
        this.container.style.display = visible ? '' : 'none';
        if (!visible && this.isOpen) this.togglePanel(false);
    }

    private showGreeting(text: string) {
        if (!text || this.isOpen || this.safeSession('get', 'rasti_greeted') === '1') return;
        this.greetingEl.querySelector('.rasti-g-text')!.textContent = text;
        this.greetingEl.hidden = false;
    }

    private hideGreeting() {
        if (this.greetingEl.hidden) return;
        this.greetingEl.hidden = true;
        this.safeSession('set', 'rasti_greeted', '1');
    }

    private setUnavailable(text: string) {
        this.unavailable = true;
        this.launcher.classList.add('rasti-unavailable');
        this.noticeEl.textContent = text;
        this.noticeEl.classList.add('show');
    }

    public setOpen(open: boolean) {
        if (this.container.style.display === 'none') return;
        this.togglePanel(open);
    }

    public destroy() {
        this.destroyed = true;
        window.clearTimeout(this.reconnectTimer);
        window.clearTimeout(this.noticeHideTimer);
        window.clearTimeout(this.typingHideTimer);
        window.clearInterval(this.recordTimer);
        this.dropSocket();
        for (const l of this.listeners) l.target.removeEventListener(l.type, l.fn);
        this.listeners = [];
        document.body.style.overflow = '';
        this.container.remove();
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

    /** Positioning/colour tokens come from `config` (explicit overrides) or the remote configuration, so this is regenerated when it arrives. */
    private css(): string {
        const pos = this.config.position;
        const offX = this.offsetX;
        const offY = this.offsetY;
        return `
                /* Design tokens mirrored from docs/product/DESIGN_TOKENS.md — keep in sync with apps/operator-dashboard/src/app/globals.css */
                #rasti-container * { box-sizing: border-box; font-family: 'Vazirmatn', Tahoma, Arial, sans-serif; }
                #rasti-container { direction: ${this.direction}; }
                #rasti-launcher {
                    position: fixed; bottom: ${offY}px; ${pos}: ${offX}px;
                    width: 60px; height: 60px; background: ${this.config.primaryColor};
                    border-radius: 50%; cursor: pointer; box-shadow: 0 6px 18px rgba(0,0,0,0.25);
                    display: flex; align-items: center; justify-content: center; color: white; font-size: 26px; z-index: 9998;
                    transition: transform .15s;
                }
                #rasti-launcher:hover { transform: scale(1.06); }
                #rasti-launcher.has-label { width: auto; padding: 0 20px; border-radius: 30px; gap: 8px; font-size: 20px; }
                #rasti-launcher .rasti-l-label { font-size: 14px; font-weight: 600; }
                #rasti-launcher:focus-visible, #rasti-panel button:focus-visible, #rasti-panel input:focus-visible, #rasti-panel select:focus-visible, #rasti-panel textarea:focus-visible { outline: 3px solid #1a73e8; outline-offset: 2px; }
                #rasti-launcher.rasti-unavailable { filter: grayscale(1); opacity: .75; }
                #rasti-greeting { position: fixed; bottom: ${offY + 70}px; ${pos}: ${offX}px; max-width: min(260px, calc(100vw - 40px)); background: #fff; color: #2C211A; border: 1px solid #ECDCC8; border-radius: 14px; padding: 10px 30px 10px 14px; font-size: 13px; line-height: 1.7; box-shadow: 0 10px 28px rgba(0,0,0,.18); z-index: 9997; cursor: pointer; }
                #rasti-greeting[hidden] { display: none; }
                #rasti-greeting .rasti-g-x { position: absolute; top: 4px; ${pos === 'right' ? 'left' : 'right'}: 6px; background: none; border: none; cursor: pointer; font-size: 14px; color: #A08C77; }
                #rasti-prechat { display: none; flex: 1; overflow-y: auto; padding: 16px; background: #FBF4EB; }
                #rasti-panel.rasti-prechat-on #rasti-prechat { display: block; }
                #rasti-panel.rasti-prechat-on #rasti-messages, #rasti-panel.rasti-prechat-on #rasti-quick, #rasti-panel.rasti-prechat-on #rasti-input-area { display: none; }
                #rasti-prechat h2 { font-size: 14px; margin: 0 0 12px; color: #2C211A; }
                .rasti-field { margin-bottom: 12px; }
                .rasti-field label, .rasti-field legend { display: block; font-size: 12px; font-weight: 600; margin-bottom: 4px; color: #5C4A3A; padding: 0; }
                .rasti-field input[type=text], .rasti-field input[type=email], .rasti-field input[type=tel], .rasti-field select, .rasti-field textarea { width: 100%; border: 1.5px solid #ECDCC8; border-radius: 10px; padding: 8px 10px; font-size: 13px; background: #fff; color: #2C211A; }
                .rasti-field textarea { min-height: 70px; resize: vertical; }
                .rasti-field fieldset { border: none; margin: 0; padding: 0; }
                .rasti-field .rasti-inline { display: flex; align-items: center; gap: 6px; font-size: 12.5px; font-weight: 400; margin-bottom: 4px; }
                .rasti-field .rasti-err { color: #8a2f22; font-size: 11.5px; margin-top: 3px; }
                .rasti-field [aria-invalid=true] { border-color: #C0504A; }
                #rasti-prechat .rasti-submit { width: 100%; background: ${this.config.primaryColor}; color: #fff; border: none; border-radius: 12px; padding: 10px; font-size: 13.5px; font-weight: 600; cursor: pointer; }
                #rasti-prechat .rasti-form-err { color: #8a2f22; font-size: 12px; margin-bottom: 8px; }
                #rasti-launcher .rasti-badge {
                    position: absolute; top: -4px; ${pos === 'right' ? 'left' : 'right'}: -4px; background: #C0504A; color: #fff;
                    font-size: 11px; font-weight: 700; min-width: 19px; height: 19px; border-radius: 999px;
                    display: none; align-items: center; justify-content: center; padding: 0 4px; border: 2px solid #fff;
                }
                #rasti-panel {
                    position: fixed; bottom: ${offY + 72}px; ${pos}: ${offX}px;
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
                .rasti-msg.rasti-pending { opacity: .6; }
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
                    #rasti-panel.rasti-sheet { width: calc(100vw - 16px); height: min(520px, calc(100dvh - 90px)); bottom: 80px; ${pos}: 8px; border-radius: 18px; border: 1px solid #ECDCC8; }
                    #rasti-panel:not(.rasti-sheet) {
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
        `;
    }

    private initUI() {
        this.loadFont();
        this.container.innerHTML = `
            <style id="rasti-style">${this.css()}</style>
            <div id="rasti-greeting" role="status" hidden><span class="rasti-g-text"></span><button type="button" class="rasti-g-x" aria-label="✕">✕</button></div>
            <div id="rasti-launcher" role="button" tabindex="0" aria-label="گفتگوی آنلاین" aria-expanded="false" aria-controls="rasti-panel"><span class="rasti-l-icon" aria-hidden="true">💬</span><span class="rasti-l-label"></span><span class="rasti-badge" id="rasti-badge"></span></div>
            <div id="rasti-panel" role="dialog" aria-label="گفتگوی آنلاین">
                <div id="rasti-header">
                    <div class="rasti-avatar" id="rasti-avatar">🙂<span class="dot" id="rasti-status-dot"></span></div>
                    <div class="rasti-htext"><b id="rasti-title">پشتیبانی آنلاین</b><span id="rasti-subtitle">معمولاً در چند دقیقه پاسخ می‌دهیم</span></div>
                    <div id="rasti-close">✕</div>
                </div>
                <div id="rasti-offline-banner">در حال اتصال مجدد…</div>
                <div id="rasti-notice" role="status"></div>
                <div id="rasti-prechat"></div>
                <div id="rasti-messages" aria-live="polite"></div>
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
        this.styleEl = document.getElementById('rasti-style') as HTMLStyleElement;
        this.greetingEl = document.getElementById('rasti-greeting')!;
        this.prechatEl = document.getElementById('rasti-prechat')!;
        this.launcher = document.getElementById('rasti-launcher')!;
        this.panel = document.getElementById('rasti-panel')!;
        // signed attachment URLs expire (10 min): on a failed <img>/<audio> ask for a fresh one, once
        this.panel.addEventListener('error', (e) => { void this.refreshAttachment(e); }, true);
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

    private listen(target: EventTarget, type: string, fn: EventListener) {
        target.addEventListener(type, fn);
        this.listeners.push({ target, type, fn });
    }

    private initEvents() {
        this.launcher.addEventListener('click', () => this.togglePanel());
        this.launcher.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this.togglePanel(); }
        });
        this.greetingEl.addEventListener('click', (e) => {
            if ((e.target as HTMLElement).closest('.rasti-g-x')) { this.hideGreeting(); return; }
            this.togglePanel(true);
        });
        // Escape closes the panel; SPA navigation re-evaluates the show/hide rules
        this.listen(document, 'keydown', (e) => { if ((e as KeyboardEvent).key === 'Escape' && this.isOpen) this.togglePanel(false); });
        this.listen(window, 'popstate', () => this.updateVisibility());
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
        this.listen(document, 'click', (e) => {
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
        this.launcher.setAttribute('aria-expanded', String(this.isOpen));
        if (this.isOpen) this.hideGreeting();
        if (typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 480px)').matches) {
            document.body.style.overflow = this.isOpen ? 'hidden' : '';
        }
        if (this.isOpen) {
            this.unreadCount = 0;
            this.updateBadge();
            this.sendMarkRead();
            this.scrollToBottom();
            if (this.needsPreChat()) {
                this.renderPreChat();
            } else if (this.startMode === 'on_open' && !this.convId && !this.unavailable) {
                void this.ensureConversation();
            }
        }
    }

    // ------------------------------------------------------------------------------------ pre-chat
    private needsPreChat(): boolean {
        return !!this.remote?.pre_chat.enabled && !this.convId && !this.preChatDone && !this.unavailable;
    }

    /** The answers sent with the conversation-creating call (`undefined` when the project asks no questions). */
    private preChatPayload(): Record<string, string | boolean> | undefined {
        const pc = this.remote?.pre_chat;
        if (!pc?.enabled) return undefined;
        const answers: Record<string, string | boolean> = { ...(this.preChatAnswers ?? {}) };
        for (const f of pc.fields) {
            const v = f.type === 'hidden' ? this.config.context?.[f.key] : undefined;
            if (v) answers[f.key] = String(v).slice(0, 255);
        }
        return answers;
    }

    private renderPreChat(errors: Record<string, string> = {}) {
        const pc = this.remote!.pre_chat;
        const form = document.createElement('form');
        form.noValidate = true;
        const title = document.createElement('h2');
        title.textContent = pc.title || this.strings.preChatTitle;
        form.appendChild(title);
        if (Object.keys(errors).length) {
            const top = document.createElement('div');
            top.className = 'rasti-form-err';
            top.setAttribute('role', 'alert');
            top.textContent = this.strings.formError;
            form.appendChild(top);
        }
        for (const f of pc.fields) {
            if (f.type === 'hidden') continue;
            form.appendChild(this.buildField(f, errors[f.key]));
        }
        const submit = document.createElement('button');
        submit.type = 'submit';
        submit.className = 'rasti-submit';
        submit.textContent = pc.submit_label || this.strings.startChat;
        form.appendChild(submit);
        form.addEventListener('submit', (e) => { e.preventDefault(); this.submitPreChat(form); });
        this.prechatEl.replaceChildren(form);
        this.panel.classList.add('rasti-prechat-on');
        (form.querySelector('input,select,textarea') as HTMLElement | null)?.focus();
    }

    /** Built with DOM APIs and textContent only — configuration text is never interpreted as HTML. */
    private buildField(f: PreChatField, error?: string): HTMLElement {
        const wrap = document.createElement('div');
        wrap.className = 'rasti-field';
        const id = `rasti-pc-${f.key}`;
        const errId = `${id}-err`;
        const text = (f.label || f.key) + (f.required && f.type !== 'consent' ? ' *' : '');
        const describe = (el: HTMLElement) => {
            if (f.required) el.setAttribute('aria-required', 'true');
            if (error) { el.setAttribute('aria-invalid', 'true'); el.setAttribute('aria-describedby', errId); }
        };
        if (f.type === 'radio') {
            const set = document.createElement('fieldset');
            const legend = document.createElement('legend');
            legend.textContent = text;
            set.appendChild(legend);
            for (const [i, c] of (f.choices ?? []).entries()) {
                const row = document.createElement('label');
                row.className = 'rasti-inline';
                const input = document.createElement('input');
                input.type = 'radio'; input.name = id; input.value = c.value; input.id = `${id}-${i}`;
                describe(input);
                row.append(input, document.createTextNode(c.label));
                set.appendChild(row);
            }
            wrap.appendChild(set);
        } else if (f.type === 'checkbox' || f.type === 'consent') {
            const row = document.createElement('label');
            row.className = 'rasti-inline';
            const input = document.createElement('input');
            input.type = 'checkbox'; input.id = id; input.name = id;
            describe(input);
            row.append(input, document.createTextNode(f.label + (f.required ? ' *' : '')));
            wrap.appendChild(row);
        } else {
            const label = document.createElement('label');
            label.htmlFor = id;
            label.textContent = text;
            wrap.appendChild(label);
            let control: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
            if (f.type === 'textarea') {
                control = document.createElement('textarea');
            } else if (f.type === 'select') {
                const select = document.createElement('select');
                const first = document.createElement('option');
                first.value = ''; first.textContent = f.placeholder || this.strings.choose;
                select.appendChild(first);
                for (const c of f.choices ?? []) {
                    const o = document.createElement('option');
                    o.value = c.value; o.textContent = c.label;
                    select.appendChild(o);
                }
                control = select;
            } else {
                const input = document.createElement('input');
                input.type = f.type === 'email' ? 'email' : f.type === 'phone' ? 'tel' : 'text';
                control = input;
            }
            control.id = id; control.name = id;
            if ('placeholder' in control && f.type !== 'select') control.placeholder = f.placeholder || '';
            if (f.max_length && 'maxLength' in control) control.maxLength = f.max_length;
            describe(control);
            wrap.appendChild(control);
        }
        const err = document.createElement('div');
        err.className = 'rasti-err';
        err.id = errId;
        err.setAttribute('role', 'alert');
        err.textContent = error || '';
        wrap.appendChild(err);
        return wrap;
    }

    private submitPreChat(form: HTMLFormElement) {
        const answers: Record<string, string | boolean> = {};
        const errors: Record<string, string> = {};
        for (const f of this.remote!.pre_chat.fields) {
            if (f.type === 'hidden') continue;
            const id = `rasti-pc-${f.key}`;
            let value: string | boolean = '';
            if (f.type === 'checkbox' || f.type === 'consent') {
                value = (form.querySelector(`#${id}`) as HTMLInputElement).checked;
            } else if (f.type === 'radio') {
                value = (form.querySelector(`input[name="${id}"]:checked`) as HTMLInputElement | null)?.value ?? '';
            } else {
                value = ((form.querySelector(`#${id}`) as HTMLInputElement).value || '').trim();
            }
            if (f.required && (value === '' || value === false)) errors[f.key] = this.strings.required;
            else if (f.type === 'email' && value && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value))) errors[f.key] = this.strings.invalidEmail;
            else if (f.type === 'phone' && value && !/^\+?[0-9()\-\s]{5,20}$/.test(String(value))) errors[f.key] = this.strings.invalidPhone;
            if (value !== '' && value !== false) answers[f.key] = value;
        }
        if (Object.keys(errors).length) { this.renderPreChat(errors); return; }
        this.preChatAnswers = answers;
        this.preChatDone = true;
        this.panel.classList.remove('rasti-prechat-on');
        this.inputField.focus();
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
            if (this.config.bootstrap) {
                await this.sessionWithBootstrap();
            } else {
                // a verified session left behind by a previously signed-in customer must never be reused by an anonymous page
                if (localStorage.getItem('rasti_session_sub')) await this.forgetStoredSession();
                const stored = localStorage.getItem('rasti_session');
                if (stored) {
                    this.sessionToken = stored;
                } else if (this.startMode === 'on_load') {
                    await this.createSession();
                }
            }
            if (this.startMode === 'on_load') await this.startChat();
            else if (this.sessionToken) await this.startChat(true, { peek: true });
        } catch (error) {
            console.error("RastiChat init failed", error);
            this.setUnavailable(this.strings.unavailable);
        } finally {
            this.initDone = true;
        }
    }

    private authenticatedOnly(): boolean {
        return !!this.remote?.identity.authenticated_only;
    }

    /** Ask the host backend for an assertion; a failure means "not signed in" (guest), never an exception. */
    private async safeBootstrap(): Promise<string | null> {
        try {
            return (await this.config.bootstrap!()) || null;
        } catch (error) {
            console.error("RastiChat bootstrap failed", error);
            return null;
        }
    }

    /** The `sub` claim of an assertion, read ONLY to tell whether the stored session belongs to the same person (the server verifies everything). */
    private assertionSubject(assertion: string): string | null {
        try {
            const payload = assertion.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
            const sub = JSON.parse(atob(payload.padEnd(Math.ceil(payload.length / 4) * 4, '='))).sub;
            return typeof sub === 'string' ? sub : null;
        } catch { return null; }
    }

    private async sessionWithBootstrap() {
        const assertion = await this.safeBootstrap();
        const sub = assertion ? this.assertionSubject(assertion) : null;
        const stored = localStorage.getItem('rasti_session');
        const storedSub = localStorage.getItem('rasti_session_sub');
        if (stored && sub && storedSub === sub) { this.sessionToken = stored; return; }      // same person: resume
        if (stored && !storedSub && !assertion) { this.sessionToken = stored; return; }      // anonymous guest keeps their session
        if (stored && storedSub) await this.forgetStoredSession();                          // different person / signed out
        const guestToken = localStorage.getItem('rasti_session');                           // a guest session (if any) is offered for upgrade
        if (assertion) {
            try { await this.exchangeAssertion(assertion, guestToken); return; } catch (error) {
                console.error("RastiChat identity exchange failed", error);
                if (this.authenticatedOnly()) { this.setUnavailable(this.strings.signIn); return; }
            }
        } else if (this.authenticatedOnly()) {
            this.setUnavailable(this.strings.signIn);
            return;
        }
        if (!this.sessionToken && this.startMode === 'on_load') await this.createGuestSession();
    }

    private async exchangeAssertion(assertion: string, guestToken: string | null = null) {
        const headers: Record<string, string> = { 'Content-Type': 'application/json' };
        if (guestToken) headers['X-Widget-Session'] = guestToken;   // lets the server attach the guest's conversation to the verified customer
        const res = await fetch(`${this.apiBase}/identity/customer/`, {
            method: 'POST', headers, body: JSON.stringify({ project_key: this.config.projectKey, assertion }),
        });
        if (!res.ok) throw new Error(`identity exchange failed (${res.status})`);
        const data = await res.json();
        this.sessionToken = data.session_token;
        localStorage.setItem('rasti_session', this.sessionToken!);
        const sub = this.assertionSubject(assertion);
        if (sub) localStorage.setItem('rasti_session_sub', sub);
    }

    /** Open a session: the trusted (bootstrapped) identity when the host provides one, otherwise a guest session. */
    private async createSession() {
        if (this.config.bootstrap) {
            const assertion = await this.safeBootstrap();
            if (assertion) {
                try { await this.exchangeAssertion(assertion); return; } catch (error) {
                    if (this.authenticatedOnly()) throw error;
                }
            } else if (this.authenticatedOnly()) {
                throw new Error('sign-in required');
            }
        }
        await this.createGuestSession();
    }

    private async createGuestSession() {
        const res = await fetch(`${this.apiBase}/widget/init/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_key: this.config.projectKey })
        });
        const data = await res.json();
        if (res.ok === false) throw new Error(`session request failed (${res.status})`);
        this.sessionToken = data.session_token;
        localStorage.setItem('rasti_session', this.sessionToken!);
        localStorage.removeItem('rasti_session_sub');
    }

    /** Revoke (best effort) and forget the stored session. */
    private async forgetStoredSession() {
        const token = localStorage.getItem('rasti_session');
        localStorage.removeItem('rasti_session');
        localStorage.removeItem('rasti_session_sub');
        this.sessionToken = null;
        this.convId = null;
        this.preChatDone = false;
        this.dropSocket();
        if (!token) return;
        try {
            await fetch(`${this.apiBase}/widget/session/revoke/`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_token: token }),
            });
        } catch { /* revocation is best effort: the server expires the session anyway */ }
    }

    /**
     * Make sure a conversation exists (it is created lazily on the first message / first open, so an idle visitor
     * never leaves an empty conversation in the inbox). Resolves true once there is one.
     */
    private async ensureConversation(): Promise<boolean> {
        if (this.convId) return true;
        if (this.startingChat) { await this.startingChat; return !!this.convId; }
        this.startingChat = (async () => {
            try {
                if (!this.sessionToken) await this.createSession();
                await this.startChat(true, { preChat: this.preChatPayload() });
            } catch (error) {
                console.error("RastiChat could not start the conversation", error);
                this.setUnavailable(this.authenticatedOnly() ? this.strings.signIn : this.strings.unavailable);
            } finally {
                this.startingChat = null;
            }
        })();
        await this.startingChat;
        return !!this.convId;
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
            localStorage.removeItem('rasti_session_sub');
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
        window.clearTimeout(this.reconnectTimer);
        this.wsReady = false;
        this.pendingSends = [];
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
        localStorage.removeItem('rasti_session_sub');
        this.sessionToken = null;
        this.convId = null;
        this.preChatAnswers = null;
        this.preChatDone = false;
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

    private async startChat(allowRecovery = true, opts: { peek?: boolean; preChat?: Record<string, string | boolean> } = {}) {
        try {
            const body: Record<string, unknown> = { session_token: this.sessionToken };
            if (opts.peek) body.create = false;           // restore an existing conversation without creating an empty one
            if (opts.preChat) body.pre_chat = opts.preChat;
            const res = await fetch(`${this.apiBase}/widget/start/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            if (res.status === 401 && allowRecovery) {
                if (await this.recoverFromInvalidSession()) await this.startChat(false, opts);
                return;
            }
            if (res.status === 400 && opts.preChat !== undefined) {
                // the project's form changed or an answer was refused: ask again, showing the server's per-field messages
                const err = await res.json().catch(() => ({}));
                this.preChatDone = false;
                this.renderPreChat(err && typeof err.errors === 'object' ? err.errors : {});
                if (!this.isOpen) this.togglePanel(true);
                return;
            }
            if (res.status === 403 && (await res.clone().json().catch(() => ({}))).code === 'identity_required') {
                this.setUnavailable(this.strings.signIn);
                return;
            }
            const data = await res.json();
            if (!data.id) return;                         // peek found nothing: the conversation starts later
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
            // the credential travels in a header, never in the URL (URLs get logged by proxies)
            const res = await fetch(`${this.apiBase}/widget/conversations/${this.convId}/messages/`, {
                headers: { 'X-Widget-Session': this.sessionToken },
            });
            if (res.status === 401) { if (await this.recoverFromInvalidSession()) await this.startChat(false); return; }
            if (!res.ok) return;
            const msgs: WireMessage[] = await res.json();
            msgs.forEach(m => this.renderIncoming(m));
            this.scrollToBottom();
        } catch (error) {
            console.error("RastiChat history load failed", error);
        }
    }

    private async refreshAttachment(event: Event): Promise<void> {
        const el = event.target;
        if (!(el instanceof HTMLImageElement || el instanceof HTMLAudioElement)) return;
        const id = /\/attachments\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\//.exec(el.getAttribute('src') || '')?.[1];
        if (!id || !this.sessionToken || el.dataset.rastiRetried) return;
        el.dataset.rastiRetried = '1'; // a really missing file must not loop
        try {
            const res = await fetch(`${this.apiBase}/widget/attachments/${id}/refresh/`, { headers: { 'X-Widget-Session': this.sessionToken } });
            if (!res.ok) return;
            const body = await res.json();
            if (!body.attachment_url) return;
            el.setAttribute('src', body.attachment_url);
            if (el instanceof HTMLAudioElement) el.load();
        } catch { /* leave the broken media as it is */ }
    }

    private canSend(): boolean {
        return !!this.ws && this.wsReady && this.ws.readyState === WebSocket.OPEN;
    }

    private scheduleReconnect() {
        window.clearTimeout(this.reconnectTimer);
        this.reconnectTimer = window.setTimeout(() => { void this.connectWebSocket(); }, 2000);
    }

    /**
     * Open the live connection. No credential is placed in the URL: a short-lived single-use
     * ticket is minted over REST (session credential in a header) and sent as the first frame.
     * The socket only counts as ready once the server answers `auth.ok`.
     */
    private async connectWebSocket() {
        if (!this.convId || !this.sessionToken) return;
        const convId = this.convId;
        let ticket: string;
        try {
            const res = await fetch(`${this.apiBase}/widget/ws-ticket/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Widget-Session': this.sessionToken },
                body: JSON.stringify({ conversation_id: convId }),
            });
            if (res.status === 401) {
                if (await this.recoverFromInvalidSession()) await this.startChat(false);
                return;
            }
            if (!res.ok) throw new Error(`ticket request failed (${res.status})`);
            ticket = (await res.json()).ticket;
        } catch (error) {
            console.error("RastiChat realtime ticket failed", error);
            this.offlineBanner.classList.add('show');
            this.scheduleReconnect();
            return;
        }
        if (this.convId !== convId) return; // the session was replaced while the ticket was in flight

        const ws = new WebSocket(`${this.wsBase}/v2/widget/${convId}/`);
        this.ws = ws;
        this.wsReady = false;

        ws.onopen = () => {
            ws.send(JSON.stringify({ type: 'auth', ticket }));
        };

        ws.onmessage = (event) => {
            const data: WireMessage = JSON.parse(event.data);
            if (!this.wsReady) {
                if (data.type === 'auth.ok') {
                    this.wsReady = true;
                    this.offlineBanner.classList.remove('show');
                    this.flushPendingSends();
                    // Anything that arrived before this point (between the first history fetch and auth, or while the
                    // socket was down) was never delivered to this socket: resync from the server. renderIncoming
                    // de-duplicates by message id / client_message_id, so frames that race the response are harmless.
                    void this.loadHistory();
                }
                return;
            }
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

        ws.onclose = () => {
            this.wsReady = false;
            this.offlineBanner.classList.add('show');
            this.scheduleReconnect();
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
        if (this.canSend()) {
            this.ws!.send(JSON.stringify({ type: 'typing' }));
        }
    }

    private sendMarkRead() {
        if (this.canSend()) {
            this.ws!.send(JSON.stringify({ type: 'mark_read' }));
        }
    }

    private markAllOutgoingSeen() {
        this.messagesContainer.querySelectorAll('.rasti-msg.visitor .rasti-tick').forEach(el => el.classList.add('seen'));
    }

    private sendMessage() {
        const text = this.inputField.value.trim();
        if (!text) return;
        const notReady = !this.convId || !this.sessionToken;
        if (notReady && this.unavailable) return;
        // Not ready yet = either the first message of a lazily started conversation, or the visitor was quicker than the initial
        // session/conversation setup. Both cases queue the message (shown as pending) instead of dropping it silently.
        const lazy = notReady;

        const clientId = 'msg_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
        if (lazy || !this.canSend()) {
            // Not authenticated (yet / any more): never drop the message silently. Show it as pending and show the
            // connection state; it is sent, in order, as soon as the server acknowledges the ticket.
            if (this.pendingSends.length >= 20) return;
            this.pendingSends.push({ clientId, text });
            this.renderIncoming({
                sender_type: 'VISITOR', content: text, message_type: 'TEXT',
                client_message_id: clientId, created_at: new Date().toISOString(), seen: false,
            });
            (this.messagesContainer.lastElementChild as HTMLElement | null)?.classList.add('rasti-pending');
            this.inputField.value = '';
            this.scrollToBottom();
            if (lazy) {
                // first message: open the conversation now; the queued message goes out (in order) once the socket authenticates.
                // While the initial setup is still running it will create the conversation itself — do not race it with a second start.
                if (this.initDone || this.startMode !== 'on_load') void this.ensureConversation();
                return;
            }
            this.offlineBanner.classList.add('show');
            return;
        }
        this.ws!.send(JSON.stringify({ message: text, client_message_id: clientId }));

        this.renderIncoming({
            sender_type: 'VISITOR', content: text, message_type: 'TEXT',
            client_message_id: clientId, created_at: new Date().toISOString(), seen: false,
        });
        this.inputField.value = '';
        this.scrollToBottom();
    }

    private flushPendingSends() {
        const queued = this.pendingSends;
        this.pendingSends = [];
        for (const { clientId, text } of queued) {
            this.ws!.send(JSON.stringify({ message: text, client_message_id: clientId }));
        }
        if (queued.length) this.messagesContainer.querySelectorAll('.rasti-pending').forEach(el => el.classList.remove('rasti-pending'));
    }

    private async uploadFile(file: File, messageType: 'IMAGE' | 'VOICE', extra?: Record<string, string>) {
        if (!this.convId && this.startMode !== 'on_load' && !this.unavailable) await this.ensureConversation();
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
        const sid = data.id ? 'id:' + data.id : '';
        if ((cid && this.renderedIds.has(cid)) || (sid && this.renderedIds.has(sid))) return;
        if (cid) this.renderedIds.add(cid);
        if (sid) this.renderedIds.add(sid);
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
        activeWidget?.destroy();
        activeWidget = new RastiChatWidget(config);
    },
    /** Open / close the chat panel programmatically (e.g. from a "Contact us" button on the host page). */
    open: () => activeWidget?.setOpen(true),
    close: () => activeWidget?.setOpen(false),
    /** Re-evaluate the show/hide path rules (a single-page app can call this after a client-side navigation). */
    refreshVisibility: () => activeWidget?.updateVisibility(),
    /** Remove the widget from the page and stop its network activity. */
    destroy: () => { activeWidget?.destroy(); activeWidget = null; },
    /** Revoke the customer's session (call when the shopper logs out of the store). */
    logout: async () => {
        await activeWidget?.logout();
    }
};
