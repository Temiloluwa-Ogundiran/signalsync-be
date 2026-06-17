import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.domains.streams.models import MemberStatus, StreamPrivacy


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
    is_default: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class StreamDiscoverResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    privacy: StreamPrivacy
    tags: Optional[list[str]]
    avatar_url: Optional[str]
    banner_url: Optional[str]
    owner_display_name: Optional[str]
    follower_count: int
    is_following: bool
    membership_status: Optional[MemberStatus] = None
    created_at: datetime


class StreamDetailResponse(BaseModel):
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
    is_default: bool
    created_at: datetime
    owner_display_name: Optional[str]
    follower_count: int
    is_following: bool

    model_config = {"from_attributes": True}


class StreamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    privacy: StreamPrivacy = StreamPrivacy.public
    description: Optional[str] = None
    forum_enabled: bool = True
    tags: Optional[list[str]] = None
    price: Optional[Decimal] = None
    require_join_approval: bool = False
    avatar_url: Optional[str] = None
    banner_url: Optional[str] = None


class ForumToggleRequest(BaseModel):
    forum_enabled: bool


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

    All callers receive: user_id, display_name, avatar_url.
    Stream owners additionally receive: status, joined_at.
    """

    user_id: uuid.UUID
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    # Owner-only fields — None for regular callers
    status: Optional[MemberStatus] = None
    joined_at: Optional[datetime] = None


class PaginatedMemberListResponse(BaseModel):
    items: list[MemberListResponse]
    next_cursor: Optional[uuid.UUID] = None


class PaginatedJoinRequestResponse(BaseModel):
    items: list[JoinRequestResponse]
    next_cursor: Optional[uuid.UUID] = None
