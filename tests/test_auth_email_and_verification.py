import os
import uuid

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import app.domains.auth.models  # noqa: F401
import app.domains.journal.models  # noqa: F401

from app.domains.auth import service as auth_service
from app.domains.auth.schemas import (
    PASSWORD_POLICY_MESSAGE,
    RegisterRequest,
    ResendVerificationRequest,
)
from app.shared.utils.email import send_verification_email


def test_send_verification_email_posts_resend_payload() -> None:
    with patch("app.shared.utils.email.httpx.post") as post_mock, patch(
        "app.shared.utils.email.settings"
    ) as settings_mock:
        settings_mock.RESEND_API_KEY = "re_test"
        settings_mock.EMAIL_FROM = "hello@synctrades.com"
        settings_mock.EMAIL_FROM_NAME = "SignalSync"
        settings_mock.FRONTEND_URL = "https://app.synctrades.com"
        settings_mock.EMAIL_VERIFY_EXPIRY_HOURS = 24
        post_mock.return_value.status_code = 200
        post_mock.return_value.raise_for_status.return_value = None

        send_verification_email("user@example.com", "token-123")

    post_mock.assert_called_once()
    payload = post_mock.call_args.kwargs["json"]
    assert payload["to"] == ["user@example.com"]
    assert payload["from"] == "SignalSync <hello@synctrades.com>"
    assert "verify-email?token=token-123" in payload["html"]
    # Branded redesign: logo image + CTA button present.
    assert "signalsync-mark.png" in payload["html"]
    assert "Verify email" in payload["html"]


def test_send_verification_email_raises_when_resend_is_unconfigured() -> None:
    with patch("app.shared.utils.email.settings") as settings_mock:
        settings_mock.RESEND_API_KEY = ""
        settings_mock.EMAIL_FROM = ""

        with pytest.raises(RuntimeError):
            send_verification_email("user@example.com", "token-123")


def test_register_swallows_email_delivery_failure() -> None:
    # P2-10: registration commit succeeds and the verification-email enqueue
    # failure is logged but NOT re-raised (do NOT roll back the new account).
    from datetime import datetime, timezone

    db = MagicMock()
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "user@example.com"
    user.display_name = "Trader"
    user.bio = None
    user.avatar_url = None
    user.is_email_verified = False
    user.auth_provider = "email"
    user.has_usable_password = True
    user.display_timezone = None
    user.onboarding_completed = False
    user.trading_experience = None
    user.primary_goal = None
    user.referral_source = None
    user.platform_role = "user"
    user.created_at = datetime.now(timezone.utc)
    with (
        patch("app.domains.auth.service.user_repo.get_by_email", return_value=None),
        patch("app.domains.auth.service.user_repo.create", return_value=user),
        patch("app.domains.auth.service.token_repo.create"),
        patch(
            "app.domains.auth.service.send_verification_email_task.delay",
            side_effect=RuntimeError("resend down"),
        ),
    ):
        response = auth_service.register(
            db,
            RegisterRequest(
                email="user@example.com",
                display_name="Trader",
                password="Password123",
            ),
        )

    db.commit.assert_called_once()
    assert "check your email" in response.message


def test_resend_verification_swallows_real_delivery_failure() -> None:
    # P2-10: the enqueue failure is logged but NOT re-raised; the endpoint
    # still returns its generic success message.
    db = MagicMock()
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "user@example.com"
    user.is_email_verified = False
    db.query.return_value.filter_by.return_value.all.return_value = []
    db.execute.return_value.scalars.return_value = []
    with (
        patch("app.domains.auth.service.user_repo.get_by_email", return_value=user),
        patch("app.domains.auth.service.token_repo.create"),
        patch(
            "app.domains.auth.service.send_verification_email_task.delay",
            side_effect=RuntimeError("resend down"),
        ),
    ):
        response = auth_service.resend_verification(
            db, ResendVerificationRequest(email="user@example.com")
        )

    assert "a new link has been sent" in response.message


def test_login_still_blocks_unverified_user() -> None:
    db = MagicMock()
    response = Response()
    user = MagicMock()
    user.hashed_password = "hashed"
    user.is_email_verified = False
    with (
        patch("app.domains.auth.service.user_repo.get_by_email", return_value=user),
        patch("app.domains.auth.service.verify_password", return_value=True),
    ):
        with pytest.raises(HTTPException) as exc:
            auth_service.login(db, "user@example.com", "password123", response)

    assert exc.value.status_code == 403
    assert exc.value.detail == "Please verify your email before logging in."


def test_register_request_rejects_weak_password() -> None:
    with pytest.raises(ValidationError) as exc:
        RegisterRequest(
            email="user@example.com",
            display_name="Trader",
            password="password",
        )

    assert PASSWORD_POLICY_MESSAGE in str(exc.value)


def test_register_request_accepts_strong_password() -> None:
    payload = RegisterRequest(
        email="user@example.com",
        display_name="Trader",
        password="Password123",
    )

    assert payload.password == "Password123"


def test_verify_email_success() -> None:
    from datetime import datetime, timezone, timedelta
    db = MagicMock()
    token = MagicMock()
    token.user_id = uuid.uuid4()
    token.is_revoked = False
    token.expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    
    user = MagicMock()
    user.is_email_verified = False
    
    db.execute.return_value.scalar_one_or_none.return_value = token

    with (
        patch("app.domains.auth.service.user_repo.get_by_id", return_value=user),
        patch("app.domains.auth.service.token_repo.revoke") as revoke_mock,
    ):
        res = auth_service.verify_email(db, "raw-token", MagicMock())

    assert res.message == "Email verified successfully."
    assert user.is_email_verified is True
    revoke_mock.assert_called_once_with(db, token)


def test_verify_email_idempotent_success() -> None:
    from datetime import datetime, timezone, timedelta
    db = MagicMock()
    token = MagicMock()
    token.user_id = uuid.uuid4()
    token.is_revoked = True
    token.expires_at = datetime.now(timezone.utc) - timedelta(hours=2)
    
    user = MagicMock()
    user.is_email_verified = True
    
    db.execute.return_value.scalar_one_or_none.return_value = token
    
    with (
        patch("app.domains.auth.service.user_repo.get_by_id", return_value=user),
        patch("app.domains.auth.service.token_repo.revoke") as revoke_mock,
    ):
        res = auth_service.verify_email(db, "raw-token", MagicMock())

    assert res.message == "Email is already verified."
    revoke_mock.assert_not_called()
    db.commit.assert_not_called()


def test_verify_email_token_expired_fails() -> None:
    from datetime import datetime, timezone, timedelta
    db = MagicMock()
    token = MagicMock()
    token.user_id = uuid.uuid4()
    token.is_revoked = False
    token.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    
    user = MagicMock()
    user.is_email_verified = False
    
    db.execute.return_value.scalar_one_or_none.return_value = token
    
    with (
        patch("app.domains.auth.service.user_repo.get_by_id", return_value=user),
    ):
        with pytest.raises(HTTPException) as exc:
            auth_service.verify_email(db, "raw-token", MagicMock())

    assert exc.value.status_code == 400
    assert "expired" in exc.value.detail


def test_verify_email_token_not_found_fails() -> None:
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None
    
    with pytest.raises(HTTPException) as exc:
        auth_service.verify_email(db, "non-existent-token", MagicMock())
        
    assert exc.value.status_code == 400
    assert "Invalid or expired" in exc.value.detail

