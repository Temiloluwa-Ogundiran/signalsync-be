import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.core.config import settings
from app.core.security import (
    create_access_token,
    get_password_hash,
    hash_token,
    verify_password,
)
from app.domains.auth.models import Token, TokenType
from app.domains.auth import repository as token_repo
from app.domains.users import repository as user_repo
from app.domains.users.schemas import UserResponse
from app.domains.auth.schemas import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginResponse,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    ResendVerificationResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    VerifyEmailResponse,
)
from app.domains.streams.models import StreamPrivacy
from app.domains.streams import repository as stream_repo
from app.tasks.auth_tasks import send_verification_email_task, send_password_reset_email_task

# Module-level dummy hash — ensures bcrypt always runs on login even for unknown
# emails, defeating timing-based email enumeration (P1-8).
_DUMMY_HASH: str = get_password_hash(uuid.uuid4().hex)


def register(db: Session, payload: RegisterRequest) -> RegisterResponse:
    # ── uniqueness checks ────────────────────────────────────────────────────
    if user_repo.get_by_email(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )
    if user_repo.get_by_username(db, payload.username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This username is already taken.",
        )

    # ── create user ──────────────────────────────────────────────────────────
    user = user_repo.create(
        db,
        username=payload.username,
        email=payload.email,
        hashed_password=get_password_hash(payload.password),
        display_name=payload.display_name,
    )

    # ── create default stream ────────────────────────────────────────────────
    stream_repo.create(
        db,
        owner_id=user.id,
        name=f"{user.display_name}'s Stream",
        description=None,
        privacy=StreamPrivacy.public,
        forum_enabled=True,
        tags=None,
        price=None,
        avatar_url=None,
        banner_url=None,
        require_join_approval=False,
        is_default=True,
    )

    # ── issue verification token ─────────────────────────────────────────────
    raw_token = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(
        hours=settings.EMAIL_VERIFY_EXPIRY_HOURS
    )
    token_repo.create(
        db,
        user_id=user.id,
        hashed_token=hash_token(raw_token),
        token_type=TokenType.VERIFY_EMAIL,
        expires_at=expires_at,
    )

    db.commit()
    db.refresh(user)

    # ── enqueue verification email (non-fatal — user can use resend flow) ─────
    try:
        send_verification_email_task.delay(user.email, raw_token)
    except Exception:
        logger.exception(
            "Failed to enqueue verification email for user %s — user must use resend flow", user.id
        )

    return RegisterResponse(
        message="Account created. Please check your email to verify your address.",
        user=UserResponse.model_validate(user),
    )


def _issue_session(db: Session, user, response: Response) -> tuple[str, int, str]:
    """Issue an access token + rotating refresh cookie for `user`.

    Shared by login and email-verification auto-login so both establish a
    session identically. Returns
    (access_token, access_token_expiry_minutes, raw_refresh_token).
    """
    access_token = create_access_token(str(user.id))

    raw_refresh = str(uuid.uuid4())
    refresh_expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    token_repo.create(
        db,
        user_id=user.id,
        hashed_token=hash_token(raw_refresh),
        token_type=TokenType.REFRESH,
        expires_at=refresh_expires_at,
    )
    db.commit()

    response.set_cookie(
        key="refresh_token",
        value=raw_refresh,
        httponly=True,
        secure=settings.IS_PRODUCTION,
        samesite="lax",
        max_age=60 * 60 * 24 * settings.REFRESH_TOKEN_EXPIRE_DAYS,
        path="/",
    )

    return access_token, settings.ACCESS_TOKEN_EXPIRE_MINUTES, raw_refresh


def login(db: Session, email: str, password: str, response: Response) -> LoginResponse:
    # ── credential checks ────────────────────────────────────────────
    # Always call verify_password — even for unknown emails — so response time
    # is identical regardless of whether the email is registered (P1-8).
    user = user_repo.get_by_email(db, email)
    password_ok = verify_password(password, user.hashed_password if user else _DUMMY_HASH)
    if not user or not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

    if not user.is_email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before logging in.",
        )

    # ── issue tokens + refresh cookie ────────────────────────────────────────
    access_token, expiry_minutes, _raw_refresh = _issue_session(db, user, response)

    return LoginResponse(
        access_token=access_token,
        access_token_expiry_minutes=expiry_minutes,
        user=UserResponse.model_validate(user),
    )


