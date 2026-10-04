const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8080/api/v1';
const WS_BASE = process.env.NEXT_PUBLIC_WS_BASE_URL || 'ws://localhost:8080/ws';

export const login = async (email: string, password: string) => {
    const res = await fetch(`${API_BASE}/auth/login/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
    });
    if (!res.ok) throw new Error('Login failed');
    const data = await res.json();
    localStorage.setItem('token', data.access);
    localStorage.setItem('user', JSON.stringify(data.user));
    return data;
};

export const getToken = () => localStorage.getItem('token');

export const fetchConversations = async () => {
    const res = await fetch(`${API_BASE}/conversations/customer/`, {
        headers: { 'Authorization': `Bearer ${getToken()}` }
    });
    if (!res.ok) throw new Error('Failed to fetch conversations');
    return res.json();
};

export const fetchMessages = async (convId: string) => {
    const res = await fetch(`${API_BASE}/conversations/${convId}/messages/`, {
        headers: { 'Authorization': `Bearer ${getToken()}` }
    });
    if (!res.ok) throw new Error('Failed to fetch messages');
    return res.json();
};

export const sendMessage = async (convId: string, content: string, clientId: string) => {
    const res = await fetch(`${API_BASE}/conversations/${convId}/send/`, {
        method: 'POST',
        headers: { 
            'Authorization': `Bearer ${getToken()}`,
            'Content-Type': 'application/json' 
        },
        body: JSON.stringify({ content, client_message_id: clientId })
    });
    if (!res.ok) throw new Error('Failed to send message');
    return res.json();
};

export const patchConversation = async (convId: string, patch: Record<string, string>) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/`, {
        method: 'PATCH',
        headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
    });
    if (!res.ok) throw new Error('Failed to update conversation');
    return res.json();
};

export const markConversationRead = async (convId: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/mark_read/`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${getToken()}` },
    });
    if (!res.ok) throw new Error('Failed to mark read');
};

export const assignConversation = async (convId: string, operatorId?: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/assign/`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(operatorId ? { operator_id: operatorId } : {}),
    });
    if (!res.ok) throw new Error('Failed to assign conversation');
    return res.json();
};

export const closeConversation = async (convId: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/close/`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${getToken()}` },
    });
    if (!res.ok) throw new Error('Failed to close conversation');
    return res.json();
};

export const reopenConversation = async (convId: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/reopen/`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${getToken()}` },
    });
    if (!res.ok) throw new Error('Failed to reopen conversation');
    return res.json();
};

export const fetchTeammates = async (convId: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/teammates/`, {
        headers: { 'Authorization': `Bearer ${getToken()}` },
    });
    if (!res.ok) throw new Error('Failed to fetch teammates');
    return res.json();
};

// --- Team operations, queues, priority, SLA ---

export const fetchTeams = async () => {
    const res = await fetch(`${API_BASE}/teams/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch teams');
    const data = await res.json();
    return data.results ?? data;
};

export const fetchQueues = async () => {
    const res = await fetch(`${API_BASE}/queues/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch queues');
    const data = await res.json();
    return data.results ?? data;
};

export const claimConversation = async (convId: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/claim/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}` },
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || 'Failed to claim conversation');
    }
    return res.json();
};

export const transferConversation = async (convId: string, teamId: string, reason?: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/transfer/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ team_id: teamId, reason: reason || '' }),
    });
    if (!res.ok) throw new Error('Failed to transfer conversation');
    return res.json();
};

export const escalateConversation = async (convId: string, reason?: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/escalate/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: reason || '' }),
    });
    if (!res.ok) throw new Error('Failed to escalate conversation');
    return res.json();
};

export const setConversationPriority = async (convId: string, priority: string, reason?: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/set_priority/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ priority, reason: reason || '' }),
    });
    if (!res.ok) throw new Error('Failed to update priority');
    return res.json();
};

export const fetchAssignmentHistory = async (convId: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/assignment_history/`, {
        headers: { 'Authorization': `Bearer ${getToken()}` },
    });
    if (!res.ok) throw new Error('Failed to fetch assignment history');
    return res.json();
};

export const fetchSupervisorSummary = async () => {
    const res = await fetch(`${API_BASE}/supervisor/summary/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (res.status === 403) throw new Error('403: Not authorized to view the supervisor summary');
    if (!res.ok) throw new Error('Failed to fetch supervisor summary');
    return res.json();
};

// --- Internal notes & mentions ---

export const fetchInternalNotes = async (convId: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/internal_notes/`, {
        headers: { 'Authorization': `Bearer ${getToken()}` },
    });
    if (!res.ok) throw new Error('Failed to fetch internal notes');
    return res.json();
};

