import hashlib
import hmac
from datetime import datetime, timezone


def verify_bachs_signature(
    *,
    raw_body: bytes,
    secret: str,
    timestamp_header: str,
    signature_header: str,
    tolerance_seconds: int = 300,
    now: datetime | None = None,
) -> bool:
    if not secret or not timestamp_header or not signature_header:
        return False
    try:
        timestamp = int(timestamp_header)
    except (TypeError, ValueError):
        return False
    current = now or datetime.now(timezone.utc)
    if abs(current.timestamp() - timestamp) > tolerance_seconds:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp_header.encode("utf-8") + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
