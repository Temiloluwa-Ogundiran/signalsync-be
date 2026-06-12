from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

# Shared limiter instance. Keyed by client IP (get_remote_address reads
# request.client.host, which reflects the real client only when the proxy/forwarded
# headers are trusted — see FORWARDED_ALLOW_IPS in the Gunicorn config).
#
# The default limit is applied globally by SlowAPIMiddleware; individual routes can
# override with a tighter @limiter.limit("...") decorator.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_DEFAULT] if settings.RATE_LIMIT_ENABLED else [],
    headers_enabled=True,
    enabled=settings.RATE_LIMIT_ENABLED,
)
