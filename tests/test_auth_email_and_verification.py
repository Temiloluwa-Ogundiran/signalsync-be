import os
import uuid

import pytest
from fastapi import HTTPException, Response
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import app.domains.auth.models  # noqa: F401
import app.domains.journal.models  # noqa: F401
import app.domains.posts.models  # noqa: F401
import app.domains.streams.models  # noqa: F401

from app.domains.auth import service as auth_service
from app.domains.auth.schemas import RegisterRequest, ResendVerificationRequest
from app.shared.utils.email import send_verification_email


def test_send_verification_email_posts_resend_payload() -> None:
    with patch("app.shared.utils.email.httpx.post") as post_mock, patch(
        "app.shared.utils.email.settings"
    ) as settings_mock:
        settings_mock.RESEND_API_KEY = "re_test"
        settings_mock.EMAIL_FROM = "hello@synctrades.com"
        settings_mock.EMAIL_FROM_NAME = "SyncTrades"
        settings_mock.FRONTEND_URL = "https://app.synctrades.com"
        settings_mock.EMAIL_VERIFY_EXPIRY_HOURS = 24
        post_mock.return_value.raise_for_status.return_value = None

        send_verification_email("user@example.com", "token-123")

    post_mock.assert_called_once()
    payload = post_mock.call_args.kwargs["json"]
    assert payload["to"] == ["user@example.com"]
    assert payload["from"] == "SyncTrades <hello@synctrades.com>"
    assert "verify-email?token=token-123" in payload["html"]


def test_send_verification_email_raises_when_resend_is_unconfigured() -> None:
    with patch("app.shared.utils.email.settings") as settings_mock:
        settings_mock.RESEND_API_KEY = ""
        settings_mock.EMAIL_FROM = ""

        with pytest.raises(RuntimeError):
            send_verification_email("user@example.com", "token-123")


def test_register_raises_when_email_delivery_fails() -> None:
    db = MagicMock()
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "user@example.com"
    user.display_name = "Trader"
    with (
        patch("app.domains.auth.service.user_repo.get_by_email", return_value=None),
        patch("app.domains.auth.service.user_repo.get_by_username", return_value=None),
        patch("app.domains.auth.service.user_repo.create", return_value=user),
        patch("app.domains.auth.service.stream_repo.create"),
        patch("app.domains.auth.service.token_repo.create"),
        patch(
            "app.domains.auth.service.send_verification_email",
            side_effect=RuntimeError("resend down"),
        ),
    ):
        with pytest.raises(RuntimeError, match="resend down"):
            auth_service.register(
                db,
                RegisterRequest(
                    email="user@example.com",
                    username="trader",
                    display_name="Trader",
                    password="password123",
                ),
            )


def test_resend_verification_raises_for_real_delivery_failure() -> None:
    db = MagicMock()
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "user@example.com"
    user.is_email_verified = False
    db.query.return_value.filter_by.return_value.all.return_value = []
    with (
        patch("app.domains.auth.service.user_repo.get_by_email", return_value=user),
        patch("app.domains.auth.service.token_repo.create"),
        patch(
            "app.domains.auth.service.send_verification_email",
            side_effect=RuntimeError("resend down"),
        ),
    ):
        with pytest.raises(RuntimeError, match="resend down"):
            auth_service.resend_verification(
                db, ResendVerificationRequest(email="user@example.com")
            )


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
