import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    display_name: Optional[str] = None
    is_email_verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}
