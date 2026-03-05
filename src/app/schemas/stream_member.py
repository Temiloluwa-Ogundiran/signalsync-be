import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

from app.models.stream_member import MemberStatus


class StreamMemberResponse(BaseModel):
    user_id: uuid.UUID
    stream_id: uuid.UUID
    status: MemberStatus
    joined_at: datetime

    model_config = {"from_attributes": True}


# Alias used for join-request list responses — same shape, clearer intent
JoinRequestResponse = StreamMemberResponse


class ApproveRejectRequest(BaseModel):
    action: Literal["approve", "reject"]


class MemberListResponse(BaseModel):
    """Response shape for GET /streams/{id}/members.

    All callers receive: user_id, username, avatar_url.
    Stream owners additionally receive: status, joined_at.
    """

    user_id: uuid.UUID
    username: str
    avatar_url: Optional[str] = None
    # Owner-only fields — None for regular callers
    status: Optional[MemberStatus] = None
    joined_at: Optional[datetime] = None
