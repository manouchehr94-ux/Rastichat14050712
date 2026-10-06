import { describe, it, expect, vi, beforeEach } from 'vitest';
import { attachmentMessageId, refreshAttachmentElement } from './attachmentRefresh';

const ID = '3f2b8c1e-4d5a-4e6f-9a7b-0c1d2e3f4a5b';
const URL_OLD = `https://api.example/api/v1/attachments/${ID}/?sig=old`;

describe('attachment URL refresh', () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let fetchMock: any;
  beforeEach(() => {
    fetchMock = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ attachment_url: `https://api.example/api/v1/attachments/${ID}/?sig=new` }) }));
    // @ts-expect-error test double
    global.fetch = fetchMock;
  });

  it('only recognises signed attachment URLs', () => {
    expect(attachmentMessageId(URL_OLD)).toBe(ID);
    expect(attachmentMessageId('https://example.com/pic.png')).toBeNull();
    expect(attachmentMessageId(null)).toBeNull();
    expect(attachmentMessageId('https://api.example/media/attachments/2026/10/05/abc.png')).toBeNull();
  });

  it('asks for a fresh URL with the bearer header (never in the URL) and swaps it in', async () => {
    const img = document.createElement('img');
    img.setAttribute('src', URL_OLD);
    expect(await refreshAttachmentElement(img, 'https://api.example/api/v1', () => 'JWT')).toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`https://api.example/api/v1/attachments/${ID}/refresh/`);
    expect(url).not.toContain('JWT');
    expect(init.headers.Authorization).toBe('Bearer JWT');
    expect(img.getAttribute('src')).toContain('sig=new');
  });

  it('retries exactly once per element (a really missing file must not loop)', async () => {
    const img = document.createElement('img');
    img.setAttribute('src', URL_OLD);
    await refreshAttachmentElement(img, 'https://api.example/api/v1', () => 'JWT');
    expect(await refreshAttachmentElement(img, 'https://api.example/api/v1', () => 'JWT')).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('does nothing without a session, for foreign images, or when the backend refuses', async () => {
    const foreign = document.createElement('img');
    foreign.setAttribute('src', 'https://example.com/a.png');
    expect(await refreshAttachmentElement(foreign, 'https://api.example/api/v1', () => 'JWT')).toBe(false);
    const signedOut = document.createElement('img');
    signedOut.setAttribute('src', URL_OLD);
    expect(await refreshAttachmentElement(signedOut, 'https://api.example/api/v1', () => null)).toBe(false);
    fetchMock.mockImplementation(() => Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) }));
    const refused = document.createElement('img');
    refused.setAttribute('src', URL_OLD);
    expect(await refreshAttachmentElement(refused, 'https://api.example/api/v1', () => 'JWT')).toBe(false);
    expect(refused.getAttribute('src')).toBe(URL_OLD);
  });

  it('reloads a voice note after swapping the source', async () => {
    const audio = document.createElement('audio');
    audio.setAttribute('src', URL_OLD);
    const load = vi.spyOn(audio, 'load').mockImplementation(() => undefined);
    await refreshAttachmentElement(audio, 'https://api.example/api/v1', () => 'JWT');
    expect(load).toHaveBeenCalledOnce();
    expect(audio.getAttribute('src')).toContain('sig=new');
  });

  it('survives a network failure', async () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error('offline')));
    const img = document.createElement('img');
    img.setAttribute('src', URL_OLD);
    expect(await refreshAttachmentElement(img, 'https://api.example/api/v1', () => 'JWT')).toBe(false);
  });
});
