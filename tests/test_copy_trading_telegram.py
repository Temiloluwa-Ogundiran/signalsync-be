from app.domains.copy_trading.telegram_auth import (
    image_message_outcome,
    merge_auth_state,
)


def test_image_only_messages_are_skipped_without_disconnecting_source() -> None:
    assert image_message_outcome().disconnect is False
    assert "skipped" in image_message_outcome().message.lower()


def test_persisted_auth_state_merges_without_retaining_passwords() -> None:
    state = merge_auth_state(
        {"phone": "+234000", "session": "encrypted-session"},
        {"state": "awaiting_password", "password": "secret"},
    )

    assert state["state"] == "awaiting_password"
    assert state["session"] == "encrypted-session"
    assert "password" not in state
