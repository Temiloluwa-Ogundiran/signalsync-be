"""
Supabase Storage utilities.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, UploadFile, status

from app.core.supabase import get_supabase
from app.models.post import PostMediaType

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


def upload_image(
    file: UploadFile,
    bucket: str,
    prefix: str,
) -> str:
    """
    Upload an image file to a Supabase Storage bucket.

    Args:
        file:   The uploaded file from the request.
        bucket: The Supabase Storage bucket name.
        prefix: Path prefix inside the bucket (e.g. the owner's user_id string).

    Returns:
        The public URL of the uploaded file.

    Raises:
        HTTPException 422 if the file type is not an allowed image type.
        HTTPException 500 if the Supabase upload fails.
    """
    content_type = file.content_type or ""
    if not any(content_type.startswith(p) for p in _ALLOWED_MIME_PREFIXES):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid file type '{content_type}'. Allowed: JPEG, PNG, WebP, GIF.",
        )

    ext = _MIME_TO_EXT.get(content_type, "jpg")
    file_path = f"{prefix}/{uuid.uuid4()}.{ext}"

    file_bytes = file.file.read()

    supabase = get_supabase()
    response = supabase.storage.from_(bucket).upload(
        path=file_path,
        file=file_bytes,
        file_options={"content-type": content_type, "upsert": "false"},
    )

    # supabase-py raises on error, but guard anyway
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
    Upload a post media file (image, video, or document) to a private Supabase bucket.

    Returns:
        A tuple of (storage_path, PostMediaType, mime_type).
        `storage_path` is the path *inside* the bucket — persist this in the DB
        and use generate_signed_url() to serve it to clients.

    Raises:
        HTTPException 422 if the file type is not allowed.
        HTTPException 500 if the Supabase upload fails.
    """
    content_type = file.content_type or ""
    if content_type not in _MEDIA_MIME_MAP:
        allowed = ", ".join(_MEDIA_MIME_MAP.keys())
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported media type '{content_type}'. Allowed: {allowed}.",
        )

    ext, media_type = _MEDIA_MIME_MAP[content_type]
    storage_path = f"{prefix}/{uuid.uuid4()}.{ext}"

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


def generate_signed_url(
    bucket: str,
    storage_path: str,
    expires_in: int,
) -> tuple[str, datetime]:
    """
    Generate a short-lived signed URL for a file in a private Supabase bucket.

    Args:
        bucket:       The Supabase Storage bucket name.
        storage_path: The path inside the bucket (as returned by upload_media).
        expires_in:   Lifetime of the URL in seconds.

    Returns:
        A tuple of (signed_url, expires_at) where expires_at is timezone-aware UTC.

    Raises:
        HTTPException 500 if Supabase fails to generate the URL.
    """
    supabase = get_supabase()
    result = supabase.storage.from_(bucket).create_signed_url(storage_path, expires_in)

    # supabase-py v2 returns an object; guard for both dict and object shapes.
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
