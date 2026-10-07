// Pure helpers behind the widget settings screen (Integration Contract v1 §9, projects/widget_config.py on the server).
// The server validates everything again; these only turn form state into the document and back.

export type FieldType = 'text' | 'textarea' | 'email' | 'phone' | 'select' | 'radio' | 'checkbox' | 'consent' | 'hidden';
export const FIELD_TYPES: { value: FieldType; label: string }[] = [
    { value: 'text', label: 'متن کوتاه' }, { value: 'textarea', label: 'متن بلند' }, { value: 'email', label: 'ایمیل' },
    { value: 'phone', label: 'تلفن' }, { value: 'select', label: 'لیست کشویی' }, { value: 'radio', label: 'گزینه‌ای' },
    { value: 'checkbox', label: 'تیک' }, { value: 'consent', label: 'پذیرش قوانین' }, { value: 'hidden', label: 'مقدار پنهان (زمینه صفحه)' },
];
export const CHOICE_TYPES: FieldType[] = ['select', 'radio'];

export interface FieldForm { key: string; type: FieldType; label: string; placeholder: string; required: boolean; choicesText: string; maxLength: string }
export type PreChatMode = 'none' | 'one' | 'form';

export interface WidgetForm {
    launcherEnabled: boolean; launcherMode: 'icon' | 'icon_text'; label: string; tooltip: string; position: 'bottom-right' | 'bottom-left';
    color: string; icon: string; greeting: string; autoOpen: boolean; offsetX: string; offsetY: string; mobileFullscreen: boolean;
    startMode: 'on_load' | 'on_open' | 'on_first_message';
    preChatMode: PreChatMode; preChatTitle: string; submitLabel: string; fields: FieldForm[];
    guestAllowed: boolean; authenticatedOnly: boolean; locale: 'fa' | 'en'; direction: 'rtl' | 'ltr';
}

type Doc = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export const emptyField = (): FieldForm => ({ key: '', type: 'text', label: '', placeholder: '', required: false, choicesText: '', maxLength: '' });

/** `value | label` per line (or just `label`, whose slug becomes the value). */
export const parseChoices = (text: string): { value: string; label: string }[] =>
    text.split('\n').map(l => l.trim()).filter(Boolean).map((line, i) => {
        const [a, ...rest] = line.split('|');
        const label = (rest.length ? rest.join('|') : a).trim();
        const value = (rest.length ? a : `c${i + 1}`).trim();
        return { value, label };
    });

export const formatChoices = (choices: { value: string; label: string }[] = []): string => choices.map(c => `${c.value} | ${c.label}`).join('\n');

export const formFromEffective = (eff: Doc): WidgetForm => {
    const l = eff.launcher, pc = eff.pre_chat;
    const visible = (pc.fields as Doc[]).filter(f => f.type !== 'hidden');
    return {
        launcherEnabled: l.enabled, launcherMode: l.mode, label: l.label, tooltip: l.tooltip, position: l.position, color: l.color, icon: l.icon,
        greeting: l.greeting, autoOpen: l.auto_open, offsetX: String(l.offset.x), offsetY: String(l.offset.y), mobileFullscreen: l.mobile.fullscreen,
        startMode: eff.behavior.start_mode,
        preChatMode: !pc.enabled ? 'none' : visible.length <= 1 ? 'one' : 'form', preChatTitle: pc.title, submitLabel: pc.submit_label,
        fields: (pc.fields as Doc[]).map(f => ({
            key: f.key, type: f.type, label: f.label, placeholder: f.placeholder || '', required: !!f.required,
            choicesText: formatChoices(f.choices), maxLength: f.max_length ? String(f.max_length) : '',
        })),
        guestAllowed: eff.identity.guest_allowed, authenticatedOnly: eff.identity.authenticated_only, locale: eff.locale, direction: eff.direction,
    };
};

const toInt = (v: string, fallback: number) => (/^\d+$/.test(v.trim()) ? parseInt(v, 10) : fallback);

/** The full document to PUT (the server stores exactly this and merges it over its defaults). */
export const docFromForm = (f: WidgetForm): Doc => ({
    version: 1,
    launcher: {
        enabled: f.launcherEnabled, mode: f.launcherMode, position: f.position, label: f.label.trim(), tooltip: f.tooltip.trim(),
        icon: f.icon, color: f.color.toUpperCase(), greeting: f.greeting.trim(), auto_open: f.autoOpen,
        offset: { x: toInt(f.offsetX, 20), y: toInt(f.offsetY, 20) }, mobile: { fullscreen: f.mobileFullscreen },
    },
    behavior: { start_mode: f.startMode },
    pre_chat: {
        enabled: f.preChatMode !== 'none', title: f.preChatTitle.trim(), submit_label: f.submitLabel.trim(),
        fields: (f.preChatMode === 'none' ? [] : f.preChatMode === 'one' ? f.fields.slice(0, 1) : f.fields).map((fl, i) => ({
            key: fl.key.trim(), type: fl.type, label: fl.label.trim(), placeholder: fl.placeholder.trim(), required: fl.required, order: i + 1,
            ...(CHOICE_TYPES.includes(fl.type) ? { choices: parseChoices(fl.choicesText) } : {}),
            ...(fl.maxLength.trim() ? { max_length: toInt(fl.maxLength, 0) } : {}),
        })),
    },
    identity: { guest_allowed: f.guestAllowed && !f.authenticatedOnly, authenticated_only: f.authenticatedOnly },
    locale: f.locale, direction: f.direction,
});

/** A stable key from a label for newly added questions (the admin can still edit it). */
export const suggestKey = (label: string, taken: string[]): string => {
    const base = (label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'q').replace(/^[0-9]/, 'q$&').slice(0, 30);
    let key = base, n = 2;
    while (taken.includes(key)) key = `${base}_${n++}`;
    return key;
};

export const embedSnippet = (publicKey: string, apiBase: string, scriptUrl: string): string =>
    `<script src="${scriptUrl}"></script>\n<script>\n  RastiChat.init({\n    projectKey: "${publicKey}",\n    apiBase: "${apiBase}",\n    wsBase: "${apiBase.replace(/^http/, 'ws').replace(/\/api\/v1$/, '/ws')}",\n    // Signed-in customers: return a short-lived assertion minted by YOUR backend (never hard-code one).\n    // bootstrap: async () => (await fetch('/chat/identity', { credentials: 'include' })).text(),\n  });\n</script>`;
