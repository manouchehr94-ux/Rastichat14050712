/**
 * Chat attachment URLs are short-lived signed URLs (`/api/v1/attachments/<message>/?sig=…`, 10 minutes by default). When an
 * <img>/<audio> that holds one fails to load — the URL expired while the page stayed open, or it was minted for the sender and the
 * viewer's access differs — ask the backend for a fresh URL for THIS user and retry exactly once. Done with one capturing `error`
 * listener on the document so no component has to know about expiry.
 */
const ATTACHMENT_PATH = /\/attachments\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\//;
const RETRIED = 'attachmentRetried';

type MediaEl = HTMLImageElement | HTMLAudioElement | HTMLVideoElement;

export function attachmentMessageId(src: string | null): string | null {
    const m = src ? ATTACHMENT_PATH.exec(src) : null;
    return m ? m[1] : null;
}

export async function refreshAttachmentElement(el: MediaEl, apiBase: string, getToken: () => string | null): Promise<boolean> {
    const id = attachmentMessageId(el.getAttribute('src'));
    const token = getToken();
    if (!id || !token || el.dataset[RETRIED]) return false;
    el.dataset[RETRIED] = '1'; // one retry per element: a genuinely missing file must not loop
    try {
        const res = await fetch(`${apiBase}/attachments/${id}/refresh/`, { headers: { Authorization: `Bearer ${token}` } });
        if (!res.ok) return false;
        const { attachment_url: url } = await res.json();
        if (!url) return false;
        el.setAttribute('src', url);
        if (el instanceof HTMLMediaElement) el.load();
        return true;
    } catch {
        return false;
    }
}

let installed = false;

export function installAttachmentRefresh(apiBase: string, getToken: () => string | null): void {
    if (installed || typeof document === 'undefined') return;
    installed = true;
    document.addEventListener('error', (event) => {
        const el = event.target;
        if (el instanceof HTMLImageElement || el instanceof HTMLAudioElement || el instanceof HTMLVideoElement) {
            void refreshAttachmentElement(el, apiBase, getToken);
        }
    }, true);
}
