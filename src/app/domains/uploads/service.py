from uuid import UUID

from fastapi import UploadFile

from app.core.config import settings
from app.shared.utils.storage import upload_image


def upload_user_avatar(file: UploadFile, user_id: UUID) -> str:
    """Upload a user's profile avatar and return its public URL."""
    return upload_image(file, prefix=f"{settings.S3_PREFIX_USER_AVATARS}/{user_id}")
