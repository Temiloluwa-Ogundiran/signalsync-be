from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings
from app.core.security import decode_token

# Shared limiter instance. Keyed by authenticated user (sub claim) if available,
# or client IP otherwise (requires trusted proxy headers — see FORWARDED_ALLOW_IPS).
def rate_limit_key(request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        payload = decode_token(auth.removeprefix("Bearer ").strip())
        if payload and payload.get("sub"):
            return f"user:{payload['sub']}"
    return get_remote_address(request)


limiter = Limiter(
    key_func=rate_limit_key,
    default_limits=[settings.RATE_LIMIT_DEFAULT] if settings.RATE_LIMIT_ENABLED else [],
    headers_enabled=True,
    enabled=settings.RATE_LIMIT_ENABLED,
    storage_uri=settings.RATE_LIMIT_REDIS_URL,
    # If the rate-limit storage (Redis) is unreachable, degrade to per-process
    # in-memory limiting instead of 500-ing every request. slowapi auto-recovers
    # to Redis once it comes back. Without this, a storage ConnectionError is
    # mis-routed to the rate-limit-exceeded handler and crashes on `exc.detail`.
    in_memory_fallback_enabled=True,
)
