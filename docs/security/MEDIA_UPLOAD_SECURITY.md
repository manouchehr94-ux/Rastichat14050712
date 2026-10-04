# Media Upload Security

Covers image/voice attachment uploads on both upload endpoints: `CustomerConversationViewSet.upload` (operator, `POST /api/v1/conversations/customer/<id>/upload/`) and `WidgetUploadView` (visitor, `POST /api/v1/widget/conversations/<id>/upload/`).

## Before this pass

Endpoints correctly gated uploads by conversation ownership and `client_message_id` idempotency, but performed **no content validation whatsoever**: any file, regardless of actual content, was accepted as long as the client declared `message_type` as `IMAGE` or `VOICE`. Extension and `Content-Type` header were both client-controlled and unverified. There was no enforced size limit (`FILE_UPLOAD_MAX_MEMORY_SIZE` only controls Django's in-memory-vs-disk-spill threshold, not a rejection bound) and no production storage backend (media was served via Django's dev-only `static()` helper, which serves nothing when `DEBUG=0`).

## What was added (`backend/conversations/media_validation.py`)

1. **Signature ("magic byte") validation**, independent of client-supplied filename/extension/`Content-Type`:
   - Images: JPEG (`\xff\xd8\xff`), PNG (`\x89PNG\r\n\x1a\n`), GIF (`GIF87a`/`GIF89a`), WebP (`RIFF....WEBP`).
   - Audio: WebM (`\x1a\x45\xdf\xa3`), Ogg (`OggS`), MP3 (`ID3` or frame-sync bytes), WAV (`RIFF....WAVE`), M4A (`....ftyp`).
   - A file whose actual bytes don't match a recognized signature for the declared `message_type` is rejected with HTTP 400, regardless of what extension or `Content-Type` the client claimed.
2. **Declared-`Content-Type` cross-check** against an allowlist per type (rejects an obviously wrong declared type even before sniffing, when present).
3. **Enforced size limits**: `MEDIA_UPLOAD_MAX_IMAGE_BYTES` (default 8MB) / `MEDIA_UPLOAD_MAX_VOICE_BYTES` (default 15MB), both overridable via environment variable.
4. **Server-generated storage filenames**: the uploaded file is renamed to `{uuid4().hex}.{sniffed_extension}` before being handed to `Message.attachment`. The client-supplied filename is never used for the storage path, which also rules out path traversal via a crafted filename.
5. **Optional malware-scan hook**: `scan_for_malware(file)` is a no-op by default; if `MEDIA_UPLOAD_SCAN_HOOK` (a dotted import path to a `callable(file) -> bool`) is set, it's invoked and a falsy return rejects the upload. No scanner is wired up in this codebase — this is a documented integration point, not a claim that scanning happens today.
6. **Rate limiting**: both upload endpoints are throttled (`ScopedRateThrottle`, scope `media_upload`, default `30/min`, configurable via `MEDIA_UPLOAD_THROTTLE_RATE`) — per-authenticated-user for operators, per-IP for anonymous visitors (DRF's standard anonymous-throttle behavior).

## Tests (`backend/conversations/tests_media_security.py`)

- A text payload dressed up as a `.png` with `Content-Type: image/png` is rejected (400), and no `Message` row is created.
- A real PNG signature is accepted, and the stored filename is verified to be server-generated (does not contain the original filename).
- An oversized file (>8MB with a valid PNG signature) is rejected.
- PNG bytes submitted as a `VOICE` message are rejected (signature doesn't match any audio container).
- A real WebM signature submitted as `VOICE` is accepted.
- Cross-workspace upload attempts are still rejected (404, pre-existing conversation-ownership check, re-verified alongside the new validation).

## Explicitly not implemented (documented limitation, not silently dropped)

- **Cleanup policy for abandoned uploads**: there is no background job deleting `Message.attachment` files whose parent `Message` row was never successfully created (e.g. a client that uploads a file but the request is interrupted before the DB write completes) or files belonging to conversations that are later deleted. Recommended approach for a production deployment: a periodic management command that walks `MEDIA_ROOT/attachments/` and deletes files older than N days with no matching `Message.attachment` reference, or switching to an object-storage backend with a lifecycle rule.
- **Production storage abstraction**: media is still served via Django's dev-only `static()` helper; nothing serves `/media/` when `DEBUG=0`. A production deployment needs `django-storages` (or equivalent) pointed at S3/GCS/Azure Blob, with `DEFAULT_FILE_STORAGE` configured accordingly. This is an infrastructure/deployment change outside what this backend-code pass covers.
- **Live virus scanning**: the hook exists; no scanner is wired up. Wiring a real scanner (e.g. ClamAV via a sidecar, or a cloud DLP API) is a deployment-specific decision.
