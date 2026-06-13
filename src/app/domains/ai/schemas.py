import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


# ── Request models ────────────────────────────────────────────────────────────

class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = None
    context_type: str = "general"
    context_ref: Optional[str] = None
    account_id: Optional[uuid.UUID] = None


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str


# ── Response models ───────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SessionResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    account_id: Optional[uuid.UUID]
    title: Optional[str]
    context_type: str
    context_ref: Optional[str]
    is_deleted: bool
    last_message_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SessionWithMessagesResponse(SessionResponse):
    messages: List[MessageResponse] = []


class SessionListResponse(BaseModel):
    items: List[SessionResponse]
    next_cursor: Optional[str] = None


class InsightResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    account_id: Optional[uuid.UUID]
    kind: str
    payload: Dict[str, Any]
    data_version: int
    generated_at: datetime
    valid_until: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class UsageResponse(BaseModel):
    credits_used: int
    credits_limit: int
    credits_remaining: int
    period_month: datetime
    message_count: int


class SuggestedPromptsResponse(BaseModel):
    prompts: List[str]