export const createInternalNote = async (convId: string, content: string, clientId: string, mentionedUserIds: string[] = []) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/internal_notes/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, client_message_id: clientId, mentioned_user_ids: mentionedUserIds }),
    });
    if (!res.ok) throw new Error('Failed to create internal note');
    return res.json();
};

// --- Managed quick replies ---

export const fetchQuickReplies = async (search?: string) => {
    const qs = search?.trim() ? `?search=${encodeURIComponent(search.trim())}` : '';
    const res = await fetch(`${API_BASE}/quick-replies/${qs}`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch quick replies');
    const data = await res.json();
    return data.results ?? data;
};

export const applyQuickReply = async (replyId: string, convId?: string) => {
    const res = await fetch(`${API_BASE}/quick-replies/${replyId}/use/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: convId }),
    });
    if (!res.ok) throw new Error('Failed to use quick reply');
    return res.json();
};

// --- Notifications ---

export const fetchNotifications = async () => {
    const res = await fetch(`${API_BASE}/notifications/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch notifications');
    const data = await res.json();
    return data.results ?? data;
};

export const fetchUnreadNotificationCount = async () => {
    const res = await fetch(`${API_BASE}/notifications/unread_count/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch unread count');
    return res.json();
};

export const markNotificationRead = async (notifId: string) => {
    const res = await fetch(`${API_BASE}/notifications/${notifId}/mark_read/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}` },
    });
    if (!res.ok) throw new Error('Failed to mark notification read');
    return res.json();
};

export const markAllNotificationsRead = async () => {
    const res = await fetch(`${API_BASE}/notifications/mark_all_read/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}` },
    });
    if (!res.ok) throw new Error('Failed to mark all notifications read');
};

export const connectNotificationsWebSocket = <T,>(onMessage: (data: T) => void) => {
    const token = getToken();
    const ws = new WebSocket(`${WS_BASE}/notifications/${token}/`);
    ws.onmessage = (event) => onMessage(JSON.parse(event.data) as T);
    return ws;
};

export const uploadAttachment = async (convId: string, file: File, messageType: 'IMAGE' | 'VOICE', clientId: string, extra?: Record<string, string>) => {
    const form = new FormData();
    form.append('file', file);
    form.append('message_type', messageType);
    form.append('client_message_id', clientId);
    if (extra) Object.entries(extra).forEach(([k, v]) => form.append(k, v));
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/upload/`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${getToken()}` },
        body: form,
    });
    if (!res.ok) throw new Error('Failed to upload attachment');
    return res.json();
};

export const shareProduct = async (convId: string, productId: string, clientId: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/share_product/`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: productId, client_message_id: clientId }),
    });
    if (!res.ok) throw new Error('Failed to share product');
    return res.json();
};

export const requestRating = async (convId: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/request_rating/`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${getToken()}` },
    });
    if (!res.ok) throw new Error('Failed to request rating');
    return res.json();
};

export const fetchProducts = async (search?: string) => {
    const qs = search?.trim() ? `?search=${encodeURIComponent(search.trim())}` : '';
    const res = await fetch(`${API_BASE}/products/${qs}`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch products');
    return res.json();
};

export const fetchTags = async () => {
    const res = await fetch(`${API_BASE}/tags/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch tags');
    return res.json();
};

export const createTag = async (name: string, color?: string) => {
    const res = await fetch(`${API_BASE}/tags/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, color: color || '' }),
    });
    if (!res.ok) throw new Error('Failed to create tag');
    return res.json();
};

export const fetchConversationTags = async (convId: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/tags/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch conversation tags');
    return res.json();
};

export const attachConversationTag = async (convId: string, tagId: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/tags/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag_id: tagId }),
    });
    if (!res.ok) throw new Error('Failed to attach tag');
    return res.json();
};

export const detachConversationTag = async (convId: string, tagId: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/tags/`, {
        method: 'DELETE', headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ tag_id: tagId }),
    });
    if (!res.ok) throw new Error('Failed to detach tag');
    return res.json();
};

export const fetchConversationNotes = async (convId: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/notes/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch notes');
    return res.json();
};

export const createConversationNote = async (convId: string, body: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/notes/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ body }),
    });
    if (!res.ok) throw new Error('Failed to add note');
    return res.json();
};

export const fetchCustomerContext = async (convId: string) => {
    const res = await fetch(`${API_BASE}/conversations/customer/${convId}/customer-context/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch customer context');
    return res.json();
};

