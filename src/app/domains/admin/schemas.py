import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.users.models import PlatformRole


class ProductEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: uuid.UUID
    event_name: str = Field(min_length=1, max_length=48)
    path: str = Field(min_length=1, max_length=255)
    referrer_host: Optional[str] = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime

    @field_validator("event_name")
    @classmethod
    def validate_event_name(cls, value: str) -> str:
        allowed = {
            "page_view", "session_engaged", "login_started", "login_succeeded",
            "registration_started", "registration_completed", "onboarding_completed",
            "account_connected", "journal_synced", "copy_route_activated",
        }
        if value not in allowed:
            raise ValueError("Unsupported product event.")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 10:
            raise ValueError("Event metadata has too many fields.")
        allowed = {"device", "source", "campaign", "result", "feature"}
        if any(key not in allowed for key in value):
            raise ValueError("Event metadata contains unsupported fields.")
        return value

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Event timestamp must include a timezone.")
        return value


class ProductEventResponse(BaseModel):
    accepted: bool = True


class AdminUserSummary(BaseModel):
    id: uuid.UUID
    email: str
    display_name: Optional[str]
    platform_role: PlatformRole
    is_email_verified: bool
    is_suspended: bool
    onboarding_completed: bool
    trading_account_count: int
    copy_account_count: int
    last_active_at: Optional[datetime]
    created_at: datetime


class AdminUserPage(BaseModel):
    items: list[AdminUserSummary]
    next_cursor: Optional[str] = None


class AdminTradingAccount(BaseModel):
    id: uuid.UUID
    display_name: Optional[str]
    broker_name: str
    broker_server: str
    broker_login: str
    status: str
    connection_state: str
    last_synced_at: Optional[datetime]


class AdminCopyAccount(BaseModel):
    id: uuid.UUID
    display_name: str
    broker_server: str
    broker_login: str
    state: str
    is_paused: bool
    last_health_at: Optional[datetime]


class AdminUserDetail(AdminUserSummary):
    auth_provider: str
    suspended_at: Optional[datetime]
    suspension_reason: Optional[str]
    active_sessions: int
    trading_accounts: list[AdminTradingAccount]
    copy_accounts: list[AdminCopyAccount]


class SuspendUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=500)


class UpdateUserRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: PlatformRole
    reason: str = Field(min_length=3, max_length=500)


class AdminMutationResponse(BaseModel):
    message: str


class AdminOverviewResponse(BaseModel):
    totals: dict[str, int]
    signups: list[dict[str, Any]]
    funnel: list[dict[str, Any]]
    product: dict[str, Any]


class AdminSystemResponse(BaseModel):
    status: str
    components: list[dict[str, Any]]
    copy_latency: dict[str, Any]
    grafana_url: Optional[str]


class AdminAuditItem(BaseModel):
    id: uuid.UUID
    actor_email: str
    target_email: Optional[str]
    action: str
    reason: Optional[str]
    details: dict[str, Any]
    created_at: datetime


class AdminAuditPage(BaseModel):
    items: list[AdminAuditItem]
    next_cursor: Optional[str] = None
