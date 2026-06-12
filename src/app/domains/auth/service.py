import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

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
    LoginResponse,
    RefreshResponse,
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    ResendVerificationResponse,
    VerifyEmailResponse,
)
from app.domains.streams.models import StreamPrivacy
from app.domains.streams import repository as stream_repo
from app.shared.utils.email import send_verification_email


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

    # ── send email (stubbed) ─────────────────────────────────────────────────
    send_verification_email(user.email, raw_token)

    return RegisterResponse(
        message="Account created. Please check your email to verify your address.",
        user=UserResponse.model_validate(user),
    )


def login(db: Session, email: str, password: str, response: Response) -> LoginResponse:
    # ── credential checks ────────────────────────────────────────────
    user = user_repo.get_by_email(db, email)
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

    if not user.is_email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email before logging in.",
        )

    # ── issue tokens ─────────────────────────────────────────────────────────
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

    # ── set refresh token as httponly cookie ─────────────────────────────────
    response.set_cookie(
        key="refresh_token",
        value=raw_refresh,
        httponly=True,
        secure=settings.IS_PRODUCTION,
        samesite="lax",
        max_age=60 * 60 * 24 * settings.REFRESH_TOKEN_EXPIRE_DAYS,
        path="/",
    )

    return LoginResponse(
        access_token=access_token,
        access_token_expiry_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        user=UserResponse.model_validate(user),
    )


def verify_email(db: Session, raw_token: str) -> VerifyEmailResponse:
    """Verify the email token and mark the user as verified."""
    hashed = hash_token(raw_token)

    # 1. Lookup the token regardless of status to support idempotency/double-calls
    stmt = select(Token).where(
        Token.token == hashed,
        Token.type == TokenType.VERIFY_EMAIL,
    )
    token = db.execute(stmt).scalar_one_or_none()

    if not token:
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token.",
        )

    user.is_email_verified = True
    token_repo.revoke(db, token)
    db.commit()

    return VerifyEmailResponse(message="Email verified successfully.")


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

    send_verification_email(user.email, raw_token)

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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
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
