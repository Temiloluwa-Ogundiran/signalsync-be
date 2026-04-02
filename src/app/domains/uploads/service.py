from typing import Literal
from uuid import UUID

from fastapi import UploadFile

from app.core.config import settings
from app.domains.posts.models import PostMediaType
from app.shared.utils.storage import upload_image, upload_media


def upload_stream_image(
    file: UploadFile,
    bucket_type: Literal["avatar", "banner"],
    user_id: UUID,
) -> str:
    bucket = (
        settings.SUPABASE_STREAM_AVATARS_BUCKET
        if bucket_type == "avatar"
        else settings.SUPABASE_STREAM_BANNERS_BUCKET
    )
    return upload_image(file, bucket=bucket, prefix=str(user_id))


def upload_post_media(
    file: UploadFile,
    user_id: UUID,
) -> tuple[str, PostMediaType, str]:
    """Upload post media and return (storage_path, media_type, mime_type)."""
    return upload_media(
        file,
        bucket=settings.SUPABASE_POST_MEDIA_BUCKET,
        prefix=str(user_id),
    )
