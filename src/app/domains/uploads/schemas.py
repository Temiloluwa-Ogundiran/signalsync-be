from typing import Optional

from pydantic import BaseModel


class ImageUploadResponse(BaseModel):
    url: str


class MediaUploadResponse(BaseModel):
    storage_path: str
    media_type: str
    mime_type: str
