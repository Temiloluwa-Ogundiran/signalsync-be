import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.journal_message import JournalMessageType


class JournalMessageCreateRequest(BaseModel):
    message_type: JournalMessageType = JournalMessageType.text
    content: Optional[str] = Field(default=None, max_length=5000)


class JournalMessageUpdateRequest(BaseModel):
    content: Optional[str] = Field(default=None, max_length=5000)


class JournalAttachmentResponse(BaseModel):
    id: uuid.UUID
    storage_path: str
    media_type: str
    mime_type: str
    original_filename: Optional[str]
    caption: Optional[str]
    signed_url: str
    signed_url_expires_at: datetime

    model_config = {"from_attributes": True}


class JournalMessageResponse(BaseModel):
    id: uuid.UUID
    daily_journal_id: Optional[uuid.UUID]
    trade_journal_id: Optional[uuid.UUID]
    author_id: Optional[uuid.UUID]
    message_type: JournalMessageType
    content: Optional[str]
    tags: list[str]
    system_data: Optional[dict]
    audio_storage_path: Optional[str]
    audio_duration_seconds: Optional[int]
    audio_url: Optional[str]
    audio_url_expires_at: Optional[datetime]
    attachments: list[JournalAttachmentResponse]
    is_edited: bool
    edited_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}