export const sendTypingEvent = (ws: WebSocket | null) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'typing' }));
};

export const sendMarkReadEvent = (ws: WebSocket | null) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'mark_read' }));
};

export const connectWebSocket = <T,>(convId: string, onMessage: (data: T) => void) => {
    const token = getToken();
    console.log("Connecting to WS:", `${WS_BASE}/dashboard/${token}/${convId}/`);
    const ws = new WebSocket(`${WS_BASE}/dashboard/${token}/${convId}/`);

    ws.onopen = () => console.log("✅ Dashboard WS Connected");
    ws.onclose = (event) => console.log("❌ Dashboard WS Closed", event.code, event.reason);
    ws.onerror = (error) => console.log("⚠️ Dashboard WS Error", error);

    ws.onmessage = (event) => {
        console.log("📩 Dashboard WS Message", event.data);
        onMessage(JSON.parse(event.data) as T);
    };
    return ws;
};

export const fetchSupportConversations = async () => {
    const res = await fetch(`${API_BASE}/support/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch support tickets');
    return res.json();
};
export const createSupportRequest = async (subject: string, message: string) => {
    const res = await fetch(`${API_BASE}/support/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ subject, initial_message: message })
    });
    if (!res.ok) throw new Error('Failed to create ticket');
    return res.json();
};
export const fetchSupportMessages = async (convId: string) => {
    const res = await fetch(`${API_BASE}/support/${convId}/messages/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch messages');
    return res.json();
};
export const sendSupportMessage = async (convId: string, content: string, clientId: string) => {
    const res = await fetch(`${API_BASE}/support/${convId}/send_message/`, {
        method: 'POST', headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, client_message_id: clientId })
    });
    if (!res.ok) throw new Error('Failed to send message');
    return res.json();
};
export const connectSupportWebSocket = (convId: string, onMessage: (data: any) => void) => {
    const token = getToken();
    const ws = new WebSocket(`${WS_BASE}/dashboard/support/${token}/${convId}/`);
    ws.onmessage = (event) => onMessage(JSON.parse(event.data));
    return ws;
};

// --- Automation rules ---

const authedJson = (method: string, body?: unknown) => ({
    method,
    headers: { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
});

export const fetchAutomationRules = async (params?: { trigger_type?: string; is_active?: boolean }) => {
    const qs = new URLSearchParams();
    if (params?.trigger_type) qs.set('trigger_type', params.trigger_type);
    if (params?.is_active !== undefined) qs.set('is_active', String(params.is_active));
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    const res = await fetch(`${API_BASE}/automations/rules/${suffix}`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (res.status === 403) throw new Error('403: دسترسی به اتوماسیون‌ها فقط برای مدیران فضای کاری است');
    if (!res.ok) throw new Error('Failed to fetch automation rules');
    const data = await res.json();
    return data.results ?? data;
};

export const createAutomationRule = async (payload: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/automations/rules/`, authedJson('POST', payload));
    if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(JSON.stringify(body)); }
    return res.json();
};

export const updateAutomationRule = async (id: string, payload: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/automations/rules/${id}/`, authedJson('PATCH', payload));
    if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(JSON.stringify(body)); }
    return res.json();
};

export const deleteAutomationRule = async (id: string) => {
    const res = await fetch(`${API_BASE}/automations/rules/${id}/`, authedJson('DELETE'));
    if (!res.ok) throw new Error('Failed to delete rule');
};

export const activateAutomationRule = async (id: string) => {
    const res = await fetch(`${API_BASE}/automations/rules/${id}/activate/`, authedJson('POST'));
    if (!res.ok) throw new Error('Failed to activate rule');
    return res.json();
};

export const deactivateAutomationRule = async (id: string) => {
    const res = await fetch(`${API_BASE}/automations/rules/${id}/deactivate/`, authedJson('POST'));
    if (!res.ok) throw new Error('Failed to deactivate rule');
    return res.json();
};

export const duplicateAutomationRule = async (id: string) => {
    const res = await fetch(`${API_BASE}/automations/rules/${id}/duplicate/`, authedJson('POST'));
    if (!res.ok) throw new Error('Failed to duplicate rule');
    return res.json();
};

export const simulateAutomationRule = async (id: string, conversationId?: string) => {
    const res = await fetch(`${API_BASE}/automations/rules/${id}/simulate/`, authedJson('POST', { conversation_id: conversationId || undefined }));
    if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.error || 'Failed to simulate rule'); }
    return res.json();
};

export const validateAutomationDraft = async (conditions: unknown, actions: unknown) => {
    const res = await fetch(`${API_BASE}/automations/validate/`, authedJson('POST', { conditions, actions }));
    return res.json();
};

export const simulateAutomationDraft = async (triggerType: string, conditions: unknown, actions: unknown, conversationId?: string) => {
    const res = await fetch(`${API_BASE}/automations/simulate/`, authedJson('POST', {
        trigger_type: triggerType, conditions, actions, conversation_id: conversationId || undefined,
    }));
    if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.error ? JSON.stringify(body.error) : 'Failed to simulate'); }
    return res.json();
};

export const fetchAutomationRegistry = async () => {
    const res = await fetch(`${API_BASE}/automations/registry/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (res.status === 403) throw new Error('403: دسترسی به اتوماسیون‌ها فقط برای مدیران فضای کاری است');
    if (!res.ok) throw new Error('Failed to fetch automation registry');
    return res.json();
};

