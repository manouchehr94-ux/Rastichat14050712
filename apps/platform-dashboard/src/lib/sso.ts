// Host-application SSO landing helpers (Integration Contract v1, §7 "Staff exchange").
//
// The host backend signs a short-lived assertion and sends the browser here with the assertion in the URL
// FRAGMENT (`/sso#assertion=<jwt>&next=/support`). A fragment is never sent to a server, never reaches an access
// log and is not in the Referer, which a query string would be. The page clears it from the address bar before it
// does anything else, then exchanges it for a dashboard access token.

export interface SsoFragment { assertion: string; next: string }

/** Only same-site relative paths: never `//host`, `/\host`, `javascript:` or an absolute URL. */
export const safeNext = (value: string | null | undefined): string => {
    if (!value || !value.startsWith('/') || value.startsWith('//') || value.startsWith('/\\') || /[\u0000-\u001f]/.test(value)) return '/';
    return value;
};

export const parseSsoFragment = (hash: string): SsoFragment | null => {
    const params = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash);
    const assertion = params.get('assertion');
    if (!assertion) return null;
    return { assertion, next: safeNext(params.get('next')) };
};
