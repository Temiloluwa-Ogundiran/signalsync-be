"""
Supabase Storage utilities.
"""

import uuid
from typing import Optional

from fastapi import HTTPException, UploadFile, status

from app.core.supabase import get_supabase

_ALLOWED_MIME_PREFIXES = ("image/jpeg", "image/png", "image/webp", "image/gif")

_MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
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
