/**
 * WebSocket authenticated with a short-lived, single-use TICKET sent in the first frame.
 *
 * Browsers cannot put an Authorization header on a WebSocket handshake, so the JWT used to
 * ride in the URL path — and from there into proxy access logs and the console. Now an
 * authenticated REST call (`POST /ws/ticket/`) mints a 30-second single-use ticket bound to
 * this user and conversation; the socket URL carries no credential at all.
 *
 * `readyState` reports OPEN only once the server has acknowledged the ticket (`auth.ok`), so
 * existing callers' `ws.readyState === WebSocket.OPEN` guards also mean "authenticated".
 *
 * Reconnection (found missing on the isolated staging stack: a backend restart, a deploy or a proxy idle timeout left
 * every open dashboard silently stale): when the connection drops for any reason other than the caller closing it, or the
 * server saying access is gone, the socket reconnects with exponential backoff and a FRESH ticket each time (tickets are
 * single use). `onReconnect` fires once the new connection is authenticated, so the caller can resynchronise whatever it
 * missed while disconnected.
 */
export type LiveSocket = Pick<WebSocket, 'send' | 'close' | 'readyState'>;

/** 4403: authenticated once, no longer authorized (revoked) — never retried. */
const CLOSE_REVOKED = 4403;
/** 4401: bad/expired/replayed ticket or auth timeout — retried a few times with a fresh ticket, then given up. */
const CLOSE_UNAUTHENTICATED = 4401;
const MAX_CONSECUTIVE_AUTH_FAILURES = 3;
const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30000;

interface TicketSocketOptions {
    apiBase: string;
    wsBase: string;
    getToken: () => string | null;
    /** WebSocket path below wsBase, e.g. `/v2/dashboard/<conversation>/` */
    path: string;
    /** Body of the ticket request, e.g. `{ kind: 'dashboard_chat', conversation_id }` */
    ticketRequest: Record<string, unknown>;
    onMessage: (data: unknown) => void;
    onOpen?: () => void;
    /** Authenticated again after a drop: refetch what may have been missed. */
    onReconnect?: () => void;
    onClose?: (code?: number) => void;
    /** Set to false to disable reconnection (default true). */
    reconnect?: boolean;
}

export class TicketSocket implements LiveSocket {
    private ws: WebSocket | null = null;
    private authenticated = false;
    private closedByCaller = false;
    private everAuthenticated = false;
    private attempt = 0;
    private authFailures = 0;
    private timer: ReturnType<typeof setTimeout> | null = null;

    constructor(private readonly opts: TicketSocketOptions) {
        void this.open();
    }

    get readyState(): number {
        return this.authenticated && this.ws ? this.ws.readyState : WebSocket.CONNECTING;
    }

    send(data: string): void {
        if (this.authenticated && this.ws) this.ws.send(data);
    }

    close(): void {
        this.closedByCaller = true;
        this.authenticated = false;
        if (this.timer) { clearTimeout(this.timer); this.timer = null; }
        this.ws?.close();
    }

    private scheduleReconnect(): void {
        if (this.closedByCaller || this.opts.reconnect === false || this.timer) return;
        const delay = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** this.attempt) * (0.5 + Math.random() * 0.5);
        this.attempt += 1;
        this.timer = setTimeout(() => { this.timer = null; void this.open(); }, delay);
    }

    private async open(): Promise<void> {
        const { apiBase, wsBase, getToken, path, ticketRequest, onMessage, onOpen, onReconnect, onClose } = this.opts;
        let ticket: string;
        try {
            const res = await fetch(`${apiBase}/ws/ticket/`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
                body: JSON.stringify(ticketRequest),
            });
            if (!res.ok) {
                onClose?.();
                // 401/403/404: the user lost access (or the conversation is gone) — retrying cannot help
                if (res.status >= 500 || res.status === 429) this.scheduleReconnect();
                return;
            }
            ticket = (await res.json()).ticket;
        } catch {
            onClose?.();
            this.scheduleReconnect(); // network down / backend restarting
            return;
        }
        if (this.closedByCaller) return;

        const ws = new WebSocket(`${wsBase}${path}`);
        this.ws = ws;
        ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', ticket }));
        ws.onmessage = (event: MessageEvent) => {
            const data = JSON.parse(event.data);
            if (!this.authenticated) {
                if (data?.type === 'auth.ok') {
                    this.authenticated = true;
                    this.attempt = 0;
                    this.authFailures = 0;
                    const reopened = this.everAuthenticated;
                    this.everAuthenticated = true;
                    onOpen?.();
                    if (reopened) onReconnect?.();
                }
                return;
            }
            onMessage(data);
        };
        ws.onclose = (event: CloseEvent) => {
            const wasAuthenticated = this.authenticated;
            this.authenticated = false;
            const code = event?.code;
            onClose?.(code);
            if (code === CLOSE_REVOKED) return;
            if (code === CLOSE_UNAUTHENTICATED && !wasAuthenticated && ++this.authFailures >= MAX_CONSECUTIVE_AUTH_FAILURES) return;
            this.scheduleReconnect();
        };
    }
}
