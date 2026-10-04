# Knowledge Base and Macros — Reference

A practical reference for the two systems added in this phase, not a theoretical architecture document. The authoritative source is always the code — this doc points at it and explains the decisions that aren't obvious from reading a single file.

## Knowledge Base

### Roles and visibility

Two independent axes control who can see/manage what — never confuse them:

| Axis | Values | Who controls it |
|---|---|---|
| **Status** (editorial state) | `DRAFT` → `REVIEW` → `PUBLISHED` → `ARCHIVED` | Any workspace operator; publish/archive are explicit actions (`services.publish_article`/`archive_article`), never implicit |
| **Visibility** (audience) | `INTERNAL` / `CUSTOMER` / `PUBLIC` | Set on the article; only `PUBLISHED` + (`CUSTOMER` or `PUBLIC`) articles are ever returned by the public endpoints |

Category management (`knowledge_base.KnowledgeBaseCategoryViewSet`) is **admin-only** to mutate (`WORKSPACE_OWNER`/`WORKSPACE_ADMIN`) — any workspace operator can list/view. Article authoring is open to any workspace operator, mirroring the trust boundary teams/queues/automations already use: workspace membership is the tenant boundary, role only gates *structural* changes (categories), not day-to-day content authoring.

**Internal never leaks to a visitor.** The public views (`knowledge_base/views.py`, the `Public*` classes) filter on `status=PUBLISHED, visibility__in=[CUSTOMER, PUBLIC]` at the queryset level — there is no code path that serves a DRAFT or INTERNAL article to an unauthenticated caller. A request for a real-but-unauthorized slug and a request for a nonexistent slug both return a plain 404 — existence of non-public content is never inferable from the response shape or status code.

**Trust boundary for public browsing** is `Project.public_key` — the same UUID the widget itself already uses to `POST /widget/init/`. There is no separate "public API key" concept; browsing the KB anonymously requires exactly the same secret an embedder already has for the chat widget.

### Content format and safety

Article `body` is **plain Markdown source**, never raw HTML. Rendering (`knowledge_base/markdown_renderer.py`) is a closed, allowlist-only renderer: it parses a small fixed Markdown subset and emits *only* the tags it constructs itself (`h1`–`h6`, `p`, `ul`/`ol`/`li`, `pre`/`code`, `strong`/`em`, `a`, `img`), with every piece of author text passed through `html.escape()` first. A literal `<script>` in the source renders as the visible text `&lt;script&gt;` — it is never interpreted as markup. Link/image URLs are scheme-allowlisted (`http`/`https`/`mailto`, or scheme-relative); `javascript:`/`data:`/`vbscript:`/`file:` are dropped. There is no "sanitize this HTML afterwards" step because untrusted HTML never enters the render path in the first place — this is a stronger guarantee than sanitizing an open format.

Attachments (`knowledge_base/attachments.py`) are validated by sniffing actual file-signature bytes (not the client-declared filename/Content-Type) — same pattern as `conversations/media_validation.py`. Stored filenames are always freshly generated (`uuid4().hex`), never derived from client input.

### Revisions

Every meaningful content edit (title/excerpt/body changed) creates a new `KnowledgeBaseArticleRevision` row (`services.update_article_content`). Metadata-only changes (category, tags, featured flag, sort order) do **not** create a revision — the content hasn't changed. Restoring a past revision (`services.restore_revision`) **creates a new current revision** with that revision's content; it never deletes or rewrites the revisions that came after it, so restoring old → new → old again is always possible and the full timeline stays intact. `publish_article(article, actor, revision=...)` composes restore-then-publish, so a published article is always reproducible from a concrete `current_revision_number`, never from in-memory-only state.

### Search

Deliberately **not** Elasticsearch or Postgres `tsvector`/`ts_rank` (see `knowledge_base/search.py` for the full rationale) — a Persian tsvector configuration isn't part of this stack, and rank scores are hard to make deterministic/testable. Instead: every article keeps a denormalized `search_text` column (title + excerpt + body + tags + category name, normalized), kept in sync on every save. Normalization (`normalize_text`) folds the two Arabic/Persian homograph pairs (`ي`→`ی`, `ك`→`ک`) and collapses whitespace; English matching is a plain `.lower()`. Search is `icontains` against `search_text`, ranked deterministically (exact title match, then title-contains, then excerpt-contains, then everything else, tie-broken by `-updated_at, id`) — never a floating relevance score, so repeated identical queries always return the same order.

### Sharing into a conversation

`services.share_article_to_conversation` sends a real `Message` (reusing the existing conversation/message/broadcast infrastructure — never a parallel "card" system) with `message_type=ARTICLE` (an additive choice on the existing `Message.MessageType` enum) and a **frozen snapshot** in `metadata.article` (title/excerpt/category/url/image at share time). If the article is edited or deleted afterward, the already-sent message keeps showing exactly what was shared — it is never a live view of the current article. Sharing is idempotent on `client_message_id`, same as every other message-creating code path in this codebase.

## Macros

### Action registry

