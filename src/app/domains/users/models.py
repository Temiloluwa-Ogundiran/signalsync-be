import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy import false as sa_false
from sqlalchemy import true as sa_true
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, as_pg_enum


class AuthProvider(str, PyEnum):
    """How the account authenticates / was created.

    EMAIL  — standard email + password sign-up.
    GOOGLE — created (or linked) via Google sign-in. The email is owned by the
             provider; such users may have no usable password until they set one.
    """

    EMAIL = "email"
    GOOGLE = "google"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    # How the account signs in. Email-managed accounts can change their email and
    # password here; Google accounts manage their email at the provider.
    auth_provider: Mapped[AuthProvider] = mapped_column(
        as_pg_enum(AuthProvider, name="authproviderenum"),
        default=AuthProvider.EMAIL,
        server_default=AuthProvider.EMAIL.value,
        nullable=False,
    )
    # Whether `hashed_password` is a real, user-chosen password. False for a
    # Google sign-up (we store a random unusable hash) until they set one. Drives
    # the "Change password" vs "Set password" choice on the Security page.
    has_usable_password: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=sa_true(),
        nullable=False,
    )

    display_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # ── Display preferences (Global settings) ────────────────────────────────
    # Preferred IANA timezone for rendering timestamps in the UI. Null means
    # "no preference" — the frontend falls back to the account/browser timezone.
    # (Currency is NOT a user preference: amounts are shown in each broker
    # account's own currency, which lives on TradingAccount.)
    display_timezone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    # Onboarding — short post-signup questionnaire (data collection). Flag gates
    # the one-time flow; the three answers are free-form short codes.
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa_false(), nullable=False
    )
    onboarding_completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    trading_experience: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Comma-joined list of goal codes (multi-select), e.g. "journal,analyze".
    primary_goal: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    referral_source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Soft delete
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_active_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    tokens: Mapped[List["Token"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
    )

    trading_accounts: Mapped[List["TradingAccount"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
    )

    journal_messages: Mapped[List["JournalMessage"]] = relationship(  # noqa: F821
        back_populates="author",
    )

    journal_templates: Mapped[List["JournalTemplate"]] = relationship(  # noqa: F821
        back_populates="owner",
        cascade="all, delete-orphan",
    )
