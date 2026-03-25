import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.journal_template import JournalTemplateType


class JournalTemplateQuestion(BaseModel):
    id: int
    order: int
    text: str = Field(min_length=1, max_length=500)


class JournalTemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    template_type: JournalTemplateType
    questions: list[JournalTemplateQuestion] = Field(min_length=1)


class JournalTemplateResponse(BaseModel):
    id: uuid.UUID
    name: str
    template_type: JournalTemplateType
    is_system: bool
    owner_id: uuid.UUID | None
    questions: list[JournalTemplateQuestion]
    created_at: datetime

    model_config = {"from_attributes": True}
