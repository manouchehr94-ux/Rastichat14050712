import { describe, it, expect } from 'vitest';
import { parseSsoFragment, safeNext } from './sso';

describe('SSO fragment', () => {
    it('reads the assertion and a safe next path from the fragment', () => {
        expect(parseSsoFragment('#assertion=a.b.c&next=/support')).toEqual({ assertion: 'a.b.c', next: '/support' });
    });
    it('defaults next to / and returns null without an assertion', () => {
        expect(parseSsoFragment('#assertion=a.b.c')).toEqual({ assertion: 'a.b.c', next: '/' });
        expect(parseSsoFragment('')).toBeNull();
        expect(parseSsoFragment('#next=/x')).toBeNull();
    });
    it('never allows an open redirect', () => {
        for (const bad of ['//evil.example', '/\\evil.example', 'https://evil.example', 'javascript:alert(1)', '/ok\nx', '']) {
            expect(safeNext(bad)).toBe('/');
        }
        expect(safeNext('/support?x=1')).toBe('/support?x=1');
    });
});
