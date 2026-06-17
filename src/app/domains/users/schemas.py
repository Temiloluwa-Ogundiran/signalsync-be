import re
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

PASSWORD_POLICY_MESSAGE = (
    "Password must be at least 8 characters and include uppercase, "
    "lowercase, and a number."
)
PASSWORD_UPPERCASE_PATTERN = re.compile(r"[A-Z]")
PASSWORD_LOWERCASE_PATTERN = re.compile(r"[a-z]")
PASSWORD_NUMBER_PATTERN = re.compile(r"\d")


def _validate_password_strength(value: str) -> str:
    if (
        not PASSWORD_UPPERCASE_PATTERN.search(value)
        or not PASSWORD_LOWERCASE_PATTERN.search(value)
        or not PASSWORD_NUMBER_PATTERN.search(value)
    ):
        raise ValueError(PASSWORD_POLICY_MESSAGE)
    return value


# ── Response schemas ─────────────────────────────────────────────────────────

class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    is_email_verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionResponse(BaseModel):
    """A single active refresh-token session for the current user."""

    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    # True for the session making the current request (so the UI can label it
    # "This device" and avoid a self-revoke footgun).
    is_current: bool = False

    model_config = {"from_attributes": True}


class AvatarUploadResponse(BaseModel):
    avatar_url: str


class MessageResponse(BaseModel):
    message: str


# ── Request schemas ──────────────────────────────────────────────────────────

class UpdateProfileRequest(BaseModel):
    # All fields optional — a PATCH only touches what's provided. display_name
    # cannot be blanked (min_length=1); bio may be set to "" to clear it.
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    bio: Optional[str] = Field(default=None, max_length=500)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        return _validate_password_strength(value)


class ChangeEmailRequest(BaseModel):
    new_email: EmailStr
    # Re-authenticate the change with the current password — an email swap is a
    # full account-takeover vector if a session leaks.
    current_password: str = Field(min_length=1)


class DeleteAccountRequest(BaseModel):
    # Confirm with the password before a (soft) account deletion.
    current_password: str = Field(min_length=1)
