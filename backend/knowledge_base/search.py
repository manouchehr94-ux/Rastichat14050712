"""Workspace-scoped Knowledge Base search.

Per spec section 4 this deliberately does NOT reach for Elasticsearch or
Postgres tsvector/ts_rank (which would need a Persian text-search
configuration this project doesn't ship, and whose ranking behavior is much
harder to make deterministic and easy to test). Instead: every article
keeps a denormalized, normalized `search_text` column (see
sync_search_text), and search both normalizes the query the same way and
does a plain `icontains` lookup — simple, works identically on every
Postgres install, and gives fully deterministic results, which matters
because Playwright/tests must never need retries to hide flaky ranking.

Persian normalization folds the two common Arabic/Persian homograph pairs
(ی/ي and ک/ك) to their Persian forms and collapses/­trims whitespace.
English matching is case-insensitive via a plain `.lower()`.
"""
import re

from django.db.models import Case, IntegerField, When

_WHITESPACE_RE = re.compile(r'\s+')

_ARABIC_YEH = 'ي'  # ي
_PERSIAN_YEH = 'ی'  # ی
_ARABIC_KAF = 'ك'  # ك
_PERSIAN_KAF = 'ک'  # ک


def normalize_text(value: str) -> str:
    if not value:
        return ''
    value = value.lower()
    value = value.replace(_ARABIC_YEH, _PERSIAN_YEH).replace(_ARABIC_KAF, _PERSIAN_KAF)
    value = _WHITESPACE_RE.sub(' ', value).strip()
    return value


def build_search_text(article) -> str:
    category_name = article.category.name if article.category_id else ''
    tags = article.tags if isinstance(article.tags, list) else []
    parts = [article.title, article.excerpt, article.body, category_name, ' '.join(str(t) for t in tags)]
    return normalize_text(' '.join(p for p in parts if p))


def sync_search_text(article, save=True):
    article.search_text = build_search_text(article)
    if save:
        article.save(update_fields=['search_text'])
    return article


def search_articles(queryset, query: str):
    """Filters `queryset` to articles whose normalized search_text contains
    the normalized query, ranked deterministically: exact-title match
    first, then title-contains, then everything else, tie-broken by
    -updated_at then id so result order never depends on physical row
    order or floating ranking scores.
    """
    normalized = normalize_text(query)
    if not normalized:
        return queryset.none()

    qs = queryset.filter(search_text__icontains=normalized)
    qs = qs.annotate(
        _rank=Case(
            When(title__iexact=query.strip(), then=0),
            When(title__icontains=query.strip(), then=1),
            When(excerpt__icontains=query.strip(), then=2),
            default=3,
            output_field=IntegerField(),
        ),
    )
    return qs.order_by('_rank', '-updated_at', 'id')
