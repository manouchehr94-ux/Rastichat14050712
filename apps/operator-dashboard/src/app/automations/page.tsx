'use client';
import { Fragment, useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
    fetchAutomationRules, createAutomationRule, updateAutomationRule, deleteAutomationRule,
    activateAutomationRule, deactivateAutomationRule, duplicateAutomationRule,
    simulateAutomationDraft, fetchAutomationRegistry,
    fetchAutomationExecutionHistory, fetchScheduledActions, cancelScheduledAction,
    fetchTeams, fetchQueues, fetchTags,
} from '@/lib/api';

// ---------- Persian display labels (backend codes are stable English identifiers) ----------

const TRIGGER_LABELS: Record<string, string> = {
    CONVERSATION_CREATED: 'ایجاد گفتگو', CONVERSATION_QUEUED: 'ورود به صف', CONVERSATION_ASSIGNED: 'واگذاری گفتگو',
    CONVERSATION_UNASSIGNED: 'لغو واگذاری', CONVERSATION_TRANSFERRED: 'انتقال گفتگو', CONVERSATION_ESCALATED: 'تشدید گفتگو',
    CONVERSATION_PRIORITY_CHANGED: 'تغییر اولویت', CONVERSATION_STATUS_CHANGED: 'تغییر وضعیت',
    CUSTOMER_MESSAGE_CREATED: 'پیام مشتری', OPERATOR_MESSAGE_CREATED: 'پیام اپراتور', INTERNAL_NOTE_CREATED: 'یادداشت داخلی',
    SLA_APPROACHING: 'نزدیک شدن به موعد SLA', SLA_BREACHED: 'نقض SLA', RATING_SUBMITTED: 'ثبت امتیاز',
    CONVERSATION_RESOLVED: 'حل شدن گفتگو', CONVERSATION_CLOSED: 'بسته شدن گفتگو', CONVERSATION_REOPENED: 'بازگشایی گفتگو',
    SCHEDULED_TIME_REACHED: 'رسیدن زمان برنامه‌ریزی‌شده',
};

const FIELD_LABELS: Record<string, string> = {
    'conversation.type': 'نوع گفتگو', 'conversation.status': 'وضعیت گفتگو', 'conversation.priority': 'اولویت گفتگو',
    'conversation.queue_id': 'شناسه صف', 'conversation.team_id': 'شناسه تیم', 'conversation.assignee_id': 'شناسه کارشناس',
    'conversation.assigned': 'واگذار شده', 'conversation.created_at': 'زمان ایجاد', 'conversation.waiting_minutes': 'دقایق انتظار',
    'conversation.message_count': 'تعداد پیام‌ها', 'conversation.customer_message_count': 'تعداد پیام‌های مشتری',
    'conversation.operator_message_count': 'تعداد پیام‌های اپراتور', 'conversation.rating': 'امتیاز گفتگو',
    'conversation.sla_state': 'وضعیت SLA', 'customer.tags': 'برچسب‌های گفتگو', 'customer.score': 'امتیاز مشتری',
    'customer.order_count': 'تعداد سفارش‌ها', 'customer.total_spending': 'مجموع خرید', 'customer.location': 'موقعیت مکانی',
    'customer.metadata': 'اطلاعات سفارشی مشتری', 'message.type': 'نوع پیام', 'message.content': 'متن پیام',
    'message.sender_type': 'نوع فرستنده', 'message.attachment_type': 'نوع پیوست', 'operational.business_hours': 'در ساعات کاری',
    'operational.agent_online': 'آنلاین بودن کارشناس', 'operational.agent_at_capacity': 'پر بودن ظرفیت کارشناس',
    'operational.queue_has_capacity': 'ظرفیت خالی صف', 'operational.assignee_is_team_member': 'عضویت کارشناس در تیم',
};

const OPERATOR_LABELS: Record<string, string> = {
    equals: 'برابر است با', not_equals: 'برابر نیست با', in: 'یکی از', not_in: 'هیچ‌کدام از',
    contains: 'شامل می‌شود', not_contains: 'شامل نمی‌شود', starts_with: 'شروع می‌شود با', ends_with: 'پایان می‌یابد با',
    exists: 'وجود دارد', not_exists: 'وجود ندارد', greater_than: 'بزرگ‌تر از', greater_than_or_equal: 'بزرگ‌تر یا مساوی',
    less_than: 'کوچک‌تر از', less_than_or_equal: 'کوچک‌تر یا مساوی', before: 'قبل از', after: 'بعد از',
    is_true: 'درست است', is_false: 'نادرست است',
};

const ACTION_LABELS: Record<string, string> = {
    ASSIGN_TO_AGENT: 'واگذاری به کارشناس', ASSIGN_TO_TEAM: 'واگذاری به تیم', ASSIGN_USING_QUEUE_STRATEGY: 'واگذاری با استراتژی صف',
    UNASSIGN: 'لغو واگذاری', RETURN_TO_QUEUE: 'بازگشت به صف', TRANSFER_TO_TEAM: 'انتقال به تیم', ESCALATE: 'تشدید',
    SET_PRIORITY: 'تنظیم اولویت', SET_STATUS: 'تنظیم وضعیت', ADD_TAG: 'افزودن برچسب', REMOVE_TAG: 'حذف برچسب',
    SEND_CUSTOMER_MESSAGE: 'ارسال پیام به مشتری', CREATE_INTERNAL_NOTE: 'ایجاد یادداشت داخلی', SEND_NOTIFICATION: 'ارسال اعلان',
    NOTIFY_SUPERVISOR: 'اطلاع به سرپرست', REQUEST_RATING: 'درخواست امتیاز', CLOSE_CONVERSATION: 'بستن گفتگو',
    REOPEN_CONVERSATION: 'بازگشایی گفتگو', SCHEDULE_ACTION: 'زمان‌بندی اقدام', CANCEL_SCHEDULED_ACTION: 'لغو اقدام زمان‌بندی‌شده',
};