export const fetchAutomationExecutionHistory = async (params?: { rule?: string; status?: string }) => {
    const qs = new URLSearchParams();
    if (params?.rule) qs.set('rule', params.rule);
    if (params?.status) qs.set('status', params.status);
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    const res = await fetch(`${API_BASE}/automations/execution-history/${suffix}`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch execution history');
    return res.json();
};

export const fetchScheduledActions = async (params?: { status?: string }) => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set('status', params.status);
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    const res = await fetch(`${API_BASE}/automations/scheduled-actions/${suffix}`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch scheduled actions');
    return res.json();
};

export const cancelScheduledAction = async (id: string) => {
    const res = await fetch(`${API_BASE}/automations/scheduled-actions/${id}/cancel/`, authedJson('POST'));
    if (!res.ok) throw new Error('Failed to cancel scheduled action');
    return res.json();
};

// --- Knowledge Base ---

export const fetchKBCategories = async (workspaceId?: string) => {
    const qs = workspaceId ? `?workspace=${workspaceId}` : '';
    const res = await fetch(`${API_BASE}/kb/categories/${qs}`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch categories');
    const data = await res.json();
    return data.results ?? data;
};

export const createKBCategory = async (payload: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/kb/categories/`, authedJson('POST', payload));
    if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.error || JSON.stringify(body)); }
    return res.json();
};

export const updateKBCategory = async (id: string, payload: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/kb/categories/${id}/`, authedJson('PATCH', payload));
    if (!res.ok) throw new Error('Failed to update category');
    return res.json();
};

export const deleteKBCategory = async (id: string) => {
    const res = await fetch(`${API_BASE}/kb/categories/${id}/`, authedJson('DELETE'));
    if (!res.ok) throw new Error('Failed to delete category');
};

export const fetchKBArticles = async (params?: { q?: string; status?: string; category?: string }) => {
    const qs = new URLSearchParams();
    if (params?.q) qs.set('q', params.q);
    if (params?.status) qs.set('status', params.status);
    if (params?.category) qs.set('category', params.category);
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    const res = await fetch(`${API_BASE}/kb/articles/${suffix}`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch articles');
    const data = await res.json();
    return data.results ?? data;
};

export const fetchKBArticle = async (id: string) => {
    const res = await fetch(`${API_BASE}/kb/articles/${id}/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch article');
    return res.json();
};

export const createKBArticle = async (payload: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/kb/articles/`, authedJson('POST', payload));
    if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.error || JSON.stringify(body)); }
    return res.json();
};

export const updateKBArticle = async (id: string, payload: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/kb/articles/${id}/`, authedJson('PATCH', payload));
    if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.error || JSON.stringify(body)); }
    return res.json();
};

export const publishKBArticle = async (id: string, revisionNumber?: number) => {
    const res = await fetch(`${API_BASE}/kb/articles/${id}/publish/`, authedJson('POST', revisionNumber ? { revision_number: revisionNumber } : {}));
    if (!res.ok) throw new Error('Failed to publish article');
    return res.json();
};

export const archiveKBArticle = async (id: string) => {
    const res = await fetch(`${API_BASE}/kb/articles/${id}/archive/`, authedJson('POST'));
    if (!res.ok) throw new Error('Failed to archive article');
    return res.json();
};

export const duplicateKBArticle = async (id: string) => {
    const res = await fetch(`${API_BASE}/kb/articles/${id}/duplicate/`, authedJson('POST'));
    if (!res.ok) throw new Error('Failed to duplicate article');
    return res.json();
};

export const fetchKBArticleRevisions = async (id: string) => {
    const res = await fetch(`${API_BASE}/kb/articles/${id}/revisions/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch revisions');
    return res.json();
};

