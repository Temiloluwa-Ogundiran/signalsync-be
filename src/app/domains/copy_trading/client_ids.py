import base64
import hashlib
import uuid


def _segment(value: bytes) -> str:
    digest = hashlib.blake2s(value, digest_size=5).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=")


def metaapi_client_id(*, route_id: uuid.UUID, intent_id: uuid.UUID) -> str:
    strategy_id = _segment(route_id.bytes)
    position_id = _segment(route_id.bytes + intent_id.bytes)
    order_id = _segment(intent_id.bytes)
    return f"{strategy_id}_{position_id}_{order_id}"
