const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8080/api/v1';
const WS_BASE = process.env.NEXT_PUBLIC_WS_BASE_URL || 'ws://localhost:8080/ws';

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

export const connectSupportWebSocket = (convId: string, onMessage: (data: any) => void) => {
    const token = getToken();
    const ws = new WebSocket(`${WS_BASE}/dashboard/support/${token}/${convId}/`);
    ws.onmessage = (event) => onMessage(JSON.parse(event.data));
    return ws;
};
