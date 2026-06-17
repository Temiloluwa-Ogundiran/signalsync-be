"""User profile & security operations for the authenticated account.

These back the frontend Settings → Profile / Security pages. Auth flows
(register, login, password *reset* via email link) live in the auth domain;
this module covers the things a *logged-in* user changes about themselves.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    get_password_hash,
    hash_token,
    verify_password,
)
from app.domains.auth.models import TokenType
from app.domains.auth import repository as token_repo
from app.domains.auth import service as auth_service
from app.domains.uploads import service as upload_service
from app.domains.users.models import User
from app.domains.users import repository as user_repo
from app.domains.users.schemas import (
    ChangeEmailRequest,
    ChangePasswordRequest,
    DeleteAccountRequest,
    SessionResponse,
    UpdateProfileRequest,
)
from app.tasks.auth_tasks import send_verification_email_task

logger = logging.getLogger(__name__)


def update_profile(
    db: Session, *, current_user: User, payload: UpdateProfileRequest
) -> User:
    """Patch the user's editable profile fields (display name, bio)."""
    fields = payload.model_dump(exclude_unset=True)

    if "display_name" in fields:
        display_name = (fields["display_name"] or "").strip()
        if not display_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Display name cannot be empty.",
            )
        current_user.display_name = display_name

    if "bio" in fields:
        bio = fields["bio"]
        # Empty string clears the bio; otherwise trim surrounding whitespace.
        current_user.bio = bio.strip() if bio else None

    db.commit()
    db.refresh(current_user)
    return current_user


def update_avatar(db: Session, *, current_user: User, file: UploadFile) -> User:
    """Upload a new avatar image and point the user at it."""
    url = upload_service.upload_user_avatar(file, current_user.id)
    current_user.avatar_url = url
    db.commit()
    db.refresh(current_user)
    return current_user


def change_password(
    db: Session, *, current_user: User, payload: ChangePasswordRequest
) -> None:
    """Verify the current password, then set a new one and end other sessions."""
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    if verify_password(payload.new_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current one.",
        )

    current_user.hashed_password = get_password_hash(payload.new_password)
    # Force re-login everywhere — a password change should drop stale sessions.
    token_repo.revoke_all_by_user_and_type(
        db, user_id=current_user.id, token_type=TokenType.REFRESH
    )
    db.commit()


def change_email(
    db: Session, *, current_user: User, payload: ChangeEmailRequest
) -> None:
    """Re-authenticate, swap the email, and require re-verification of the new one."""
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    new_email = payload.new_email.lower()
    if new_email == current_user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That is already your email address.",
        )

    existing = user_repo.get_by_email(db, new_email)
    if existing and existing.id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    current_user.email = new_email
    current_user.is_email_verified = False

    # Issue a fresh verification token for the new address.
    raw_token = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(
        hours=settings.EMAIL_VERIFY_EXPIRY_HOURS
    )
    token_repo.create(
        db,
        user_id=current_user.id,
        hashed_token=hash_token(raw_token),
        token_type=TokenType.VERIFY_EMAIL,
        expires_at=expires_at,
    )
    # Once is_email_verified flips false, get_current_user rejects the user, so
    # existing refresh tokens are dead weight — revoke them to be explicit.
    token_repo.revoke_all_by_user_and_type(
        db, user_id=current_user.id, token_type=TokenType.REFRESH
    )
    db.commit()

    try:
        send_verification_email_task.delay(new_email, raw_token)
    except Exception:
        logger.exception(
            "Failed to enqueue verification email for user %s after email change",
            current_user.id,
        )


def list_sessions(
    db: Session, *, current_user: User, current_refresh_token: str | None
) -> list[SessionResponse]:
    """Active refresh-token sessions, flagging the one for the current request."""
    current_hash = hash_token(current_refresh_token) if current_refresh_token else None
    tokens = token_repo.list_active_by_user_and_type(
        db, user_id=current_user.id, token_type=TokenType.REFRESH
    )
    return [
        SessionResponse(
            id=t.id,
            created_at=t.created_at,
            expires_at=t.expires_at,
            is_current=current_hash is not None and t.token == current_hash,
        )
        for t in tokens
    ]


def revoke_session(db: Session, *, current_user: User, session_id: UUID) -> None:
    """Revoke a single refresh-token session owned by the user."""
    token = token_repo.get_active_by_id_for_user(
        db,
        token_id=session_id,
        user_id=current_user.id,
        token_type=TokenType.REFRESH,
    )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found.",
        )
    token_repo.revoke(db, token)
    db.commit()


def delete_account(
    db: Session,
    *,
    current_user: User,
    payload: DeleteAccountRequest,
    response: Response,
) -> None:
    """Re-authenticate, soft-delete the account, and revoke every session."""
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    current_user.is_deleted = True
    current_user.deleted_at = datetime.now(timezone.utc)
    token_repo.revoke_all_by_user_and_type(
        db, user_id=current_user.id, token_type=TokenType.REFRESH
    )
    db.commit()
    # Clear the refresh cookie so the browser drops the now-dead session.
    auth_service.clear_refresh_cookie(response)
