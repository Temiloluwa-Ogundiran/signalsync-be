import os
import uuid

import pytest
from fastapi import HTTPException, Response
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/test")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import app.domains.auth.models  # noqa: F401
import app.domains.journal.models  # noqa: F401

from app.domains.auth import service as auth_service


def _has_refresh_cookie(response: Response) -> bool:
    """True if the response carries a non-empty refresh_token Set-Cookie."""
    set_cookies = response.headers.getlist("set-cookie")
    return any(
        c.startswith("refresh_token=") and not c.startswith("refresh_token=;")
        for c in set_cookies
    )


def test_refresh_normal_rotation_revokes_old_and_sets_new_cookie() -> None:
    db = MagicMock()
    response = Response()
    user = MagicMock()
    user.id = uuid.uuid4()

    active_token = MagicMock()
    active_token.user_id = user.id

    with (
        patch(
            "app.domains.auth.service.token_repo.get_active",
            return_value=active_token,
        ),
        patch("app.domains.auth.service.user_repo.get_by_id", return_value=user),
        patch("app.domains.auth.service.token_repo.revoke") as revoke_mock,
        patch("app.domains.auth.service.token_repo.create") as create_mock,
    ):
        res = auth_service.refresh_access_token(db, "raw-refresh", response)

    revoke_mock.assert_called_once_with(db, active_token)
    create_mock.assert_called_once()
    assert res.access_token
    assert _has_refresh_cookie(response)


def test_refresh_grace_path_issues_live_cookie() -> None:
    """The concurrent-rotation grace path must hand the losing client a live
    refresh token + cookie, otherwise it stays pinned to the revoked token and
    is force-logged-out once the grace window expires (the logout bug)."""
    db = MagicMock()
    response = Response()
    user = MagicMock()
    user.id = uuid.uuid4()

    grace_record = MagicMock()
    grace_record.user_id = user.id

    with (
        # Token is no longer active (it was just rotated by a parallel request)...
        patch("app.domains.auth.service.token_repo.get_active", return_value=None),
        # ...but it's within the grace window.
        patch(
            "app.domains.auth.service.token_repo.get_recently_revoked",
            return_value=grace_record,
        ),
        patch("app.domains.auth.service.user_repo.get_by_id", return_value=user),
        patch("app.domains.auth.service.token_repo.create") as create_mock,
        patch("app.domains.auth.service.token_repo.revoke") as revoke_mock,
    ):
        res = auth_service.refresh_access_token(db, "stale-but-in-grace", response)

    # A fresh refresh token was minted for this client...
    create_mock.assert_called_once()
    # ...and the grace path does NOT revoke (the prior rotation already did).
    revoke_mock.assert_not_called()
    assert res.access_token
    # The key fix: the grace response now carries a live refresh cookie.
    assert _has_refresh_cookie(response)


def test_refresh_rejects_token_past_grace() -> None:
    db = MagicMock()
    response = Response()

    with (
        patch("app.domains.auth.service.token_repo.get_active", return_value=None),
        patch(
            "app.domains.auth.service.token_repo.get_recently_revoked",
            return_value=None,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            auth_service.refresh_access_token(db, "long-dead-token", response)

    assert exc.value.status_code == 401
    assert not _has_refresh_cookie(response)


def test_refresh_rejects_missing_token() -> None:
    db = MagicMock()
    response = Response()

    with pytest.raises(HTTPException) as exc:
        auth_service.refresh_access_token(db, None, response)

    assert exc.value.status_code == 401
