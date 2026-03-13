from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.upload import ImageUploadResponse, MediaUploadResponse
from app.services import upload_service

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post(
    "/image",
    response_model=ImageUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a stream image",
    description=(
        "Upload an avatar or banner image. Returns the public URL to pass to "
        "POST /streams. Send as multipart/form-data with `file` (image) and "
        "`bucket_type` ('avatar' or 'banner')."
    ),
)
def upload_image_endpoint(
    file: UploadFile = File(..., description="Image file (JPEG, PNG, WebP, or GIF)"),
    bucket_type: Literal["avatar", "banner"] = Form(..., description="'avatar' or 'banner'"),
    current_user: User = Depends(get_current_user),
) -> ImageUploadResponse:
    url = upload_service.upload_stream_image(
        file, bucket_type=bucket_type, user_id=current_user.id
    )
    return ImageUploadResponse(url=url)


@router.post(
    "/media",
    response_model=MediaUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload post media",
    description=(
        "Upload a media file (image, video, or PDF) for use in a post. "
        "Returns storage_path + metadata to pass to POST /streams/{id}/posts. "
        "Send as multipart/form-data with `file`."
    ),
)
def upload_media_endpoint(
    file: UploadFile = File(..., description="Media file"),
    current_user: User = Depends(get_current_user),
) -> MediaUploadResponse:
    storage_path, media_type, mime_type = upload_service.upload_post_media(
        file, user_id=current_user.id
    )
    return MediaUploadResponse(
        storage_path=storage_path,
        media_type=media_type.value,
        mime_type=mime_type,
    )