def verify_email(db: Session, raw_token: str, response: Response) -> VerifyEmailResponse:
    """Verify the email token and mark the user as verified."""
    hashed = hash_token(raw_token)

    # 1. Lookup the token regardless of status to support idempotency/double-calls
    stmt = select(Token).where(
        Token.token == hashed,
        Token.type == TokenType.VERIFY_EMAIL,
    )
    token = db.execute(stmt).scalar_one_or_none()

    if not token:
        # Log enough to disambiguate the failure in prod without leaking the raw
        # token. A "not found" here means the hash isn't in the DB at all —
        # usually a stale link whose token was already rotated by a resend, or a
        # link from a different environment/database.
        logger.warning(
            "verify_email: token hash not found (prefix=%s)", hashed[:12]
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token.",
        )

    user = user_repo.get_by_id(db, token.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    # If the user is already verified, return success.
    # This prevents StrictMode double-renders or client double-submissions from showing error states.
    if user.is_email_verified:
        return VerifyEmailResponse(message="Email is already verified.")

    # 2. Check if the token has expired or is revoked for an unverified user
    if token.is_revoked or token.expires_at <= datetime.now(timezone.utc):
        logger.warning(
            "verify_email: token rejected for user %s (revoked=%s, expires_at=%s, now=%s)",
            token.user_id,
            token.is_revoked,
            token.expires_at,
            datetime.now(timezone.utc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token.",
        )

    user.is_email_verified = True
    token_repo.revoke(db, token)
    db.commit()

    # Auto-login: issue a session so the user lands signed in straight from the
    # email link (no second manual login). _issue_session commits internally.
    # We also return the raw refresh token here (in addition to the httponly
    # cookie) so the frontend's NextAuth session can refresh long-term — the
    # browser JS can't read the cookie, and login from an email link is a
    # one-time, user-initiated action.
    access_token, expiry_minutes, raw_refresh = _issue_session(db, user, response)

    return VerifyEmailResponse(
        message="Email verified successfully.",
        access_token=access_token,
        access_token_expiry_minutes=expiry_minutes,
        refresh_token=raw_refresh,
        user=UserResponse.model_validate(user),
    )


def resend_verification(
    db: Session, payload: ResendVerificationRequest
) -> ResendVerificationResponse:
    user = user_repo.get_by_email(db, payload.email)

    # Always return a generic 200 to avoid leaking which emails are registered.
    # Only actually send if the user exists and is unverified.
    if not user or user.is_email_verified:
        return ResendVerificationResponse(
            message="If that email is registered and unverified, a new link has been sent."
        )

    # Revoke any existing VERIFY_EMAIL tokens for this user, then create a new one.
    stmt = select(Token).where(
        Token.user_id == user.id,
        Token.type == TokenType.VERIFY_EMAIL,
        Token.is_revoked.is_(False),
    )
    for t in db.execute(stmt).scalars():
        token_repo.revoke(db, t)

    raw_token = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(
        hours=settings.EMAIL_VERIFY_EXPIRY_HOURS
    )
    token_repo.create(
        db,
        user_id=user.id,
        hashed_token=hash_token(raw_token),
        token_type=TokenType.VERIFY_EMAIL,
        expires_at=expires_at,
    )
    db.commit()

    try:
        send_verification_email_task.delay(user.email, raw_token)
    except Exception:
        logger.exception(
            "Failed to enqueue resend verification email for user %s", user.id
        )

    return ResendVerificationResponse(
        message="If that email is registered and unverified, a new link has been sent."
    )


def refresh_access_token(
    db: Session, raw_refresh: str | None, response: Response
) -> RefreshResponse:
    """
    Validate the refresh token cookie, rotate the refresh token, and
    return a new access token.
    """
    if not raw_refresh:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token missing.",
        )

    hashed = hash_token(raw_refresh)
    token_record = token_repo.get_active(
        db,
        hashed_token=hashed,
        token_type=TokenType.REFRESH,
    )
    if not token_record:
        grace_record = token_repo.get_recently_revoked(
            db, hashed_token=hashed, token_type=TokenType.REFRESH
        )
        if grace_record is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token.",
            )
        # Concurrent-rotation race: token was just rotated by a parallel request.
        # Issue a fresh ACCESS token only — no new refresh token, no new cookie.
        user = user_repo.get_by_id(db, grace_record.user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found.",
            )
        return RefreshResponse(
            access_token=create_access_token(str(user.id)),
            access_token_expiry_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        )

    user = user_repo.get_by_id(db, token_record.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found.",
        )

    # ── rotate: revoke old, issue new ────────────────────────────────────────
    token_repo.revoke(db, token_record)

    new_raw_refresh = str(uuid.uuid4())
    refresh_expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    token_repo.create(
        db,
        user_id=user.id,
        hashed_token=hash_token(new_raw_refresh),
        token_type=TokenType.REFRESH,
        expires_at=refresh_expires_at,
    )

    new_access_token = create_access_token(str(user.id))
    db.commit()

    response.set_cookie(
        key="refresh_token",
        value=new_raw_refresh,
        httponly=True,
        secure=settings.IS_PRODUCTION,
        samesite="lax",
        max_age=60 * 60 * 24 * settings.REFRESH_TOKEN_EXPIRE_DAYS,
        path="/",
    )

    return RefreshResponse(
        access_token=new_access_token,
        access_token_expiry_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
    )


