import re

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.domains.users.schemas import UserResponse

PASSWORD_POLICY_MESSAGE = (
    "Password must be at least 8 characters and include uppercase, "
    "lowercase, and a number."
)
PASSWORD_UPPERCASE_PATTERN = re.compile(r"[A-Z]")
PASSWORD_LOWERCASE_PATTERN = re.compile(r"[a-z]")
PASSWORD_NUMBER_PATTERN = re.compile(r"\d")


# ── Request schemas ──────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=50)
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        if (
            not PASSWORD_UPPERCASE_PATTERN.search(value)
            or not PASSWORD_LOWERCASE_PATTERN.search(value)
            or not PASSWORD_NUMBER_PATTERN.search(value)
        ):
            raise ValueError(PASSWORD_POLICY_MESSAGE)
        return value


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        if (
            not PASSWORD_UPPERCASE_PATTERN.search(value)
            or not PASSWORD_LOWERCASE_PATTERN.search(value)
            or not PASSWORD_NUMBER_PATTERN.search(value)
        ):
            raise ValueError(PASSWORD_POLICY_MESSAGE)
        return value


# ── Response schemas ─────────────────────────────────────────────────────────

class RegisterResponse(BaseModel):
    message: str
    user: UserResponse


class VerifyEmailResponse(BaseModel):
    message: str
    # On a fresh verification we also log the user in (issue tokens + set the
    # refresh cookie) so the frontend doesn't have to ask them to sign in again.
    # These are None when the email was already verified (idempotent re-hit).
    access_token: str | None = None
    token_type: str = "bearer"
    access_token_expiry_minutes: int | None = None
    # Raw refresh token, returned only on fresh verification so the frontend can
    # seed a long-lived session from the email link (see service.verify_email).
    refresh_token: str | None = None
    user: UserResponse | None = None


class ResendVerificationResponse(BaseModel):
    message: str


class ForgotPasswordResponse(BaseModel):
    message: str


class ResetPasswordResponse(BaseModel):
    message: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    access_token_expiry_minutes: int
    user: UserResponse


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    access_token_expiry_minutes: int
