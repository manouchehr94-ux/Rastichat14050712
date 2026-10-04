"""Validation for visitor/operator chat attachments (images, voice notes).

Extension checks alone are not sufficient (a client can rename any file),
so uploads are validated by sniffing the actual file-signature ("magic
bytes") of the content, independent of the client-supplied filename or
declared Content-Type. The generated storage filename never reuses any
part of the client-supplied name, which also rules out path traversal.
"""
import uuid

from django.conf import settings

MAX_IMAGE_BYTES = getattr(settings, 'MEDIA_UPLOAD_MAX_IMAGE_BYTES', 8 * 1024 * 1024)
MAX_VOICE_BYTES = getattr(settings, 'MEDIA_UPLOAD_MAX_VOICE_BYTES', 15 * 1024 * 1024)

IMAGE_ALLOWED_CONTENT_TYPES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
VOICE_ALLOWED_CONTENT_TYPES = {
    'audio/webm', 'audio/ogg', 'audio/mpeg', 'audio/mp3',
    'audio/wav', 'audio/x-wav', 'audio/mp4', 'audio/m4a', 'audio/x-m4a',
}


class UploadValidationError(Exception):
    """Raised with a message safe to return to the client as-is."""


def _sniff_image(head: bytes):
    if head.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'webp'
    return None


def _sniff_audio(head: bytes):
    if head[:4] == b'\x1a\x45\xdf\xa3':
        return 'webm'
    if head[:4] == b'OggS':
        return 'ogg'
    if head[:3] == b'ID3' or head[:2] in (b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'):
        return 'mp3'
    if head[:4] == b'RIFF' and head[8:12] == b'WAVE':
        return 'wav'
    if head[4:8] == b'ftyp':
        return 'm4a'
    return None


def scan_for_malware(file) -> bool:
    """Optional virus/malware-scan integration point.

    No scanner is wired up in this codebase. If `MEDIA_UPLOAD_SCAN_HOOK` is
    set to a dotted path of a callable (``callable(file) -> bool``, True
    meaning "clean"), it is invoked here. Otherwise uploads are treated as
    clean by default (signature + size validation still applies).
    """
    hook_path = getattr(settings, 'MEDIA_UPLOAD_SCAN_HOOK', None)
    if not hook_path:
        return True
    from django.utils.module_loading import import_string
    scan_fn = import_string(hook_path)
    return bool(scan_fn(file))


def validate_and_normalize_upload(file, message_type):
    """Validate an uploaded chat attachment and rename it to a safe name.

    Raises UploadValidationError on any failure. On success, mutates
    ``file.name`` to a random, collision-safe name using the extension
    derived from the sniffed signature (never from client input) and
    returns the file.
    """
    from .models import Message

    if file.size <= 0:
        raise UploadValidationError('Empty file')

    is_image = message_type == Message.MessageType.IMAGE
    max_bytes = MAX_IMAGE_BYTES if is_image else MAX_VOICE_BYTES
    if file.size > max_bytes:
        raise UploadValidationError(
            f'File exceeds maximum allowed size of {max_bytes // (1024 * 1024)}MB'
        )

    head = file.read(64)
    file.seek(0)
    kind = _sniff_image(head) if is_image else _sniff_audio(head)
    if kind is None:
        raise UploadValidationError('Unrecognized or unsupported file format')

    declared_ct = (getattr(file, 'content_type', '') or '').lower()
    allowed_cts = IMAGE_ALLOWED_CONTENT_TYPES if is_image else VOICE_ALLOWED_CONTENT_TYPES
    if declared_ct and declared_ct not in allowed_cts:
        raise UploadValidationError('Declared content type is not allowed for this message type')

    if not scan_for_malware(file):
        raise UploadValidationError('File failed security scan')

    file.name = f'{uuid.uuid4().hex}.{kind}'
    return file
