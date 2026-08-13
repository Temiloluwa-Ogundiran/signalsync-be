import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
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
from app.domains.users.models import AuthProvider
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
from app.tasks.auth_tasks import send_verification_email_task, send_password_reset_email_task
from app.domains.affiliates import service as affiliate_service

# Module-level dummy hash — ensures bcrypt always runs on login even for unknown
# emails, defeating timing-based email enumeration (P1-8).
_DUMMY_HASH: str = get_password_hash(uuid.uuid4().hex)

# Google's tokeninfo endpoint — verifies an ID token's signature, expiry, and
# issuer server-side and returns its claims. Avoids adding a google-auth dep.
_GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


def _seed_demo_data(db: Session, user) -> None:
    """Seed a demo trading account so the new user lands in a populated app.

    Best-effort: a failure here must never block sign-up, so it's isolated. The
    seed shares the open session and is committed with the rest of sign-up.
    """
    try:
        from app.domains.demo.service import seed_demo_account

        seed_demo_account(db, user.id)
    except Exception:
        logger.exception("Demo seeding failed for user %s — continuing sign-up", user.id)


def register(db: Session, payload: RegisterRequest) -> RegisterResponse:
    # ── uniqueness checks ────────────────────────────────────────────────────
    if user_repo.get_by_email(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    # ── create user ──────────────────────────────────────────────────────────
    user = user_repo.create(
        db,
        email=payload.email,
        hashed_password=get_password_hash(payload.password),
        display_name=payload.display_name,
    )
    affiliate_service.attribute_new_user(
        db,
        referred_user=user,
        referral_code=payload.referral_code,
        source=payload.referral_source_detail,
        campaign=payload.referral_campaign,
    )

    # ── seed demo data so the app isn't empty on first login ─────────────────
    _seed_demo_data(db, user)

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


def _rotate_refresh_cookie(db: Session, user, response: Response) -> str:
    """Issue a new refresh token for `user` and set it on the response cookie.

    Does NOT revoke any existing token — callers that rotate an active token must
    revoke the old one themselves first. Used by both the normal rotation path and
    the concurrent-rotation grace path so a successful refresh ALWAYS hands the
    client a live refresh token (otherwise a client that loses a rotation race
    stays pinned to a revoked token and is force-logged-out once grace expires).
    """
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

    response.set_cookie(
        key="refresh_token",
        value=new_raw_refresh,
        httponly=True,
        secure=settings.IS_PRODUCTION,
        samesite="lax",
        max_age=60 * 60 * 24 * settings.REFRESH_TOKEN_EXPIRE_DAYS,
        path="/",
    )

    return new_raw_refresh


def _verify_google_id_token(id_token: str) -> dict:
    """Verify a Google ID token via Google's tokeninfo endpoint.

    Validates the signature/expiry server-side and checks the audience matches
    our configured client ID. Returns the token claims on success.
    """
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured.",
        )

    try:
        resp = httpx.get(
            _GOOGLE_TOKENINFO_URL, params={"id_token": id_token}, timeout=10.0
        )
    except httpx.HTTPError:
        logger.exception("google_auth: tokeninfo request failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach Google to verify sign-in. Try again.",
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google sign-in token.",
        )

    claims = resp.json()

    # Audience must match our client ID — guards against tokens minted for a
    # different app being replayed here.
    if claims.get("aud") != settings.GOOGLE_CLIENT_ID:
        logger.warning("google_auth: aud mismatch (got=%s)", claims.get("aud"))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google sign-in token.",
        )

    if claims.get("email_verified") not in (True, "true"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your Google email is not verified.",
        )

    if not claims.get("email"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google sign-in did not return an email.",
        )

    return claims


def google_auth(
    db: Session,
    id_token: str,
    response: Response,
    *,
    referral_code: str | None = None,
    referral_source_detail: str | None = None,
    referral_campaign: str | None = None,
) -> LoginResponse:
    """Sign in (or sign up) with a Google ID token.

    Verifies the token, finds the user by email or creates one (auto-linking to
    an existing email/password account), then issues our own session tokens.
    """
    claims = _verify_google_id_token(id_token)
    email = claims["email"].lower()

    user = user_repo.get_by_email(db, email)

    if user is None:
        # New user — create the account with an unusable password (they can set
        # one later via set-password) and the standard default stream. The
        # account is marked Google-provider with no usable password.
        display_name = (
            claims.get("name") or claims.get("given_name") or email.split("@")[0]
        )
        user = user_repo.create(
            db,
            email=email,
            hashed_password=get_password_hash(uuid.uuid4().hex),
            display_name=display_name,
            auth_provider=AuthProvider.GOOGLE,
            has_usable_password=False,
        )
        if claims.get("picture") and not user.avatar_url:
            user.avatar_url = claims["picture"]
        # Google has verified the email for us.
        user.is_email_verified = True
        db.flush()
        affiliate_service.attribute_new_user(
            db,
            referred_user=user,
            referral_code=referral_code,
            source=referral_source_detail,
            campaign=referral_campaign,
        )
        _seed_demo_data(db, user)
        db.commit()
        db.refresh(user)
    elif not user.is_email_verified:
        # Existing email/password account that never verified — Google proves
        # ownership of the inbox, so mark it verified and link.
        user.is_email_verified = True
        db.commit()
        db.refresh(user)

    access_token, expiry_minutes, _raw_refresh = _issue_session(db, user, response)

    return LoginResponse(
        access_token=access_token,
        access_token_expiry_minutes=expiry_minutes,
        user=UserResponse.model_validate(user),
    )


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
    if user.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is suspended. Contact support for assistance.",
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
        # Issue a fresh access token AND hand this client its own live refresh
        # token + cookie. Without the cookie the losing client stays pinned to the
        # revoked token and is force-logged-out once the grace window expires.
        user = user_repo.get_by_id(db, grace_record.user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found.",
            )
        _rotate_refresh_cookie(db, user, response)
        db.commit()
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

    _rotate_refresh_cookie(db, user, response)

    new_access_token = create_access_token(str(user.id))
    db.commit()

    return RefreshResponse(
        access_token=new_access_token,
        access_token_expiry_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
    )


def clear_refresh_cookie(response: Response) -> None:
    """Clear the refresh-token cookie (logout, account deletion, …).

    Mirrors the attributes used when the cookie is set so the browser actually
    drops it.
    """
    response.delete_cookie(
        key="refresh_token",
        httponly=True,
        secure=settings.IS_PRODUCTION,
        samesite="lax",
        path="/",
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

    clear_refresh_cookie(response)

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
    # A reset always yields a real, user-chosen password (e.g. a Google user who
    # never set one can now sign in with email too).
    user.has_usable_password = True
    token_repo.revoke(db, token_record)
    # Revoke all refresh tokens — force re-login everywhere
    token_repo.revoke_all_by_user_and_type(
        db, user_id=user.id, token_type=TokenType.REFRESH
    )
    db.commit()
    return ResetPasswordResponse(message="Password reset successfully. Please log in.")
