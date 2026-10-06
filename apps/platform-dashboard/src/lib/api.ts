const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8080/api/v1';
const WS_BASE = process.env.NEXT_PUBLIC_WS_BASE_URL || 'ws://localhost:8080/ws';

import { TicketSocket } from './ticketSocket';
import { installAttachmentRefresh } from './attachmentRefresh';

export interface SupportSocketMessage { id: string; content: string; sender_type: string; [key: string]: unknown }

export const login = async (email: string, password: string) => {
    const res = await fetch(`${API_BASE}/auth/login/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
    });
    if (!res.ok) throw new Error('Login failed');
    const data = await res.json();
    localStorage.setItem('token', data.access);
    localStorage.setItem('user', JSON.stringify(data.user));
    return data;
};

export const getToken = () => localStorage.getItem('token');
// signed attachment URLs expire: swap in a fresh one (once) when an <img>/<audio> holding one fails to load
installAttachmentRefresh(API_BASE, getToken);

export const fetchPlatformInbox = async () => {
    const res = await fetch(`${API_BASE}/platform/support/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch inbox');
    return res.json();
};

export const fetchPlatformSupportMessages = async (convId: string) => {
    const res = await fetch(`${API_BASE}/platform/support/${convId}/messages/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch messages');
    return res.json();
};

export const assignTicket = async (convId: string) => {
    const res = await fetch(`${API_BASE}/platform/support/${convId}/assign/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}` }
    });
    if (!res.ok) throw new Error('Failed to assign');
    return res.json();
};

export const replyTicket = async (convId: string, content: string, clientId: string) => {
    const res = await fetch(`${API_BASE}/platform/support/${convId}/reply/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, client_message_id: clientId })
    });
    if (!res.ok) throw new Error('Failed to reply');
    return res.json();
};

export const markPlatformRead = async (convId: string) => {
    const res = await fetch(`${API_BASE}/platform/support/${convId}/mark_read/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}` }
    });
    if (!res.ok) throw new Error('Failed to mark read');
    return res.json();
};

/**
 * Credential-free support connection (see ticketSocket.ts). TicketSocket implements the part of the
 * WebSocket API callers use (send / close / readyState), so it is typed as WebSocket and call sites stay unchanged.
 */
export const connectSupportWebSocket = (convId: string, onMessage: (data: SupportSocketMessage) => void): WebSocket =>
    new TicketSocket({
        apiBase: API_BASE, wsBase: WS_BASE, getToken, path: `/v2/support/${convId}/`,
        ticketRequest: { kind: 'support', conversation_id: convId },
        onMessage: (data) => onMessage(data as SupportSocketMessage),
    }) as unknown as WebSocket;
