"""Per-project allowed domains (`Project.allowed_domains`).

`allowed_domains` is a comma/space/newline separated list of *web origins' hosts* that may embed the
project's widget. Entries (normalised, lower-case):

* `shop.example.com`            — that host, default port only (origin without an explicit port)
* `shop.example.com:8443`       — that host AND port
* `*.example.com`               — any sub-domain of example.com (never the apex itself); at least two labels
                                  after the wildcard so `*.com` / `*` are refused
* a scheme/path is tolerated on input (`https://shop.example.com/`) and stripped

IMPORTANT — what this is and is not: an `Origin` check stops OTHER WEBSITES' browsers from using a
project key (embedding abuse, session farming, cross-site calls). It is not authentication: any non-browser
client can send any `Origin`. Authorization always comes from the session / ticket / JWT, never from the origin.
"""
import re
import time
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

_LABEL = re.compile(r'^(?!-)[a-z0-9-]{1,63}(?<!-)$')
_SPLIT = re.compile(r'[,\s;]+')
_DEFAULT_PORTS = {'http': 80, 'https': 443}


def _split_entry(entry):
    """-> (host, port|None, wildcard) or raises ValidationError."""
    raw = entry.strip().lower()
    if not raw:
        raise ValidationError('Empty domain.')
    if '://' in raw:
        parts = urlsplit(raw)
        if parts.scheme not in ('http', 'https') or parts.path not in ('', '/') or parts.query or parts.fragment:
            raise ValidationError(f'"{entry}" must be a bare host (optionally with scheme and port).')
        raw = parts.netloc
    if any(ch in raw for ch in '/?#@\\ '):
        raise ValidationError(f'"{entry}" must be a bare host (optionally with a port).')
    host, _, port = raw.partition(':')
    port_num = None
    if port:
        if not port.isdigit() or not (1 <= int(port) <= 65535):
            raise ValidationError(f'"{entry}" has an invalid port.')
        port_num = int(port)
    wildcard = host.startswith('*.')
    if wildcard:
        host = host[2:]
        if host.count('.') < 1:
            raise ValidationError(f'"{entry}" is too broad: a wildcard needs at least a registrable domain (*.example.com).')
    labels = host.split('.')
    if not host or any(not _LABEL.match(label) for label in labels):
        raise ValidationError(f'"{entry}" is not a valid host name.')
    if all(label.isdigit() for label in labels):
        if wildcard:
            raise ValidationError(f'"{entry}": wildcards on IP addresses are not allowed.')
    return host, port_num, wildcard


def normalize_entry(entry):
    host, port, wildcard = _split_entry(entry)
    return ('*.' if wildcard else '') + host + (f':{port}' if port else '')


def parse_allowed_domains(text):
    """Strict parse (validators / serializers): list of normalised unique entries, or ValidationError."""
    entries, errors = [], []
    for part in _SPLIT.split(text or ''):
        if not part:
            continue
        try:
            norm = normalize_entry(part)
        except ValidationError as exc:
            errors.extend(exc.messages)
            continue
        if norm not in entries:
            entries.append(norm)
    if errors:
        raise ValidationError(errors)
    return entries


def validate_allowed_domains(value):
    parse_allowed_domains(value)


def normalize_allowed_domains_text(text):
    """Canonical comma-separated form; unparseable input is returned unchanged (it never matches anything)."""
    try:
        return ', '.join(parse_allowed_domains(text))
    except ValidationError:
        return text


def lenient_entries(text):
    """Entries that parse (invalid ones are skipped — they can never authorise an origin)."""
    out = []
    for part in _SPLIT.split(text or ''):
        if not part:
            continue
        try:
            out.append(_split_entry(part))
        except ValidationError:
            continue
    return out


def parse_origin(origin):
    """`https://shop.example.com:8443` -> (scheme, host, port-or-default). None for anything not a web origin
    (missing, 'null', other schemes, garbage)."""
    if not origin or not isinstance(origin, str) or origin.strip().lower() == 'null':
        return None
    try:
        parts = urlsplit(origin.strip().lower())
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if parts.scheme not in _DEFAULT_PORTS or not host or parts.path not in ('', '/') or parts.query or parts.fragment:
        return None
    return parts.scheme, host, port if port is not None else _DEFAULT_PORTS[parts.scheme]


def origin_matches(origin, entries):
    """True iff `origin` is covered by one of the parsed `entries` ((host, port|None, wildcard) tuples)."""
    parsed = parse_origin(origin)
    if parsed is None:
        return False
    scheme, host, port = parsed
    for entry_host, entry_port, wildcard in entries:
        if entry_port is None:
            if port != _DEFAULT_PORTS[scheme]:
                continue
        elif entry_port != port:
            continue
        if wildcard:
            if host.endswith('.' + entry_host) and host != entry_host:
                return True
        elif host == entry_host:
            return True
    return False


# ----------------------------------------------------------------- policy
class OriginDecision:
    ALLOWED = 'allowed'
    NOT_ALLOWED = 'origin_not_allowed'
    REQUIRED = 'origin_required'
    NO_DOMAINS = 'no_domains_configured'


def decide_origin(project, origin, *, establishing, require_domains=False):
    """Policy for one request against one project.

    * No domains configured: allowed (legacy, protected only by the global CORS list) unless
      `require_domains` (WIDGET_REQUIRE_ALLOWED_DOMAINS) says an unconfigured project is refused.
    * Domains configured, `Origin` present: it must match.
    * Domains configured, `Origin` absent: refused when `establishing` a session (browsers always send Origin on a
      cross-site init, so a browserless caller cannot be tied to a domain); tolerated for calls that already carry a
      valid session credential (same-origin GETs and non-browser clients are authenticated by the credential itself).
    """
    entries = lenient_entries(project.allowed_domains)
    if not entries:
        return OriginDecision.NO_DOMAINS if require_domains else OriginDecision.ALLOWED
    if not origin:
        return OriginDecision.REQUIRED if establishing else OriginDecision.ALLOWED
    return OriginDecision.ALLOWED if origin_matches(origin, entries) else OriginDecision.NOT_ALLOWED


# --------------------------------------------- union of all active projects' domains
_CACHE = {'at': 0.0, 'entries': []}


def all_project_entries(ttl_seconds=60):
    """Parsed entries of every active project in an active workspace (cached): used so CORS preflights and the
    WebSocket handshake — which cannot know the project yet — accept a configured store domain without anyone editing
    the global environment list. The per-project check above remains the real gate."""
    now = time.monotonic()
    if now - _CACHE['at'] > ttl_seconds:
        from .models import Project
        entries = []
        for text in Project.objects.filter(is_active=True, workspace__is_active=True).exclude(allowed_domains='').values_list('allowed_domains', flat=True):
            entries.extend(lenient_entries(text))
        _CACHE.update(at=now, entries=entries)
    return _CACHE['entries']


def clear_entries_cache():
    _CACHE.update(at=0.0, entries=[])


def sync_allowed_domains(project, hostnames):
    """Idempotently merge verified hostnames (e.g. a RastiSi store's verified domains) into the project's list.
    Returns True if the project changed. Never removes an entry an operator added by hand."""
    current = parse_allowed_domains(project.allowed_domains)
    merged = list(current)
    for hostname in hostnames:
        norm = normalize_entry(hostname)
        if norm not in merged:
            merged.append(norm)
    if merged == current:
        return False
    project.allowed_domains = ', '.join(merged)
    project.save(update_fields=['allowed_domains', 'updated_at'])
    clear_entries_cache()
    return True
