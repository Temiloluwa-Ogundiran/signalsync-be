import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel

from app.models.stream import StreamPrivacy


class StreamResponse(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    description: Optional[str]
    privacy: StreamPrivacy
    forum_enabled: bool
    tags: Optional[list[str]]
    price: Optional[Decimal]
    avatar_url: Optional[str]
    banner_url: Optional[str]
    require_join_approval: bool
    created_at: datetime

    model_config = {"from_attributes": True}
