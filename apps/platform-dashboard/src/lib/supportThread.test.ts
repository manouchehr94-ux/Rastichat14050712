import { describe, it, expect } from 'vitest';
import { isMine, statusLabel, openedByLabel } from './supportThread';

describe('support thread helpers', () => {
    it('uses the server-annotated side when present', () => {
        expect(isMine({ sender_type: 'USER', sender_side: 'platform' }, 'platform')).toBe(true);
        expect(isMine({ sender_type: 'USER', sender_side: 'tenant' }, 'platform')).toBe(false);
        expect(isMine({ sender_type: 'USER', sender_side: 'platform' }, 'tenant')).toBe(false);
    });
    it('falls back to the legacy rule for un-annotated messages', () => {
        expect(isMine({ sender_type: 'USER' }, 'tenant')).toBe(true);
        expect(isMine({ sender_type: 'SYSTEM' }, 'tenant')).toBe(false);
    });
    it('words the status from each side\'s viewpoint', () => {
        expect(statusLabel('WAITING_FOR_WORKSPACE', 'tenant')).toBe('در انتظار پاسخ شما');
        expect(statusLabel('WAITING_FOR_WORKSPACE', 'platform')).toBe('در انتظار پاسخ سازمان');
        expect(statusLabel('WAITING_FOR_PLATFORM', 'platform')).toBe('در انتظار پاسخ شما');
        expect(statusLabel('UNKNOWN', 'tenant')).toBe('UNKNOWN');
    });
    it('labels who opened the thread', () => {
        expect(openedByLabel('PLATFORM')).toContain('پلتفرم');
        expect(openedByLabel('TENANT')).toContain('سازمان');
    });
});
