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
 */
export type LiveSocket = Pick<WebSocket, 'send' | 'close' | 'readyState'>;

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
    onClose?: (code?: number) => void;
}

export class TicketSocket implements LiveSocket {
    private ws: WebSocket | null = null;
    private authenticated = false;
    private closedByCaller = false;

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
        this.ws?.close();
    }

    private async open(): Promise<void> {
        const { apiBase, wsBase, getToken, path, ticketRequest, onMessage, onOpen, onClose } = this.opts;
        let ticket: string;
        try {
            const res = await fetch(`${apiBase}/ws/ticket/`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
                body: JSON.stringify(ticketRequest),
            });
            if (!res.ok) throw new Error(`ticket request failed (${res.status})`);
            ticket = (await res.json()).ticket;
        } catch {
            onClose?.();
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
                    onOpen?.();
                }
                return;
            }
            onMessage(data);
        };
        ws.onclose = (event: CloseEvent) => {
            this.authenticated = false;
            onClose?.(event?.code);
        };
    }
}
