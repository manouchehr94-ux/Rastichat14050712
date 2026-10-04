"""Validation for Knowledge Base article attachments (article images and
PDFs). Same signature-sniffing approach as
conversations.media_validation.validate_and_normalize_upload: the actual
file bytes are inspected, never the client-supplied filename or declared
Content-Type, and the stored filename is always freshly generated (never
derived from client input), which also rules out path traversal.
"""
import uuid

from django.conf import settings

MAX_ATTACHMENT_BYTES = getattr(settings, 'KB_UPLOAD_MAX_ATTACHMENT_BYTES', 8 * 1024 * 1024)

ALLOWED_CONTENT_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'application/pdf'}


class UploadValidationError(Exception):
    """Raised with a message safe to return to the client as-is."""


def _sniff(head: bytes):
    if head.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'webp'
    if head[:5] == b'%PDF-':
        return 'pdf'
    return None


def validate_and_normalize_kb_upload(file):
    if file.size <= 0:
        raise UploadValidationError('Empty file')
    if file.size > MAX_ATTACHMENT_BYTES:
        raise UploadValidationError(f'File exceeds maximum allowed size of {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB')

    head = file.read(64)
    file.seek(0)
    kind = _sniff(head)
    if kind is None:
        raise UploadValidationError('Unrecognized or unsupported file format')

    declared_ct = (getattr(file, 'content_type', '') or '').lower()
    if declared_ct and declared_ct not in ALLOWED_CONTENT_TYPES:
        raise UploadValidationError('Declared content type is not allowed for Knowledge Base attachments')

    file.name = f'{uuid.uuid4().hex}.{kind}'
    return file
