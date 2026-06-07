import os

import pytest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

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