const EXEC_STATUS_LABELS: Record<string, string> = {
    MATCHED: 'منطبق', NOT_MATCHED: 'عدم تطبیق', SUCCEEDED: 'موفق', PARTIALLY_SUCCEEDED: 'نیمه‌موفق',
    FAILED: 'ناموفق', SKIPPED: 'رد شده', SKIPPED_LOOP: 'رد شده (حلقه)', CANCELLED: 'لغو شده',
};

const SCHED_STATUS_LABELS: Record<string, string> = {
    PENDING: 'در انتظار', RUNNING: 'در حال اجرا', SUCCEEDED: 'موفق', FAILED: 'ناموفق', CANCELLED: 'لغو شده', SKIPPED: 'رد شده',
};

const STARTER_TEMPLATES = [
    {
        name: 'خوش‌آمدگویی خودکار', description: 'ارسال پیام خوش‌آمدگویی هنگام شروع گفتگوی جدید.',
        trigger_type: 'CONVERSATION_CREATED', conditions: {},
        actions: [{ type: 'SEND_CUSTOMER_MESSAGE', params: { template: 'سلام {customer_name}! به {store_name} خوش آمدید. همکاران ما به‌زودی پاسخگوی شما خواهند بود.' } }],
    },
    {
        name: 'درخواست امتیاز پس از بسته شدن', description: 'پس از بسته شدن گفتگو، از مشتری درخواست امتیازدهی می‌شود.',
        trigger_type: 'CONVERSATION_CLOSED', conditions: {},
        actions: [{ type: 'REQUEST_RATING', params: {} }],
    },
    {
        name: 'تشدید خودکار در نقض SLA', description: 'با نقض مهلت SLA، گفتگو به‌صورت خودکار تشدید می‌شود.',
        trigger_type: 'SLA_BREACHED', conditions: {},
        actions: [{ type: 'ESCALATE', params: { reason: 'نقض خودکار SLA' } }],
    },
];

// ---------- Types ----------

interface RegistryFieldInfo { type: string; operators: string[] }
interface RegistryActionParam { kind: string; required: boolean; choices?: string[]; ref?: string }
interface Registry {
    triggers: { value: string; label: string }[];
    condition_fields: Record<string, RegistryFieldInfo>;
    valueless_operators: string[];
    actions: Record<string, { params: Record<string, RegistryActionParam> }>;
}

type ActionParamValue = string | number | ActionDef;
interface ActionDef { type: string; params: Record<string, ActionParamValue> }

interface NamedRef { id: string; name: string }

type JSONPrimitive = string | number | boolean | null;
type JSONValue = JSONPrimitive | JSONValue[];

interface ConditionLeafJSON { field: string; operator: string; value?: JSONValue; path?: string }
interface ConditionAllJSON { all: ConditionNodeJSON[] }
interface ConditionAnyJSON { any: ConditionNodeJSON[] }
interface ConditionNotJSON { not: ConditionNodeJSON }
type ConditionNodeJSON = ConditionAllJSON | ConditionAnyJSON | ConditionNotJSON | ConditionLeafJSON | Record<string, never>;

interface AutomationRule {
    id: string; name: string; description: string; is_active: boolean; trigger_type: string;
    conditions: ConditionNodeJSON; actions: ActionDef[]; priority: number; stop_processing: boolean;
    created_by_name: string | null; updated_by_name: string | null;
    created_at: string; updated_at: string; last_executed_at: string | null; execution_count: number;
    valid_from: string | null; valid_until: string | null;
}

interface ConditionTraceLeaf { type: 'leaf'; result: boolean; field: string; operator: string; value?: JSONValue; actual: JSONValue; error?: string }
interface ConditionTraceEmpty { type: 'empty'; result: boolean }
interface ConditionTraceNot { type: 'not'; result: boolean; child: ConditionTrace }
interface ConditionTraceGroup { type: 'all' | 'any'; result: boolean; children: ConditionTrace[] }
type ConditionTrace = ConditionTraceLeaf | ConditionTraceEmpty | ConditionTraceNot | ConditionTraceGroup;

interface SimulateResult { matched: boolean; condition_trace: ConditionTrace; would_run_actions: { index: number; type: string; params: Record<string, JSONValue> }[] }

interface ActionExecutionRow {
    id: number; action_index: number; action_type: string; status: string; result_summary: unknown;
    affected_object_type: string; affected_object_id: string; error_summary: string; created_at: string;
}
interface ExecutionRow {
    id: number; rule: string | null; rule_name: string; trigger_type: string; conversation: string | null;
    status: string; error_summary: string; created_at: string; action_executions: ActionExecutionRow[];
}
interface ScheduledActionRow {
    id: string; rule: string | null; rule_name: string | null; conversation: string | null; action_definition: ActionDef;
    execute_at: string; status: string; attempts: number; max_attempts: number; last_error: string; created_at: string;
}

type CondUINode =
    | { kind: 'leaf'; field: string; operator: string; value: string; path: string }
    | { kind: 'group'; mode: 'all' | 'any'; children: CondUINode[] }
    | { kind: 'not'; child: CondUINode | null };

function emptyGroup(): CondUINode { return { kind: 'group', mode: 'all', children: [] }; }

function emptyLeaf(registry: Registry): CondUINode {
    const field = Object.keys(registry.condition_fields)[0] || 'conversation.status';
    const operator = registry.condition_fields[field]?.operators[0] || 'equals';
    return { kind: 'leaf', field, operator, value: '', path: '' };
}