`macros/schema.py::ACTION_PARAM_SPECS` is the single source of truth — the same declarative-registry pattern as `automations/schema.py`. 14 action types, each with hardcoded required/optional params, `choices` for enums, and `ref` for workspace-scoped foreign keys (validated to belong to the *target* workspace at save time, never trusted from the client):

`SEND_REPLY`, `SEND_ARTICLE`, `ADD_TAG`, `REMOVE_TAG`, `SET_PRIORITY`, `SET_STATUS`, `ASSIGN_TO_AGENT`, `ASSIGN_TO_TEAM`, `RETURN_TO_QUEUE`, `TRANSFER_TO_TEAM`, `CREATE_INTERNAL_NOTE`, `REQUEST_RATING`, `CLOSE_CONVERSATION`, `REOPEN_CONVERSATION`.

There is no separate `MacroAction` table — `Macro.actions` is a schema-validated JSON list, same convention `AutomationRule.actions` already uses; deterministic order is simply list order. Every handler (`macros/actions.py`) calls the exact same approved domain services the rest of the product uses (`conversations.services.*`, `knowledge_base.services.share_article_to_conversation`) — never a raw model mutation — so a macro's `ASSIGN_TO_AGENT` goes through the identical capacity check (`conversations.services._require_capacity`) a human clicking "assign" does, and can fail the same way (409, no partial state).

### Execution and idempotency

`macros/services.execute_macro(macro, conversation, actor, idempotency_key)`:

1. Rejects an inactive macro or a conversation from a different workspace before anything runs.
2. Reserves the `idempotency_key` via a DB unique constraint on `(workspace, idempotency_key)`. A **second call with the same key** — a double-click, or a browser retry after a timeout — finds the already-reserved/completed row and returns it **without running anything again**.
3. Runs every action in order via `_run_pending_actions`, continuing past a single action's failure (same philosophy as the automation engine): each action gets its own `MacroActionExecution` row (`SUCCEEDED`/`FAILED`), and the final `MacroExecution.status` is computed *after* the loop from a fresh read of all action rows — never from state captured before a retry, which would otherwise permanently pin a fixed-and-retried execution at `PARTIALLY_SUCCEEDED`.
4. `retry_macro_execution` re-enters the same `_run_pending_actions` — it only re-attempts action indexes that never reached `SUCCEEDED`, so a retry can never duplicate an already-completed reply/tag/assignment/note.

Execution statuses: `PENDING` (reserved, mid-run — only ever observed if the process crashes between steps 2 and 3) → `SUCCEEDED` / `PARTIALLY_SUCCEEDED` / `FAILED` / `CANCELLED`.

### Variables

`macros/templating.py::ALLOWED_VARIABLES` is a strict, hardcoded allowlist: `customer_name`, `store_name`, `conversation_id`, `agent_name`, `queue_name`, `team_name`, `article_title`, `order_number`, `product_name`. Interpolation is a whitelist regex substitution (never `.format()`/`eval`/dotted attribute traversal) — any `{token}` not in the allowlist (including anything secret/token/password-shaped) is left as **literal text**, never evaluated and never an error. There is deliberately no internal-note-only variable — the same allowlist is used for every action type, so there is nothing an internal note's variables could leak into a customer-facing reply.

### Permissions

`macros/permissions.py` resolves every check against the macro's **own** `workspace_id` — never "any workspace the user happens to belong to". A user who is `WORKSPACE_OWNER` in Workspace A and only `WORKSPACE_OPERATOR` in Workspace B cannot manage or view Workspace B's macros with Workspace A's privileges.

| Visibility | Who can view/execute | Who can create | Who can edit/delete |
|---|---|---|---|
| `PRIVATE` | Owner only | Any operator (for themselves) | Owner only (never visibility/owner/team fields — see below) |
| `TEAM` | Active members of that team | A `SUPERVISOR` of that team | Owner/Admin only |
| `WORKSPACE` | Any workspace member | Owner/Admin only | Owner/Admin only |

Owner/Admin can always manage everything in their own workspace. A non-admin editing their own PRIVATE macro can change its content/actions/active-state, but the API strips `visibility`/`owner`/`team` from their request before validating — an operator can never self-promote a PRIVATE macro to `TEAM`/`WORKSPACE` visibility by editing it.

## Known limitations (deliberate simplifications, disclosed)

- **No per-workspace policy toggle** for "operators may create private/team macros" — the spec allowed this to be gated by workspace policy; this implementation enables it by default for any operator (private macros for themselves, team macros only if they're a supervisor of that team) rather than building a new settings model for a single boolean. Revisit if a workspace genuinely needs to lock this down further.
- **No Elasticsearch, no Postgres tsvector** for KB search — see the Search section above. Fine for a single workspace's realistic article count; would need revisiting well before tens of thousands of articles.
- **No REQUEST_IMAGE action type** — the task's example (Damaged Product macro) mentions "request image"; the minimum action list in scope for this phase doesn't include a dedicated action for it, so the seeded starter template uses `SEND_REPLY` with a templated message asking for a photo instead of a first-class action.
- **Starter macro templates reference the two teams the existing seed already creates** (فروش/تیم فنی) rather than inventing a "Finance"/"Returns" team not otherwise part of this product's fixtures — see `seed_data.py`.
