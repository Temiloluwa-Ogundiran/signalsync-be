"""
Object storage: custom storage microservice (preferred) or Supabase Storage (legacy paths).
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings
from app.core.supabase import get_supabase
from app.domains.posts.models import PostMediaType
from app.shared.utils.storage_service_client import (
    gateway_url_for_object_key,
    is_legacy_supabase_storage_path,
    storage_service_enabled,
    upload_via_storage_service,
)

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


def upload_image(
    file: UploadFile,
    bucket: str,
    prefix: str,
) -> str:
    """
    Upload a stream avatar/banner image.

    Returns:
        A URL suitable for persisting on `Stream.avatar_url` / `banner_url`:
        public Supabase URL (legacy) or HTTPS gateway URL on the storage microservice.
    """
    content_type = file.content_type or ""
    if not any(content_type.startswith(p) for p in _ALLOWED_MIME_PREFIXES):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid file type '{content_type}'. Allowed: JPEG, PNG, WebP, GIF.",
        )

    if storage_service_enabled():
        file.file.seek(0)
        object_key = upload_via_storage_service(file)
        return gateway_url_for_object_key(object_key)

    ext = _MIME_TO_EXT.get(content_type, "jpg")
    file_path = f"{prefix}/{uuid.uuid4()}.{ext}"
    file.file.seek(0)
    file_bytes = file.file.read()

    supabase = get_supabase()
    response = supabase.storage.from_(bucket).upload(
        path=file_path,
        file=file_bytes,
        file_options={"content-type": content_type, "upsert": "false"},
    )

    if hasattr(response, "error") and response.error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload image. Please try again.",
        )

    public_url: str = supabase.storage.from_(bucket).get_public_url(file_path)
    return public_url


def upload_media(
    file: UploadFile,
    bucket: str,
    prefix: str,
) -> tuple[str, PostMediaType, str]:
    """
    Upload post or journal attachment media.

    Returns:
        (storage_path, PostMediaType, mime_type). For the microservice, `storage_path`
        is the object key; use `generate_signed_url` to build the gateway URL for clients.
    """
    content_type = file.content_type or ""
    if content_type not in _MEDIA_MIME_MAP:
        allowed = ", ".join(_MEDIA_MIME_MAP.keys())
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported media type '{content_type}'. Allowed: {allowed}.",
        )

    if storage_service_enabled():
        object_key = upload_via_storage_service(file)
        _, media_type = _MEDIA_MIME_MAP[content_type]
        return object_key, media_type, content_type

    ext, media_type = _MEDIA_MIME_MAP[content_type]
    storage_path = f"{prefix}/{uuid.uuid4()}.{ext}"

    file.file.seek(0)
    file_bytes = file.file.read()

    supabase = get_supabase()
    response = supabase.storage.from_(bucket).upload(
        path=storage_path,
        file=file_bytes,
        file_options={"content-type": content_type, "upsert": "false"},
    )

    if hasattr(response, "error") and response.error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload media. Please try again.",
        )

    return storage_path, media_type, content_type


def upload_journal_voice_note(file: UploadFile, *, user_prefix: str) -> tuple[str, str]:
    """
    Upload a journal voice attachment.

    Returns:
        (storage_path, normalized_content_type). `user_prefix` is kept for API
        compatibility; the storage microservice assigns its own object key.
    """
    _ = user_prefix
    content_type = (file.content_type or "").lower()
    if content_type not in JOURNAL_VOICE_ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported audio format for voice message.",
        )

    if storage_service_enabled():
        file.file.seek(0)
        object_key = upload_via_storage_service(file)
        return object_key, content_type

    ext = "bin"
    if "/" in content_type:
        ext = content_type.split("/")[1].replace("x-", "")

    storage_path = f"{user_prefix}/{uuid.uuid4()}.{ext}"
    file.file.seek(0)
    file_bytes = file.file.read()

    supabase = get_supabase()
    supabase.storage.from_(settings.JOURNAL_VOICE_BUCKET).upload(
        path=storage_path,
        file=file_bytes,
        file_options={"content-type": content_type, "upsert": "false"},
    )
    return storage_path, content_type


def generate_signed_url(
    bucket: str,
    storage_path: str,
    expires_in: int,
) -> tuple[str, datetime]:
    """
    Return a URL clients can use to fetch private media.

    For the storage microservice (non-legacy keys), this returns a stable HTTPS gateway URL
    that redirects to a fresh S3 presigned URL on each request.
    """
    if storage_service_enabled() and not is_legacy_supabase_storage_path(storage_path):
        url = gateway_url_for_object_key(storage_path)
        ttl = min(
            expires_in,
            max(1, int(settings.STORAGE_SERVICE_PRESIGNED_TTL_SECONDS)),
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
        return url, expires_at

    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase Storage is not configured but this media requires a legacy URL.",
        )

    supabase = get_supabase()
    result = supabase.storage.from_(bucket).create_signed_url(storage_path, expires_in)

    signed_url: Optional[str]
    if isinstance(result, dict):
        signed_url = result.get("signedURL") or result.get("signed_url")
    else:
        raw = getattr(result, "signed_url", None)
        signed_url = str(raw) if raw is not None else None

    if not signed_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate media URL. Please try again.",
        )

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return signed_url, expires_at