function coerceValue(fieldType: string, operator: string, raw: string): JSONValue {
    if (operator === 'in' || operator === 'not_in') {
        const parts = raw.split(',').map(s => s.trim()).filter(Boolean);
        return fieldType === 'number' ? parts.map(Number) : parts;
    }
    if (fieldType === 'number') return Number(raw);
    if (fieldType === 'datetime') return raw ? new Date(raw).toISOString() : raw;
    return raw;
}

function serializeCondition(node: CondUINode, registry: Registry): ConditionNodeJSON {
    if (node.kind === 'group') {
        if (node.children.length === 0) return {};
        const children = node.children.map(c => serializeCondition(c, registry));
        return node.mode === 'all' ? { all: children } : { any: children };
    }
    if (node.kind === 'not') {
        return node.child ? { not: serializeCondition(node.child, registry) } : {};
    }
    const fieldType = registry.condition_fields[node.field]?.type || 'string';
    const valueless = registry.valueless_operators.includes(node.operator);
    const out: ConditionLeafJSON = { field: node.field, operator: node.operator };
    if (!valueless) out.value = coerceValue(fieldType, node.operator, node.value);
    if (node.field === 'customer.metadata') out.path = node.path;
    return out;
}

function deserializeCondition(json: ConditionNodeJSON | undefined): CondUINode {
    if (!json || Object.keys(json).length === 0) return emptyGroup();
    if ('all' in json) return { kind: 'group', mode: 'all', children: json.all.map(deserializeCondition) };
    if ('any' in json) return { kind: 'group', mode: 'any', children: json.any.map(deserializeCondition) };
    if ('not' in json) return { kind: 'not', child: deserializeCondition(json.not) };
    const leaf = json as ConditionLeafJSON;
    const value = Array.isArray(leaf.value) ? leaf.value.join(', ') : (leaf.value ?? '');
    return { kind: 'leaf', field: leaf.field, operator: leaf.operator, value: String(value), path: leaf.path || '' };
}

// ---------- Condition builder (recursive) ----------

function ConditionEditor({ node, onChange, onRemove, registry, depth }: {
    node: CondUINode; onChange: (n: CondUINode) => void; onRemove?: () => void; registry: Registry; depth: number;
}) {
    if (node.kind === 'group') {
        return (
            <div className="border border-gray-200 rounded-lg p-2.5 bg-cream/40" style={{ marginInlineStart: depth ? 8 : 0 }}>
                <div className="flex items-center gap-2 mb-2">
                    <span className="text-xs text-gray-500">همه شرط‌های زیر باید</span>
                    <select value={node.mode} onChange={e => onChange({ ...node, mode: e.target.value as 'all' | 'any' })}
                        className="text-xs border border-gray-200 rounded px-1.5 py-1">
                        <option value="all">همه (AND)</option>
                        <option value="any">یکی (OR)</option>
                    </select>
                    <span className="text-xs text-gray-500">برقرار باشد</span>
                    {onRemove && <button onClick={onRemove} className="ms-auto text-xs text-red-500 hover:underline">حذف گروه</button>}
                </div>
                <div className="flex flex-col gap-2">
                    {node.children.map((child, i) => (
                        <ConditionEditor key={i} node={child} depth={depth + 1} registry={registry}
                            onChange={n => { const next = [...node.children]; next[i] = n; onChange({ ...node, children: next }); }}
                            onRemove={() => onChange({ ...node, children: node.children.filter((_, idx) => idx !== i) })} />
                    ))}
                </div>
                <div className="flex gap-2 mt-2">
                    <button onClick={() => onChange({ ...node, children: [...node.children, emptyLeaf(registry)] })}
                        className="text-xs text-terracotta hover:underline">+ شرط</button>
                    <button onClick={() => onChange({ ...node, children: [...node.children, emptyGroup()] })}
                        className="text-xs text-terracotta hover:underline">+ گروه تودرتو</button>
                    <button onClick={() => onChange({ ...node, children: [...node.children, { kind: 'not', child: emptyLeaf(registry) }] })}
                        className="text-xs text-terracotta hover:underline">+ نقیض (NOT)</button>
                </div>
            </div>
        );
    }

    if (node.kind === 'not') {
        return (
            <div className="border border-dashed border-gray-300 rounded-lg p-2.5" style={{ marginInlineStart: depth ? 8 : 0 }}>
                <div className="flex items-center gap-2 mb-2">
                    <span className="text-xs font-bold text-gray-500">NOT (نقیض)</span>
                    {onRemove && <button onClick={onRemove} className="ms-auto text-xs text-red-500 hover:underline">حذف</button>}
                </div>
                {node.child
                    ? <ConditionEditor node={node.child} depth={depth + 1} registry={registry} onChange={c => onChange({ ...node, child: c })} />
                    : <button onClick={() => onChange({ ...node, child: emptyLeaf(registry) })} className="text-xs text-terracotta">+ شرط</button>}
            </div>
        );
    }

    const fieldType = registry.condition_fields[node.field]?.type || 'string';
    const operators = registry.condition_fields[node.field]?.operators || [];
    const valueless = registry.valueless_operators.includes(node.operator);

    return (
        <div className="flex flex-wrap items-center gap-1.5 bg-white border border-gray-200 rounded-lg p-2" style={{ marginInlineStart: depth ? 8 : 0 }}>
            <select value={node.field} onChange={e => {
                const field = e.target.value;
                const op = registry.condition_fields[field]?.operators[0] || 'equals';
                onChange({ ...node, field, operator: op });
            }} className="text-xs border border-gray-200 rounded px-1.5 py-1 min-w-0">
                {Object.keys(registry.condition_fields).map(f => <option key={f} value={f}>{FIELD_LABELS[f] || f}</option>)}
            </select>
            <select value={node.operator} onChange={e => onChange({ ...node, operator: e.target.value })}
                className="text-xs border border-gray-200 rounded px-1.5 py-1">
                {operators.map(op => <option key={op} value={op}>{OPERATOR_LABELS[op] || op}</option>)}
            </select>
            {node.field === 'customer.metadata' && (
                <input value={node.path} onChange={e => onChange({ ...node, path: e.target.value })} placeholder="کلید (مثلاً vip_level)"
                    className="text-xs border border-gray-200 rounded px-1.5 py-1 w-32" />
            )}
            {!valueless && (
                fieldType === 'datetime'
                    ? <input type="datetime-local" value={node.value} onChange={e => onChange({ ...node, value: e.target.value })} className="text-xs border border-gray-200 rounded px-1.5 py-1" />
                    : <input value={node.value} onChange={e => onChange({ ...node, value: e.target.value })}
                        placeholder={node.operator === 'in' || node.operator === 'not_in' ? 'مقادیر با کاما جدا شوند' : 'مقدار'}
                        className="text-xs border border-gray-200 rounded px-1.5 py-1 flex-1 min-w-[100px]" />
            )}
            {onRemove && <button onClick={onRemove} className="text-xs text-red-500 hover:underline">حذف</button>}
        </div>
    );
}

