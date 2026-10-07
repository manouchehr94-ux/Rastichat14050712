import { describe, it, expect } from 'vitest';
import { docFromForm, formFromEffective, parseChoices, formatChoices, suggestKey, embedSnippet, emptyField } from './widgetConfig';

const effective = () => ({
    launcher: { enabled: true, mode: 'icon', position: 'bottom-right', offset: { x: 20, y: 20 }, label: '', tooltip: '', icon: 'chat', color: '#bc5a38', greeting: '', auto_open: false, mobile: { fullscreen: true } },
    behavior: { start_mode: 'on_load' },
    pre_chat: { enabled: false, title: '', submit_label: '', fields: [] },
    identity: { guest_allowed: true, authenticated_only: false }, locale: 'fa', direction: 'rtl',
});

describe('widget settings helpers', () => {
    it('round-trips the effective configuration through the form', () => {
        const doc = docFromForm(formFromEffective(effective()));
        expect(doc.launcher.color).toBe('#BC5A38');
        expect(doc.launcher.offset).toEqual({ x: 20, y: 20 });
        expect(doc.pre_chat).toEqual({ enabled: false, title: '', submit_label: '', fields: [] });
        expect(doc.identity).toEqual({ guest_allowed: true, authenticated_only: false });
    });

    it('mode "no questions" sends no fields; mode "one question" sends only the first', () => {
        const form = formFromEffective(effective());
        form.fields = [{ ...emptyField(), key: 'a', label: 'A' }, { ...emptyField(), key: 'b', label: 'B' }];
        form.preChatMode = 'none';
        expect(docFromForm(form).pre_chat).toMatchObject({ enabled: false, fields: [] });
        form.preChatMode = 'one';
        expect(docFromForm(form).pre_chat.fields.map((f: { key: string }) => f.key)).toEqual(['a']);
        form.preChatMode = 'form';
        expect(docFromForm(form).pre_chat.fields.map((f: { key: string; order: number }) => [f.key, f.order])).toEqual([['a', 1], ['b', 2]]);
    });

    it('classifies a loaded configuration as none / one / form', () => {
        const e = effective();
        expect(formFromEffective(e).preChatMode).toBe('none');
        e.pre_chat = { enabled: true, title: '', submit_label: '', fields: [{ key: 'q', type: 'text', label: 'Q' }] } as never;
        expect(formFromEffective(e).preChatMode).toBe('one');
        e.pre_chat.fields.push({ key: 'r', type: 'text', label: 'R' } as never);
        expect(formFromEffective(e).preChatMode).toBe('form');
    });

    it('parses and formats choices, including bare labels and pipes in labels', () => {
        expect(parseChoices('order | سفارش\nbilling | پرداخت\n\n')).toEqual([{ value: 'order', label: 'سفارش' }, { value: 'billing', label: 'پرداخت' }]);
        expect(parseChoices('فقط برچسب')).toEqual([{ value: 'c1', label: 'فقط برچسب' }]);
        expect(parseChoices('a | x | y')).toEqual([{ value: 'a', label: 'x | y' }]);
        expect(formatChoices([{ value: 'a', label: 'A' }])).toBe('a | A');
    });

    it('choices only travel with select/radio fields; max_length only when set', () => {
        const form = formFromEffective(effective());
        form.preChatMode = 'form';
        form.fields = [
            { ...emptyField(), key: 's', type: 'select', label: 'S', choicesText: 'a | A' },
            { ...emptyField(), key: 't', type: 'text', label: 'T', choicesText: 'ignored', maxLength: '40' },
        ];
        const [s, t] = docFromForm(form).pre_chat.fields;
        expect(s.choices).toEqual([{ value: 'a', label: 'A' }]);
        expect(t.choices).toBeUndefined();
        expect(t.max_length).toBe(40);
        expect(s.max_length).toBeUndefined();
    });

    it('authenticated-only implies guests are not allowed', () => {
        const form = formFromEffective(effective());
        form.authenticatedOnly = true;
        form.guestAllowed = true;
        expect(docFromForm(form).identity).toEqual({ guest_allowed: false, authenticated_only: true });
    });

    it('suggests unique, schema-valid keys', () => {
        expect(suggestKey('Order number', [])).toBe('order_number');
        expect(suggestKey('Order number', ['order_number'])).toBe('order_number_2');
        expect(suggestKey('شماره سفارش', [])).toBe('q');
        expect(suggestKey('3 items', [])).toMatch(/^q3/);
    });

    it('embed snippet carries only the public key and urls, with the bootstrap left as a commented example', () => {
        const snippet = embedSnippet('KEY', 'https://chat.example.com/api/v1', 'https://chat.example.com/widget.js');
        expect(snippet).toContain('projectKey: "KEY"');
        expect(snippet).toContain('wsBase: "wss://chat.example.com/ws"');
        expect(snippet).toContain('// bootstrap:');
        expect(snippet).not.toMatch(/token|secret|password/i);
    });
});