export const restoreKBArticleRevision = async (id: string, revisionNumber: number) => {
    const res = await fetch(`${API_BASE}/kb/articles/${id}/revisions/${revisionNumber}/restore/`, authedJson('POST'));
    if (!res.ok) throw new Error('Failed to restore revision');
    return res.json();
};

export const shareKBArticle = async (id: string, conversationId: string, clientId: string) => {
    const res = await fetch(`${API_BASE}/kb/articles/${id}/share/`, authedJson('POST', {
        conversation_id: conversationId, client_message_id: clientId, base_url: typeof window !== 'undefined' ? window.location.origin : '',
    }));
    if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.error || 'Failed to share article'); }
    return res.json();
};

export const fetchKBFeedbackSummary = async (id: string) => {
    const res = await fetch(`${API_BASE}/kb/articles/${id}/feedback-summary/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch feedback summary');
    return res.json();
};

// --- Macros ---

export const fetchMacros = async (params?: { workspace?: string; is_active?: boolean; category?: string }) => {
    const qs = new URLSearchParams();
    if (params?.workspace) qs.set('workspace', params.workspace);
    if (params?.is_active !== undefined) qs.set('is_active', String(params.is_active));
    if (params?.category) qs.set('category', params.category);
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    const res = await fetch(`${API_BASE}/macros/${suffix}`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch macros');
    const data = await res.json();
    return data.results ?? data;
};

export const fetchMacro = async (id: string) => {
    const res = await fetch(`${API_BASE}/macros/${id}/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch macro');
    return res.json();
};

export const createMacro = async (payload: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/macros/`, authedJson('POST', payload));
    if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.error || JSON.stringify(body)); }
    return res.json();
};

export const updateMacro = async (id: string, payload: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/macros/${id}/`, authedJson('PATCH', payload));
    if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.error || JSON.stringify(body)); }
    return res.json();
};

export const deleteMacro = async (id: string) => {
    const res = await fetch(`${API_BASE}/macros/${id}/`, authedJson('DELETE'));
    if (!res.ok) throw new Error('Failed to delete macro');
};

export const activateMacro = async (id: string) => {
    const res = await fetch(`${API_BASE}/macros/${id}/activate/`, authedJson('POST'));
    if (!res.ok) throw new Error('Failed to activate macro');
    return res.json();
};

export const deactivateMacro = async (id: string) => {
    const res = await fetch(`${API_BASE}/macros/${id}/deactivate/`, authedJson('POST'));
    if (!res.ok) throw new Error('Failed to deactivate macro');
    return res.json();
};

export const duplicateMacro = async (id: string) => {
    const res = await fetch(`${API_BASE}/macros/${id}/duplicate/`, authedJson('POST'));
    if (!res.ok) throw new Error('Failed to duplicate macro');
    return res.json();
};

export const previewMacro = async (id: string, conversationId: string) => {
    const res = await fetch(`${API_BASE}/macros/${id}/preview/`, authedJson('POST', { conversation_id: conversationId }));
    if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.error || 'Failed to preview macro'); }
    return res.json();
};

export const executeMacro = async (id: string, conversationId: string, idempotencyKey: string) => {
    const res = await fetch(`${API_BASE}/macros/${id}/execute/`, authedJson('POST', {
        conversation_id: conversationId, idempotency_key: idempotencyKey,
    }));
    if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.error || 'Failed to execute macro'); }
    return res.json();
};

export const fetchMacroRegistry = async () => {
    const res = await fetch(`${API_BASE}/macros/registry/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch macro registry');
    return res.json();
};

export const fetchMacroExecutionHistory = async (params?: { workspace?: string; macro?: string; conversation?: string; status?: string }) => {
    const qs = new URLSearchParams();
    if (params?.workspace) qs.set('workspace', params.workspace);
    if (params?.macro) qs.set('macro', params.macro);
    if (params?.conversation) qs.set('conversation', params.conversation);
    if (params?.status) qs.set('status', params.status);
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    const res = await fetch(`${API_BASE}/macros/execution-history/${suffix}`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch macro execution history');
    return res.json();
};

export const fetchMacroExecution = async (id: string) => {
    const res = await fetch(`${API_BASE}/macros/executions/${id}/`, { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (!res.ok) throw new Error('Failed to fetch macro execution');
    return res.json();
};

export const retryMacroExecution = async (id: string) => {
    const res = await fetch(`${API_BASE}/macros/executions/${id}/`, authedJson('POST'));
    if (!res.ok) { const body = await res.json().catch(() => ({})); throw new Error(body.error || 'Failed to retry execution'); }
    return res.json();
};