// ---------- Action builder ----------

function ActionParamField({ name, spec, value, onChange, teams, queues, tags }: {
    name: string; spec: RegistryActionParam; value: ActionParamValue | undefined; onChange: (v: ActionParamValue) => void;
    teams: NamedRef[]; queues: NamedRef[]; tags: NamedRef[];
}) {
    const strValue = value === undefined ? '' : String(value);
    if (spec.choices) {
        return (
            <select value={strValue} onChange={e => onChange(e.target.value)} className="text-xs border border-gray-200 rounded px-1.5 py-1">
                <option value="">انتخاب کنید…</option>
                {spec.choices.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
        );
    }
    if (spec.ref === 'team') {
        return (
            <select value={strValue} onChange={e => onChange(e.target.value)} className="text-xs border border-gray-200 rounded px-1.5 py-1">
                <option value="">انتخاب تیم…</option>
                {teams.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
        );
    }
    if (spec.ref === 'queue') {
        return (
            <select value={strValue} onChange={e => onChange(e.target.value)} className="text-xs border border-gray-200 rounded px-1.5 py-1">
                <option value="">انتخاب صف…</option>
                {queues.map(q => <option key={q.id} value={q.id}>{q.name}</option>)}
            </select>
        );
    }
    if (spec.ref === 'tag') {
        return (
            <select value={strValue} onChange={e => onChange(e.target.value)} className="text-xs border border-gray-200 rounded px-1.5 py-1">
                <option value="">انتخاب برچسب…</option>
                {tags.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
        );
    }
    if (spec.ref === 'user') {
        return <input value={strValue} onChange={e => onChange(e.target.value)} placeholder="شناسه کارشناس (Agent ID)" className="text-xs border border-gray-200 rounded px-1.5 py-1 w-40" />;
    }
    if (spec.kind === 'number') {
        return <input type="number" value={strValue} onChange={e => onChange(e.target.value === '' ? '' : Number(e.target.value))} className="text-xs border border-gray-200 rounded px-1.5 py-1 w-24" />;
    }
    return <input value={strValue} onChange={e => onChange(e.target.value)} placeholder={name} className="text-xs border border-gray-200 rounded px-1.5 py-1 flex-1 min-w-[140px]" />;
}

function ActionRow({ action, onChange, onRemove, registry, allowedTypes, teams, queues, tags }: {
    action: ActionDef; onChange: (a: ActionDef) => void; onRemove?: () => void; registry: Registry;
    allowedTypes: string[]; teams: NamedRef[]; queues: NamedRef[]; tags: NamedRef[];
}) {
    const spec = registry.actions[action.type];
    return (
        <div className="border border-gray-200 rounded-lg p-2.5 bg-white flex flex-col gap-2">
            <div className="flex items-center gap-2">
                <select value={action.type} onChange={e => onChange({ type: e.target.value, params: {} })} className="text-xs border border-gray-200 rounded px-1.5 py-1">
                    {allowedTypes.map(t => <option key={t} value={t}>{ACTION_LABELS[t] || t}</option>)}
                </select>
                {onRemove && <button onClick={onRemove} className="ms-auto text-xs text-red-500 hover:underline">حذف اقدام</button>}
            </div>
            {spec && Object.keys(spec.params).length > 0 && (
                <div className="flex flex-wrap gap-2">
                    {Object.entries(spec.params).map(([name, paramSpec]) => {
                        if (paramSpec.kind === 'object') {
                            const existing = action.params[name];
                            const nested: ActionDef = (existing && typeof existing === 'object') ? existing
                                : { type: allowedTypes.filter(t => !['SCHEDULE_ACTION', 'CANCEL_SCHEDULED_ACTION'].includes(t))[0], params: {} };
                            return (
                                <div key={name} className="w-full">
                                    <div className="text-[11px] text-gray-400 mb-1">اقدامی که اجرا خواهد شد:</div>
                                    <ActionRow action={nested} registry={registry} teams={teams} queues={queues} tags={tags}
                                        allowedTypes={allowedTypes.filter(t => !['SCHEDULE_ACTION', 'CANCEL_SCHEDULED_ACTION'].includes(t))}
                                        onChange={a => onChange({ ...action, params: { ...action.params, [name]: a } })} />
                                </div>
                            );
                        }
                        return (
                            <label key={name} className="flex items-center gap-1.5 text-xs text-gray-500">
                                {name}{paramSpec.required && <span className="text-red-500">*</span>}:
                                <ActionParamField name={name} spec={paramSpec} value={action.params[name]} teams={teams} queues={queues} tags={tags}
                                    onChange={v => onChange({ ...action, params: { ...action.params, [name]: v } })} />
                            </label>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

// ---------- Rule form ----------

function renderTrace(trace: ConditionTrace | undefined, depth = 0): React.ReactNode {
    if (!trace) return null;
    const indent = { marginInlineStart: depth * 12 };
    if (trace.type === 'leaf') {
        return (
            <div style={indent} className={`text-[11px] ${trace.result ? 'text-success' : 'text-gray-400'}`}>
                {trace.result ? '✓' : '✗'} {FIELD_LABELS[trace.field] || trace.field} {OPERATOR_LABELS[trace.operator] || trace.operator}
                {trace.value !== undefined && trace.value !== null ? ` "${trace.value}"` : ''} — مقدار فعلی: {JSON.stringify(trace.actual)}
            </div>
        );
    }
    if (trace.type === 'not') {
        return (
            <div style={indent}>
                <div className={`text-[11px] font-semibold ${trace.result ? 'text-success' : 'text-gray-400'}`}>{trace.result ? '✓' : '✗'} NOT</div>
                {renderTrace(trace.child, depth + 1)}
            </div>
        );
    }
    if (trace.type === 'empty') return <div style={indent} className="text-[11px] text-success">✓ بدون شرط (همیشه منطبق)</div>;
    return (
        <div style={indent}>
            <div className={`text-[11px] font-semibold ${trace.result ? 'text-success' : 'text-gray-400'}`}>{trace.result ? '✓' : '✗'} {trace.type === 'all' ? 'همه (AND)' : 'یکی (OR)'}</div>
            {trace.children.map((c, i) => <div key={i}>{renderTrace(c, depth + 1)}</div>)}
        </div>
    );
}

function RuleForm({ rule, registry, teams, queues, tags, onSaved, onCancel }: {
    rule: AutomationRule | null; registry: Registry; teams: NamedRef[]; queues: NamedRef[]; tags: NamedRef[];
    onSaved: () => void; onCancel: () => void;
}) {
    const [name, setName] = useState(rule?.name || '');
    const [description, setDescription] = useState(rule?.description || '');
    const [triggerType, setTriggerType] = useState(rule?.trigger_type || registry.triggers[0]?.value || '');
    const [priority, setPriority] = useState(rule?.priority ?? 100);
    const [stopProcessing, setStopProcessing] = useState(rule?.stop_processing ?? false);
    const [condition, setCondition] = useState<CondUINode>(rule ? deserializeCondition(rule.conditions) : emptyGroup());
    const [actions, setActions] = useState<ActionDef[]>(rule?.actions?.length ? rule.actions : []);
    const [simConvId, setSimConvId] = useState('');
    const [simResult, setSimResult] = useState<SimulateResult | null>(null);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);

    // A starter-template draft is passed in as a full AutomationRule-shaped
    // object (id: '') so the form can be pre-filled — but it isn't a saved
    // rule yet. Whether to PATCH or POST must key off a real id, not mere
    // object presence, or a template "creation" would PATCH a blank id.
    const isExisting = Boolean(rule?.id);
    const allowedActionTypes = Object.keys(registry.actions);

    const buildPayload = () => ({
        name, description, trigger_type: triggerType, priority, stop_processing: stopProcessing,
        conditions: serializeCondition(condition, registry), actions,
    });

    const handleSimulate = async () => {
        setError(''); setSimResult(null);
        try {
            const payload = buildPayload();
            const result = await simulateAutomationDraft(payload.trigger_type, payload.conditions, payload.actions, simConvId || undefined);
            setSimResult(result);
        } catch (e: unknown) { setError(e instanceof Error ? e.message : 'شبیه‌سازی ناموفق بود'); }
    };

    const handleSave = async () => {
        setError(''); setBusy(true);
        try {
            const payload = buildPayload();
            if (isExisting && rule) await updateAutomationRule(rule.id, payload);
            else await createAutomationRule(payload);
            onSaved();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'ذخیره قانون ناموفق بود');
        } finally { setBusy(false); }
    };

    return (
        <div className="border border-gray-200 rounded-xl bg-white p-4 flex flex-col gap-4">
            <div className="flex items-center justify-between">
                <h2 className="font-bold text-sm text-gray-700">{isExisting ? 'ویرایش قانون' : 'قانون جدید'}</h2>
                <button onClick={onCancel} className="text-xs text-gray-400 hover:text-gray-600">انصراف</button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <label className="flex flex-col gap-1 text-xs text-gray-500">
                    نام قانون
                    <input value={name} onChange={e => setName(e.target.value)} className="text-sm border border-gray-200 rounded-lg px-3 py-2" />
                </label>
                <label className="flex flex-col gap-1 text-xs text-gray-500">
                    رویداد محرک (WHEN)
                    <select value={triggerType} onChange={e => setTriggerType(e.target.value)} className="text-sm border border-gray-200 rounded-lg px-3 py-2">
                        {registry.triggers.map(t => <option key={t.value} value={t.value}>{TRIGGER_LABELS[t.value] || t.label}</option>)}
                    </select>
                </label>
            </div>

            <label className="flex flex-col gap-1 text-xs text-gray-500">
                توضیحات
                <textarea value={description} onChange={e => setDescription(e.target.value)} rows={2} className="text-sm border border-gray-200 rounded-lg px-3 py-2" />
            </label>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <label className="flex flex-col gap-1 text-xs text-gray-500">
                    اولویت اجرا (عدد کوچک‌تر زودتر اجرا می‌شود)
                    <input type="number" value={priority} onChange={e => setPriority(Number(e.target.value))} className="text-sm border border-gray-200 rounded-lg px-3 py-2 w-32" />
                </label>
                <label className="flex items-center gap-2 text-xs text-gray-500 mt-5">
                    <input type="checkbox" checked={stopProcessing} onChange={e => setStopProcessing(e.target.checked)} />
                    پس از اجرای موفق، از اجرای قوانین با اولویت پایین‌تر جلوگیری شود
                </label>
            </div>

            <div>
                <div className="text-xs font-bold text-gray-600 mb-2">شرط‌ها (IF)</div>
                <ConditionEditor node={condition} onChange={setCondition} registry={registry} depth={0} />
            </div>

            <div>
                <div className="flex items-center justify-between mb-2">
                    <div className="text-xs font-bold text-gray-600">اقدام‌ها (THEN)</div>
                    <button onClick={() => setActions([...actions, { type: allowedActionTypes[0], params: {} }])} className="text-xs text-terracotta hover:underline">+ افزودن اقدام</button>
                </div>
                <div className="flex flex-col gap-2">
                    {actions.length === 0 && <div className="text-xs text-gray-400">هنوز اقدامی اضافه نشده است.</div>}
                    {actions.map((a, i) => (
                        <ActionRow key={i} action={a} registry={registry} teams={teams} queues={queues} tags={tags} allowedTypes={allowedActionTypes}
                            onChange={next => { const arr = [...actions]; arr[i] = next; setActions(arr); }}
                            onRemove={() => setActions(actions.filter((_, idx) => idx !== i))} />
                    ))}
                </div>
            </div>

            <div className="border-t border-gray-100 pt-3">
                <div className="text-xs font-bold text-gray-600 mb-2">شبیه‌سازی (بدون اثر واقعی)</div>
                <div className="flex items-center gap-2 mb-2">
                    <input value={simConvId} onChange={e => setSimConvId(e.target.value)} placeholder="شناسه گفتگو برای آزمایش (اختیاری)" className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 flex-1" />
                    <button onClick={handleSimulate} className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-terracotta text-terracotta hover:bg-terracotta-tint">اجرای شبیه‌سازی</button>
                </div>
                {simResult && (
                    <div className="bg-cream/50 rounded-lg p-2.5 text-xs">
                        <div className={`font-bold mb-1 ${simResult.matched ? 'text-success' : 'text-gray-500'}`}>
                            {simResult.matched ? 'شرط‌ها منطبق شدند ✓' : 'شرط‌ها منطبق نشدند ✗'}
                        </div>
                        {renderTrace(simResult.condition_trace)}
                        {simResult.matched && (
                            <div className="mt-2">
                                <div className="font-semibold text-gray-600">اقدام‌هایی که اجرا می‌شوند (بدون اجرای واقعی):</div>
                                {simResult.would_run_actions.map(a => <div key={a.index} className="text-gray-500">— {ACTION_LABELS[a.type] || a.type}</div>)}
                            </div>
                        )}
                    </div>
                )}
            </div>

            {error && <div className="text-xs text-red-500 bg-red-50 border border-red-200 rounded-lg p-2">{error}</div>}

            <div className="flex justify-end gap-2">
                <button onClick={onCancel} className="text-xs px-3 py-2 rounded-lg border border-gray-200 text-gray-500">انصراف</button>
                <button onClick={handleSave} disabled={busy || !name} className="text-xs font-semibold px-4 py-2 rounded-lg bg-terracotta text-white disabled:opacity-50">
                    {busy ? 'در حال ذخیره…' : isExisting ? 'ذخیره تغییرات' : 'ایجاد قانون'}
                </button>
            </div>
        </div>
    );
}

// ---------- Main page ----------

export default function AutomationsPage() {
    const router = useRouter();
    const [tab, setTab] = useState<'rules' | 'history' | 'scheduled'>('rules');
    const [registry, setRegistry] = useState<Registry | null>(null);
    const [rules, setRules] = useState<AutomationRule[]>([]);
    const [teams, setTeams] = useState<NamedRef[]>([]);
    const [queues, setQueues] = useState<NamedRef[]>([]);
    const [tags, setTags] = useState<NamedRef[]>([]);
    const [editing, setEditing] = useState<'new' | AutomationRule | null>(null);
    const [denied, setDenied] = useState(false);
    const [error, setError] = useState('');
    const [toast, setToast] = useState('');

    const loadAll = useCallback(() => {
        Promise.all([fetchAutomationRegistry(), fetchAutomationRules(), fetchTeams(), fetchQueues(), fetchTags()])
            .then(([reg, ruleList, teamList, queueList, tagList]) => {
                setRegistry(reg); setRules(ruleList); setTeams(teamList); setQueues(queueList); setTags(tagList);
            })
            .catch((e: Error) => {
                if (e.message.startsWith('403')) setDenied(true);
                else setError('بارگذاری اتوماسیون‌ها ناموفق بود');
            });
    }, []);

    useEffect(() => {
        if (!localStorage.getItem('token')) { router.push('/login'); return; }
        loadAll();
    }, [loadAll, router]);

    const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

    const handleActivateToggle = async (rule: AutomationRule) => {
        try {
            if (rule.is_active) await deactivateAutomationRule(rule.id); else await activateAutomationRule(rule.id);
            loadAll();
        } catch { showToast('عملیات ناموفق بود'); }
    };

    const handleDuplicate = async (rule: AutomationRule) => {
        try { await duplicateAutomationRule(rule.id); loadAll(); showToast('نسخه غیرفعال از قانون ایجاد شد'); } catch { showToast('کپی‌سازی ناموفق بود'); }
    };

    const handleDelete = async (rule: AutomationRule) => {
        if (!confirm(`قانون «${rule.name}» حذف شود؟`)) return;
        try { await deleteAutomationRule(rule.id); loadAll(); } catch { showToast('حذف ناموفق بود'); }
    };

    const applyTemplate = (tpl: typeof STARTER_TEMPLATES[number]) => {
        setEditing({
            id: '', name: tpl.name, description: tpl.description, is_active: false, trigger_type: tpl.trigger_type,
            conditions: tpl.conditions, actions: tpl.actions, priority: 100, stop_processing: false,
            created_by_name: null, updated_by_name: null, created_at: '', updated_at: '', last_executed_at: null,
            execution_count: 0, valid_from: null, valid_until: null,
        } as AutomationRule);
    };

    if (denied) {
        return (
            <div className="flex h-screen items-center justify-center bg-gray-100" dir="rtl">
                <div className="text-center">
                    <div className="text-lg font-bold text-gray-700">دسترسی محدود شده است</div>
                    <div className="text-sm text-gray-400 mt-1">اتوماسیون‌ها فقط برای مالک یا مدیر فضای کاری قابل مشاهده است.</div>
                    <Link href="/" className="inline-block mt-4 text-sm text-terracotta hover:underline">بازگشت به صندوق ورودی</Link>
                </div>
            </div>
        );
    }

    if (error) return <div className="p-6 text-red-500" dir="rtl">{error}</div>;

    return (
        <div className="min-h-screen bg-gray-100 p-4 md:p-6" dir="rtl">
            <div className="flex items-center justify-between mb-4">
                <h1 className="font-bold text-lg text-gray-800">اتوماسیون و گردش‌کار</h1>
                <Link href="/" className="text-sm text-terracotta hover:underline">بازگشت به صندوق ورودی</Link>
            </div>

            {toast && <div className="mb-3 text-xs bg-success-soft text-success border border-success-soft rounded-lg px-3 py-2">{toast}</div>}

            <div className="flex gap-2 mb-4">
                {(['rules', 'history', 'scheduled'] as const).map(t => (
                    <button key={t} onClick={() => setTab(t)}
                        className={`text-xs font-semibold px-3 py-2 rounded-lg border ${tab === t ? 'bg-terracotta text-white border-terracotta' : 'bg-white border-gray-200 text-gray-500'}`}>
                        {t === 'rules' ? 'قوانین' : t === 'history' ? 'تاریخچه اجرا' : 'اقدامات زمان‌بندی‌شده'}
                    </button>
                ))}
            </div>

            {!registry ? (
                <div className="text-center text-gray-400 py-10">در حال بارگذاری…</div>
            ) : tab === 'rules' ? (
                <div className="flex flex-col gap-4">
                    {editing ? (
                        <RuleForm rule={editing === 'new' ? null : editing} registry={registry} teams={teams} queues={queues} tags={tags}
                            onCancel={() => setEditing(null)} onSaved={() => { setEditing(null); loadAll(); showToast('قانون ذخیره شد'); }} />
                    ) : (
                        <>
                            <div className="flex flex-wrap items-center gap-2">
                                <button onClick={() => setEditing('new')} className="text-xs font-semibold px-3 py-2 rounded-lg bg-terracotta text-white">+ قانون جدید</button>
                                <span className="text-xs text-gray-400">یا شروع از یک الگو:</span>
                                {STARTER_TEMPLATES.map(tpl => (
                                    <button key={tpl.name} onClick={() => applyTemplate(tpl)} className="text-xs px-2.5 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:border-terracotta hover:text-terracotta" title={tpl.description}>
                                        {tpl.name}
                                    </button>
                                ))}
                            </div>

                            <div className="bg-white border border-gray-200 rounded-xl overflow-x-auto">
                                <table className="w-full text-sm text-right">
                                    <thead>
                                        <tr className="text-xs text-gray-400 border-b border-gray-100">
                                            <th className="py-2 px-3 font-medium">نام</th>
                                            <th className="py-2 px-3 font-medium">رویداد</th>
                                            <th className="py-2 px-3 font-medium">وضعیت</th>
                                            <th className="py-2 px-3 font-medium">اولویت</th>
                                            <th className="py-2 px-3 font-medium">اجراها</th>
                                            <th className="py-2 px-3 font-medium">آخرین اجرا</th>
                                            <th className="py-2 px-3 font-medium">عملیات</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {rules.length === 0 && (
                                            <tr><td colSpan={7} className="py-8 text-center text-xs text-gray-400">هنوز قانونی ایجاد نشده است.</td></tr>
                                        )}
                                        {rules.map(rule => (
                                            <tr key={rule.id} className="border-b border-gray-50">
                                                <td className="py-2 px-3">{rule.name}</td>
                                                <td className="py-2 px-3 text-xs text-gray-500">{TRIGGER_LABELS[rule.trigger_type] || rule.trigger_type}</td>
                                                <td className="py-2 px-3">
                                                    <span className={`text-[10.5px] font-semibold px-2 py-0.5 rounded-full ${rule.is_active ? 'bg-success-soft text-success' : 'bg-gray-100 text-gray-500'}`}>
                                                        {rule.is_active ? 'فعال' : 'غیرفعال'}
                                                    </span>
                                                </td>
                                                <td className="py-2 px-3 text-xs">{rule.priority}</td>
                                                <td className="py-2 px-3 text-xs">{rule.execution_count}</td>
                                                <td className="py-2 px-3 text-xs text-gray-400">{rule.last_executed_at ? new Date(rule.last_executed_at).toLocaleString('fa-IR') : '—'}</td>
                                                <td className="py-2 px-3">
                                                    <div className="flex flex-wrap gap-2 text-xs">
                                                        <button onClick={() => setEditing(rule)} className="text-terracotta hover:underline">ویرایش</button>
                                                        <button onClick={() => handleActivateToggle(rule)} className="text-gray-500 hover:underline">{rule.is_active ? 'غیرفعال‌سازی' : 'فعال‌سازی'}</button>
                                                        <button onClick={() => handleDuplicate(rule)} className="text-gray-500 hover:underline">کپی</button>
                                                        <button onClick={() => handleDelete(rule)} className="text-red-500 hover:underline">حذف</button>
                                                    </div>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}
                </div>
            ) : tab === 'history' ? (
                <ExecutionHistoryTab />
            ) : (
                <ScheduledActionsTab />
            )}
        </div>
    );
}

function ExecutionHistoryTab() {
    const [executions, setExecutions] = useState<ExecutionRow[]>([]);
    const [expanded, setExpanded] = useState<number | null>(null);

    useEffect(() => { fetchAutomationExecutionHistory().then(d => setExecutions(d.results ?? d)).catch(() => setExecutions([])); }, []);

    return (
        <div className="bg-white border border-gray-200 rounded-xl overflow-x-auto">
            <table className="w-full text-sm text-right">
                <thead>
                    <tr className="text-xs text-gray-400 border-b border-gray-100">
                        <th className="py-2 px-3 font-medium">قانون</th>
                        <th className="py-2 px-3 font-medium">رویداد</th>
                        <th className="py-2 px-3 font-medium">وضعیت</th>
                        <th className="py-2 px-3 font-medium">گفتگو</th>
                        <th className="py-2 px-3 font-medium">زمان</th>
                        <th className="py-2 px-3 font-medium"></th>
                    </tr>
                </thead>
                <tbody>
                    {executions.length === 0 && <tr><td colSpan={6} className="py-8 text-center text-xs text-gray-400">تاریخچه‌ای وجود ندارد.</td></tr>}
                    {executions.map(ex => (
                        <Fragment key={ex.id}>
                            <tr className="border-b border-gray-50 cursor-pointer" onClick={() => setExpanded(expanded === ex.id ? null : ex.id)}>
                                <td className="py-2 px-3">{ex.rule_name || '—'}</td>
                                <td className="py-2 px-3 text-xs text-gray-500">{TRIGGER_LABELS[ex.trigger_type] || ex.trigger_type}</td>
                                <td className="py-2 px-3 text-xs">{EXEC_STATUS_LABELS[ex.status] || ex.status}</td>
                                <td className="py-2 px-3 text-xs text-gray-400">{ex.conversation ? String(ex.conversation).slice(0, 8) : '—'}</td>
                                <td className="py-2 px-3 text-xs text-gray-400">{new Date(ex.created_at).toLocaleString('fa-IR')}</td>
                                <td className="py-2 px-3 text-xs text-terracotta">{expanded === ex.id ? 'بستن' : 'جزئیات'}</td>
                            </tr>
                            {expanded === ex.id && (
                                <tr>
                                    <td colSpan={6} className="bg-cream/30 px-4 py-3">
                                        {ex.error_summary && <div className="text-xs text-red-500 mb-2">خطا: {ex.error_summary}</div>}
                                        {ex.action_executions.length === 0 && <div className="text-xs text-gray-400">اقدامی اجرا نشد.</div>}
                                        {ex.action_executions.map(a => (
                                            <div key={a.id} className="text-xs flex items-center gap-2 py-1">
                                                <span className={a.status === 'SUCCEEDED' ? 'text-success' : 'text-red-500'}>{a.status === 'SUCCEEDED' ? '✓' : '✗'}</span>
                                                <span>{ACTION_LABELS[a.action_type] || a.action_type}</span>
                                                {a.error_summary && <span className="text-red-400">— {a.error_summary}</span>}
                                            </div>
                                        ))}
                                    </td>
                                </tr>
                            )}
                        </Fragment>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function ScheduledActionsTab() {
    const [rows, setRows] = useState<ScheduledActionRow[]>([]);
    const [toast, setToast] = useState('');

    const load = useCallback(() => { fetchScheduledActions().then(d => setRows(d.results ?? d)).catch(() => setRows([])); }, []);
    useEffect(() => { load(); }, [load]);

    const handleCancel = async (id: string) => {
        try { await cancelScheduledAction(id); load(); } catch { setToast('لغو ناموفق بود'); setTimeout(() => setToast(''), 3000); }
    };

    return (
        <div className="flex flex-col gap-3">
            {toast && <div className="text-xs bg-red-50 text-red-500 border border-red-200 rounded-lg px-3 py-2">{toast}</div>}
            <div className="bg-white border border-gray-200 rounded-xl overflow-x-auto">
                <table className="w-full text-sm text-right">
                    <thead>
                        <tr className="text-xs text-gray-400 border-b border-gray-100">
                            <th className="py-2 px-3 font-medium">قانون</th>
                            <th className="py-2 px-3 font-medium">اقدام</th>
                            <th className="py-2 px-3 font-medium">زمان اجرا</th>
                            <th className="py-2 px-3 font-medium">وضعیت</th>
                            <th className="py-2 px-3 font-medium">تلاش‌ها</th>
                            <th className="py-2 px-3 font-medium">عملیات</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.length === 0 && <tr><td colSpan={6} className="py-8 text-center text-xs text-gray-400">اقدام زمان‌بندی‌شده‌ای وجود ندارد.</td></tr>}
                        {rows.map(row => (
                            <tr key={row.id} className="border-b border-gray-50">
                                <td className="py-2 px-3">{row.rule_name || '—'}</td>
                                <td className="py-2 px-3 text-xs text-gray-500">{ACTION_LABELS[row.action_definition?.type] || row.action_definition?.type}</td>
                                <td className="py-2 px-3 text-xs text-gray-400">{new Date(row.execute_at).toLocaleString('fa-IR')}</td>
                                <td className="py-2 px-3 text-xs">{SCHED_STATUS_LABELS[row.status] || row.status}</td>
                                <td className="py-2 px-3 text-xs">{row.attempts}/{row.max_attempts}</td>
                                <td className="py-2 px-3 text-xs">
                                    {row.status === 'PENDING' && <button onClick={() => handleCancel(row.id)} className="text-red-500 hover:underline">لغو</button>}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
