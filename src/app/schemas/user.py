import uuid
from datetime import datetime

from pydantic import BaseModel


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    is_email_verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}
