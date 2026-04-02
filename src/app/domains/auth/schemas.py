from pydantic import BaseModel, EmailStr, Field

from app.domains.users.schemas import UserResponse


# ── Request schemas ──────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=50)
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8)


class ResendVerificationRequest(BaseModel):
    email: EmailStr


# ── Response schemas ─────────────────────────────────────────────────────────

class RegisterResponse(BaseModel):
    message: str
    user: UserResponse


class VerifyEmailResponse(BaseModel):
    message: str


class ResendVerificationResponse(BaseModel):
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
