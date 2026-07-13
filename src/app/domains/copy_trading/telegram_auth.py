import json
from dataclasses import dataclass

from app.domains.copy_trading.models import TelegramAuthState


SENSITIVE_AUTH_FIELDS = {"password", "code"}


def merge_auth_state(current: dict | None, updates: dict) -> dict:
    merged = dict(current or {})
    merged.update(
        {
            key: value
            for key, value in updates.items()
            if key not in SENSITIVE_AUTH_FIELDS and value is not None
        }
    )
    for key in SENSITIVE_AUTH_FIELDS:
        merged.pop(key, None)
    return merged


def decode_auth_state(attempt, cipher) -> dict:
    if not attempt.encrypted_state:
        return {}
    return json.loads(cipher.decrypt(attempt.encrypted_state))


def encode_auth_state(payload: dict, cipher) -> str:
    return cipher.encrypt(json.dumps(payload, separators=(",", ":")))


def auth_state_from_public(value: str) -> TelegramAuthState:
    return {
        "starting": TelegramAuthState.pending,
        "code_required": TelegramAuthState.awaiting_code,
        "password_required": TelegramAuthState.awaiting_password,
        "verifying": TelegramAuthState.pending,
        "qr_required": TelegramAuthState.pending,
        "ready": TelegramAuthState.ready,
        "failed": TelegramAuthState.failed,
        "expired": TelegramAuthState.expired,
    }.get(value, TelegramAuthState.pending)


@dataclass(frozen=True)
class ImageMessageOutcome:
    disconnect: bool
    message: str


def image_message_outcome() -> ImageMessageOutcome:
    return ImageMessageOutcome(
        disconnect=False,
        message="An image-only message was skipped because image signals are not processed.",
    )
