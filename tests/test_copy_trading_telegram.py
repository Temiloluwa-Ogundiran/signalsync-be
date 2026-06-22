from unittest.mock import MagicMock, patch
import uuid

from app.domains.copy_trading.models import (
    AutomationConfidence,
    TelegramSourceState,
)
from app.domains.copy_trading.telegram_auth import (
    image_message_outcome,
    merge_auth_state,
)
from app.domains.copy_trading.worker_runtime import (
    _learning_source_outcome,
    _mark_learning_failed,
)


def test_low_confidence_learning_is_advisory_and_copyable() -> None:
    state, reason = _learning_source_outcome(
        confidence=AutomationConfidence.low,
        image_primary=False,
    )

    assert state == TelegramSourceState.advisory
    assert "review" in reason.lower()


def test_image_primary_has_a_distinct_unsupported_state() -> None:
    state, reason = _learning_source_outcome(
        confidence=AutomationConfidence.high,
        image_primary=True,
    )

    assert state == TelegramSourceState.unsupported_image_primary
    assert "image" in reason.lower()


@patch("app.domains.copy_trading.worker_runtime.SessionLocal")
def test_learning_failure_is_retryable_not_unsupported(session_local) -> None:
    source = MagicMock(id=uuid.uuid4(), user_id=uuid.uuid4())
    db = session_local.return_value.__enter__.return_value
    db.get.return_value = source

    _mark_learning_failed(source.id, "Try again", "timeout")

    assert source.state == TelegramSourceState.failed_retryable


def test_repeated_image_only_messages_pause_the_source() -> None:
    assert image_message_outcome(1).disconnect is False
    assert image_message_outcome(3).disconnect is True


def test_persisted_auth_state_merges_without_retaining_passwords() -> None:
    state = merge_auth_state(
        {"phone": "+234000", "session": "encrypted-session"},
        {"state": "awaiting_password", "password": "secret"},
    )

    assert state["state"] == "awaiting_password"
    assert state["session"] == "encrypted-session"
    assert "password" not in state
