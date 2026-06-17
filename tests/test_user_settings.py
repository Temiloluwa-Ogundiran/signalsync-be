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

from app.core.security import get_password_hash, hash_token
from app.domains.users import service as user_service
from app.domains.users.schemas import (
    ChangeEmailRequest,
    ChangePasswordRequest,
    DeleteAccountRequest,
    UpdateProfileRequest,
)


def _make_user(password: str = "Password123") -> MagicMock:
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "user@example.com"
    user.hashed_password = get_password_hash(password)
    user.display_name = "Trader"
    user.bio = None
    user.avatar_url = None
    user.is_email_verified = True
    user.is_deleted = False
    return user


# ── update_profile ───────────────────────────────────────────────────────────

def test_update_profile_sets_display_name_and_bio():
    db = MagicMock()
    user = _make_user()
    user_service.update_profile(
        db,
        current_user=user,
        payload=UpdateProfileRequest(display_name="New Name", bio="  hi  "),
    )
    assert user.display_name == "New Name"
    assert user.bio == "hi"  # trimmed
    db.commit.assert_called_once()


def test_update_profile_blank_bio_clears_it():
    db = MagicMock()
    user = _make_user()
    user.bio = "something"
    user_service.update_profile(
        db, current_user=user, payload=UpdateProfileRequest(bio="")
    )
    assert user.bio is None


def test_update_profile_does_not_touch_unset_fields():
    db = MagicMock()
    user = _make_user()
    user.bio = "keep me"
    user_service.update_profile(
        db, current_user=user, payload=UpdateProfileRequest(display_name="X")
    )
    assert user.bio == "keep me"  # bio was not in the payload


# ── change_password ──────────────────────────────────────────────────────────

def test_change_password_rejects_wrong_current_password():
    db = MagicMock()
    user = _make_user(password="Password123")
    with pytest.raises(HTTPException) as exc:
        user_service.change_password(
            db,
            current_user=user,
            payload=ChangePasswordRequest(
                current_password="WrongPass1", new_password="NewPass123"
            ),
        )
    assert exc.value.status_code == 400
    db.commit.assert_not_called()


def test_change_password_rejects_same_password():
    db = MagicMock()
    user = _make_user(password="Password123")
    with pytest.raises(HTTPException) as exc:
        user_service.change_password(
            db,
            current_user=user,
            payload=ChangePasswordRequest(
                current_password="Password123", new_password="Password123"
            ),
        )
    assert exc.value.status_code == 400


def test_change_password_succeeds_and_revokes_refresh_tokens():
    db = MagicMock()
    user = _make_user(password="Password123")
    old_hash = user.hashed_password
    with patch(
        "app.domains.users.service.token_repo.revoke_all_by_user_and_type"
    ) as revoke_all:
        user_service.change_password(
            db,
            current_user=user,
            payload=ChangePasswordRequest(
                current_password="Password123", new_password="NewPass123"
            ),
        )
    assert user.hashed_password != old_hash
    revoke_all.assert_called_once()
    db.commit.assert_called_once()


# ── change_email ─────────────────────────────────────────────────────────────

def test_change_email_rejects_wrong_password():
    db = MagicMock()
    user = _make_user(password="Password123")
    with pytest.raises(HTTPException) as exc:
        user_service.change_email(
            db,
            current_user=user,
            payload=ChangeEmailRequest(
                new_email="new@example.com", current_password="Nope12345"
            ),
        )
    assert exc.value.status_code == 400


def test_change_email_rejects_taken_email():
    db = MagicMock()
    user = _make_user(password="Password123")
    other = MagicMock()
    other.id = uuid.uuid4()
    with patch(
        "app.domains.users.service.user_repo.get_by_email", return_value=other
    ):
        with pytest.raises(HTTPException) as exc:
            user_service.change_email(
                db,
                current_user=user,
                payload=ChangeEmailRequest(
                    new_email="taken@example.com", current_password="Password123"
                ),
            )
    assert exc.value.status_code == 409


def test_change_email_unverifies_and_sends_verification():
    db = MagicMock()
    user = _make_user(password="Password123")
    with (
        patch(
            "app.domains.users.service.user_repo.get_by_email", return_value=None
        ),
        patch("app.domains.users.service.token_repo.create"),
        patch("app.domains.users.service.token_repo.revoke_all_by_user_and_type"),
        patch(
            "app.domains.users.service.send_verification_email_task.delay"
        ) as send,
    ):
        user_service.change_email(
            db,
            current_user=user,
            payload=ChangeEmailRequest(
                new_email="New@Example.com", current_password="Password123"
            ),
        )
    assert user.email == "new@example.com"  # lowercased
    assert user.is_email_verified is False
    send.assert_called_once()
    db.commit.assert_called_once()


# ── delete_account ───────────────────────────────────────────────────────────

def test_delete_account_rejects_wrong_password():
    db = MagicMock()
    user = _make_user(password="Password123")
    with pytest.raises(HTTPException) as exc:
        user_service.delete_account(
            db,
            current_user=user,
            payload=DeleteAccountRequest(current_password="Wrong12345"),
            response=Response(),
        )
    assert exc.value.status_code == 400
    assert user.is_deleted is False


def test_delete_account_soft_deletes_and_clears_cookie():
    db = MagicMock()
    user = _make_user(password="Password123")
    with (
        patch("app.domains.users.service.token_repo.revoke_all_by_user_and_type"),
        patch(
            "app.domains.users.service.auth_service.clear_refresh_cookie"
        ) as clear_cookie,
    ):
        user_service.delete_account(
            db,
            current_user=user,
            payload=DeleteAccountRequest(current_password="Password123"),
            response=Response(),
        )
    assert user.is_deleted is True
    assert user.deleted_at is not None
    clear_cookie.assert_called_once()
    db.commit.assert_called_once()


# ── revoke_session ───────────────────────────────────────────────────────────

def test_revoke_session_missing_raises_404():
    db = MagicMock()
    user = _make_user()
    with patch(
        "app.domains.users.service.token_repo.get_active_by_id_for_user",
        return_value=None,
    ):
        with pytest.raises(HTTPException) as exc:
            user_service.revoke_session(
                db, current_user=user, session_id=uuid.uuid4()
            )
    assert exc.value.status_code == 404


# ── list_sessions ────────────────────────────────────────────────────────────

def test_list_sessions_flags_current_device():
    db = MagicMock()
    user = _make_user()
    raw_refresh = "raw-refresh-token"

    current = MagicMock()
    current.id = uuid.uuid4()
    current.created_at = MagicMock()
    current.expires_at = MagicMock()
    current.token = hash_token(raw_refresh)

    other = MagicMock()
    other.id = uuid.uuid4()
    other.created_at = MagicMock()
    other.expires_at = MagicMock()
    other.token = hash_token("some-other-token")

    with patch(
        "app.domains.users.service.token_repo.list_active_by_user_and_type",
        return_value=[current, other],
    ):
        sessions = user_service.list_sessions(
            db, current_user=user, current_refresh_token=raw_refresh
        )

    by_id = {s.id: s for s in sessions}
    assert by_id[current.id].is_current is True
    assert by_id[other.id].is_current is False