def logout(db: Session, raw_refresh: str | None, response: Response) -> dict:
    """
    Revoke the refresh token (if present) and clear the cookie.
    Always returns 200 to avoid leaking token validity.
    """
    if raw_refresh:
        hashed = hash_token(raw_refresh)
        token_record = token_repo.get_active(
            db,
            hashed_token=hashed,
            token_type=TokenType.REFRESH,
        )
        if token_record:
            token_repo.revoke(db, token_record)
            db.commit()

    response.delete_cookie(
        key="refresh_token",
        httponly=True,
        secure=settings.IS_PRODUCTION,
        samesite="lax",
        path="/",
    )

    return {"message": "Logged out successfully."}


def forgot_password(db: Session, payload: ForgotPasswordRequest) -> ForgotPasswordResponse:
    """Request a password reset. Always 200 — never leaks whether the email exists."""
    user = user_repo.get_by_email(db, payload.email)
    if user and user.is_email_verified and not user.is_deleted:
        # Revoke any existing reset tokens
        token_repo.revoke_all_by_user_and_type(
            db, user_id=user.id, token_type=TokenType.RESET_PASSWORD
        )
        raw_token = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=settings.PASSWORD_RESET_EXPIRY_MINUTES
        )
        token_repo.create(
            db,
            user_id=user.id,
            hashed_token=hash_token(raw_token),
            token_type=TokenType.RESET_PASSWORD,
            expires_at=expires_at,
        )
        db.commit()
        try:
            send_password_reset_email_task.delay(user.email, raw_token)
        except Exception:
            logger.exception("Failed to enqueue password reset email for user %s", user.id)
    return ForgotPasswordResponse(
        message="If that email is registered, a password reset link has been sent."
    )


def reset_password(db: Session, payload: ResetPasswordRequest) -> ResetPasswordResponse:
    """Consume the reset token and set a new password."""
    hashed = hash_token(payload.token)
    token_record = token_repo.get_active(
        db, hashed_token=hashed, token_type=TokenType.RESET_PASSWORD
    )
    if not token_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )
    user = user_repo.get_by_id(db, token_record.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User not found.")

    user.hashed_password = get_password_hash(payload.new_password)
    token_repo.revoke(db, token_record)
    # Revoke all refresh tokens — force re-login everywhere
    token_repo.revoke_all_by_user_and_type(
        db, user_id=user.id, token_type=TokenType.REFRESH
    )
    db.commit()
    return ResetPasswordResponse(message="Password reset successfully. Please log in.")
