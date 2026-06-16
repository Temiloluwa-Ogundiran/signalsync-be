import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.domains.posts.models import PostMediaType, PostType


# ---------------------------------------------------------------------------
# Nested / embedded schemas
# ---------------------------------------------------------------------------


class PostTradeData(BaseModel):
    """Structured trade payload for signal-type posts. Rejects unknown fields."""

    model_config = {"extra": "forbid"}

    symbol: str = Field(min_length=1, max_length=20)
    direction: Literal["buy", "sell"]
    entry: Decimal = Field(gt=0)
    stop_loss: Optional[Decimal] = Field(default=None, gt=0)
    take_profit: Optional[Decimal] = Field(default=None, gt=0)
    risk_percent: Optional[Decimal] = Field(default=None, gt=0, le=100)
    timeframe: Optional[str] = Field(default=None, max_length=10)
    notes: Optional[str] = Field(default=None, max_length=2000)


class PostMediaResponse(BaseModel):
    id: uuid.UUID
    url: str  # signed URL — valid until url_expires_at
    url_expires_at: datetime
    media_type: PostMediaType
    mime_type: str
    original_filename: Optional[str] = None

    model_config = {"from_attributes": True}


class AuthorInfo(BaseModel):
    id: uuid.UUID
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class PostCreate(BaseModel):
    """
    Used by the service layer after the route has parsed Form fields.
    Either content or a media file must be provided — that check happens in
    the service (the media file cannot be validated here because it comes
    from a separate `File(...)` parameter in the route).
    """

    type: PostType
    content: Optional[str] = None
    trade_data: Optional[PostTradeData] = None
    # For replies only — set by the service, not directly from the client
    parent_post_id: Optional[uuid.UUID] = None

    @classmethod
    def from_form(
        cls,
        type: PostType,
        content: Optional[str],
        trade_data_raw: Optional[str],
    ) -> "PostCreate":
        """
        Parse a PostCreate from multipart Form fields.
        `trade_data_raw` is the raw JSON string from the form.
        """
        parsed_trade_data: Optional[PostTradeData] = None
        if trade_data_raw is not None:
            try:
                raw_dict = json.loads(trade_data_raw)
                if not isinstance(raw_dict, dict):
                    raise ValueError("trade_data must be a JSON object")
                parsed_trade_data = PostTradeData.model_validate(raw_dict)
            except (ValueError, TypeError) as exc:
                from fastapi import HTTPException, status

                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"'trade_data' is invalid: {exc}",
                ) from exc

        return cls(type=type, content=content, trade_data=parsed_trade_data)


class PostUpdate(BaseModel):
    content: Optional[str] = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "PostUpdate":
        if self.content is None:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="At least one field must be provided for an update.",
            )
        return self


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class PostResponse(BaseModel):
    id: uuid.UUID
    stream_id: uuid.UUID
    author_id: uuid.UUID
    type: PostType
    content: Optional[str] = None
    trade_data: Optional[dict] = None
    parent_post_id: Optional[uuid.UUID] = None
    is_deleted: bool
    # True when this is a reply and its parent post has been soft-deleted.
    # Clients should render a "(Parent deleted)" placeholder in that case.
    is_parent_deleted: bool = False
    created_at: datetime
    updated_at: datetime

    author: AuthorInfo
    media: Optional[PostMediaResponse] = None
    reply_count: int = 0
    upvote_count: int = 0
    has_upvoted: bool = False

    model_config = {"from_attributes": True}


class PostListResponse(BaseModel):
    items: list[PostResponse]
    next_cursor: Optional[uuid.UUID] = None
