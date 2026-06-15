"""
Object storage — AWS S3 (direct, via boto3). Private bucket; reads are served
via short-lived presigned GET URLs. Keys are `{prefix}/{uuid}.{ext}`.
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, UploadFile, status

from app.core.s3 import delete_object, presigned_get_url, put_object, s3_enabled
from app.domains.posts.models import PostMediaType
from app.shared.utils.uploads import validate_audio_magic_bytes, validate_image_magic_bytes

_MAGIC_PEEK_BYTES = 16

_ALLOWED_MIME_PREFIXES = ("image/jpeg", "image/png", "image/webp", "image/gif")

_MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}

# Media MIME → (extension, PostMediaType)
_MEDIA_MIME_MAP: dict[str, tuple[str, PostMediaType]] = {
    "image/jpeg": ("jpg", PostMediaType.image),
    "image/png": ("png", PostMediaType.image),
    "image/webp": ("webp", PostMediaType.image),
    "image/gif": ("gif", PostMediaType.image),
    "video/mp4": ("mp4", PostMediaType.video),
    "video/quicktime": ("mov", PostMediaType.video),
    "video/webm": ("webm", PostMediaType.video),
    "application/pdf": ("pdf", PostMediaType.document),
}

JOURNAL_VOICE_ALLOWED_MIME_TYPES = frozenset(
    {
        "audio/mpeg",
        "audio/mp3",
        "audio/mp4",
        "audio/m4a",
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
        "audio/ogg",
    }
)


def _require_storage() -> None:
    if not s3_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Object storage is not configured.",
        )


def _read_all(file: UploadFile) -> bytes:
    file.file.seek(0)
    data = file.file.read()
    file.file.seek(0)
    return data


def upload_image(file: UploadFile, *, prefix: str) -> str:
    """
    Upload a stream avatar/banner image to S3.

    Returns the S3 object key (persist it; build a fetch URL via
    `generate_signed_url`).
    """
    _require_storage()
    content_type = file.content_type or ""
    if not any(content_type.startswith(p) for p in _ALLOWED_MIME_PREFIXES):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid file type '{content_type}'. Allowed: JPEG, PNG, WebP, GIF.",
        )

    file.file.seek(0)
    header = file.file.read(_MAGIC_PEEK_BYTES)
    validate_image_magic_bytes(header)

    ext = _MIME_TO_EXT.get(content_type, "jpg")
    key = f"{prefix}/{uuid.uuid4()}.{ext}"
    put_object(key=key, body=_read_all(file), content_type=content_type)
    return key


def upload_media(file: UploadFile, *, prefix: str) -> tuple[str, PostMediaType, str]:
    """
    Upload post or journal attachment media to S3.

    Returns (storage_path, PostMediaType, mime_type) where storage_path is the
    S3 object key.
    """
    _require_storage()
    content_type = file.content_type or ""
    if content_type not in _MEDIA_MIME_MAP:
        allowed = ", ".join(_MEDIA_MIME_MAP.keys())
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported media type '{content_type}'. Allowed: {allowed}.",
        )

    ext, media_type = _MEDIA_MIME_MAP[content_type]
    if media_type == PostMediaType.image:
        file.file.seek(0)
        header = file.file.read(_MAGIC_PEEK_BYTES)
        validate_image_magic_bytes(header)

    key = f"{prefix}/{uuid.uuid4()}.{ext}"
    put_object(key=key, body=_read_all(file), content_type=content_type)
    return key, media_type, content_type


def upload_journal_voice_note(file: UploadFile, *, user_prefix: str) -> tuple[str, str]:
    """
    Upload a journal voice attachment to S3.

    Returns (storage_path, normalized_content_type) where storage_path is the
    S3 object key.
    """
    _require_storage()
    content_type = (file.content_type or "").lower()
    if content_type not in JOURNAL_VOICE_ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported audio format for voice message.",
        )

    file.file.seek(0)
    header = file.file.read(_MAGIC_PEEK_BYTES)
    validate_audio_magic_bytes(header)

    ext = "bin"
    if "/" in content_type:
        ext = content_type.split("/")[1].replace("x-", "")
    key = f"{user_prefix}/{uuid.uuid4()}.{ext}"
    put_object(key=key, body=_read_all(file), content_type=content_type)
    return key, content_type


def generate_signed_url(storage_path: str, expires_in: int) -> tuple[str, datetime]:
    """Return a short-lived presigned GET URL (and its expiry) for an S3 key."""
    _require_storage()
    url = presigned_get_url(key=storage_path, expires_in=expires_in)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(1, int(expires_in)))
    return url, expires_at


def delete_storage_object(storage_path: str) -> bool:
    """Best-effort delete of an S3 object by key. Never raises."""
    if not storage_path or not s3_enabled():
        return False
    return delete_object(key=storage_path)
