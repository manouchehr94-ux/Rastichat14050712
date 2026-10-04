"""A closed, allowlist-only Markdown-to-HTML renderer for Knowledge Base
article bodies.

This is deliberately NOT a general-purpose Markdown library and NOT an HTML
sanitizer bolted onto one. The article `body` field is always plain-text
Markdown source; this renderer parses a small, fixed subset of Markdown
syntax and emits ONLY the handful of HTML tags it constructs itself, with
every piece of user-authored text passed through `html.escape()` before
being placed inside a tag. Raw HTML written by the author is never
interpreted as markup — a literal `<script>` in the source renders as the
visible text `&lt;script&gt;`, not a live tag. There is no code path here
that can ever emit a tag/attribute the allowlist below doesn't already
know about, so there is nothing to "sanitize" after the fact.

Supported syntax: `#`..`######` headings, blank-line-separated paragraphs,
`-`/`*` unordered and `1.` ordered lists, fenced ``` code blocks, inline
`code`, `**bold**`, `*italic*`, `[text](url)` links, and bare image
references `![alt](url)`. Anything else is treated as literal paragraph
text.
"""
import html
import re
from urllib.parse import urlparse

_ALLOWED_URL_SCHEMES = {'http', 'https', 'mailto'}


class MarkdownRenderError(Exception):
    pass


def _safe_url(url: str) -> str | None:
    """Returns the URL unchanged if it uses an allowed scheme or is a
    scheme-relative path (no scheme at all — e.g. `/kb/articles/...` or a
    relative attachment link); otherwise returns None so the caller can
    drop the link/image entirely rather than ever emit `javascript:`,
    `data:`, `vbscript:`, or any other unsafe scheme.
    """
    url = (url or '').strip()
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.scheme:
        # No scheme: relative or root-relative path. Reject scheme-like
        # prefixes that urlparse might not catch (e.g. backslash tricks).
        if url.lower().startswith(('javascript:', 'data:', 'vbscript:', 'file:')):
            return None
        return url
    if parsed.scheme.lower() in _ALLOWED_URL_SCHEMES:
        return url
    return None


_INLINE_LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)\s]+)\)')
_INLINE_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)\)')
_INLINE_CODE_RE = re.compile(r'`([^`]+)`')
_INLINE_BOLD_RE = re.compile(r'\*\*([^*]+)\*\*')
_INLINE_ITALIC_RE = re.compile(r'(?<!\*)\*([^*]+)\*(?!\*)')

MAX_BODY_LENGTH = 200_000


def _render_inline(text: str) -> str:
    """Escapes text, THEN applies inline markup on top of the escaped
    string — so the only literal `<`/`>` characters that can ever appear in
    the output are the ones this function places itself, never anything
    from the author's raw input.
    """
    escaped = html.escape(text)

    def _image(match):
        alt = html.escape(match.group(1))
        url = _safe_url(html.unescape(match.group(2)))
        if not url:
            return alt
        return f'<img src="{html.escape(url, quote=True)}" alt="{alt}" loading="lazy">'

    def _link(match):
        label = match.group(1)
        url = _safe_url(html.unescape(match.group(2)))
        if not url:
            return label
        return f'<a href="{html.escape(url, quote=True)}" rel="noopener noreferrer" target="_blank">{label}</a>'

    escaped = _INLINE_IMAGE_RE.sub(_image, escaped)
    escaped = _INLINE_LINK_RE.sub(_link, escaped)
    escaped = _INLINE_CODE_RE.sub(lambda m: f'<code>{m.group(1)}</code>', escaped)
    escaped = _INLINE_BOLD_RE.sub(lambda m: f'<strong>{m.group(1)}</strong>', escaped)
    escaped = _INLINE_ITALIC_RE.sub(lambda m: f'<em>{m.group(1)}</em>', escaped)
    return escaped


_HEADING_RE = re.compile(r'^(#{1,6})\s+(.*)$')
_ORDERED_ITEM_RE = re.compile(r'^\d+\.\s+(.*)$')
_UNORDERED_ITEM_RE = re.compile(r'^[-*]\s+(.*)$')
_FENCE_RE = re.compile(r'^```')


def render_markdown(source: str) -> str:
    """Renders `source` (plain-text Markdown) to a safe HTML fragment."""
    if source is None:
        return ''
    if len(source) > MAX_BODY_LENGTH:
        raise MarkdownRenderError('Article body exceeds the maximum allowed length')

    lines = source.replace('\r\n', '\n').split('\n')
    out = []
    i = 0
    list_stack = None  # ('ul' | 'ol', [items]) currently open

    def _flush_list():
        nonlocal list_stack
        if list_stack is None:
            return
        tag, items = list_stack
        out.append(f'<{tag}>')
        for item in items:
            out.append(f'<li>{_render_inline(item)}</li>')
        out.append(f'</{tag}>')
        list_stack = None

    paragraph_buf: list[str] = []

    def _flush_paragraph():
        if paragraph_buf:
            out.append(f'<p>{_render_inline(" ".join(paragraph_buf))}</p>')
            paragraph_buf.clear()

    while i < len(lines):
        line = lines[i]

        if _FENCE_RE.match(line):
            _flush_paragraph()
            _flush_list()
            i += 1
            code_lines = []
            while i < len(lines) and not _FENCE_RE.match(lines[i]):
                code_lines.append(lines[i])
                i += 1
            i += 1  # consume closing fence
            out.append(f'<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>')
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            _flush_paragraph()
            _flush_list()
            level = len(heading.group(1))
            out.append(f'<h{level}>{_render_inline(heading.group(2))}</h{level}>')
            i += 1
            continue

        ordered = _ORDERED_ITEM_RE.match(line)
        unordered = _UNORDERED_ITEM_RE.match(line)
        if ordered or unordered:
            _flush_paragraph()
            tag = 'ol' if ordered else 'ul'
            item_text = (ordered or unordered).group(1)
            if list_stack is None or list_stack[0] != tag:
                _flush_list()
                list_stack = (tag, [])
            list_stack[1].append(item_text)
            i += 1
            continue

        if not line.strip():
            _flush_paragraph()
            _flush_list()
            i += 1
            continue

        paragraph_buf.append(line.strip())
        i += 1

    _flush_paragraph()
    _flush_list()
    return '\n'.join(out)
