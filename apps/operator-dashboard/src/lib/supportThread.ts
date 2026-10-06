// Shared rendering rules for tenant <-> platform support threads (the same helpers exist in the platform dashboard).
// Both sides' messages are `sender_type: USER`; the server annotates `sender_side` ('platform' | 'tenant').

export type Side = 'platform' | 'tenant';

/** Whose message is this, from the viewpoint of `me`? Falls back to the legacy rule (every USER message is "mine") when the server did not annotate it. */
export const isMine = (msg: { sender_type: string; sender_side?: string }, me: Side): boolean =>
    msg.sender_side ? msg.sender_side === me : msg.sender_type === 'USER';

const LABELS: Record<string, { tenant: string; platform: string }> = {
    OPEN: { tenant: 'باز', platform: 'باز' },
    PENDING: { tenant: 'در حال بررسی', platform: 'در حال بررسی' },
    WAITING_FOR_PLATFORM: { tenant: 'در انتظار پاسخ پلتفرم', platform: 'در انتظار پاسخ شما' },
    WAITING_FOR_WORKSPACE: { tenant: 'در انتظار پاسخ شما', platform: 'در انتظار پاسخ سازمان' },
    RESOLVED: { tenant: 'حل‌شده', platform: 'حل‌شده' },
    CLOSED: { tenant: 'بسته‌شده', platform: 'بسته‌شده' },
};

export const statusLabel = (status: string, me: Side): string => LABELS[status]?.[me] ?? status;

export const openedByLabel = (side: string): string => (side === 'PLATFORM' ? 'آغاز‌شده توسط پلتفرم' : 'آغاز‌شده توسط سازمان');
