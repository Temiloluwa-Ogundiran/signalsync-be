from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Callable

import anyio
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import decode_token
from app.domains.users import repository as user_repo

logger = logging.getLogger(__name__)


def _touch_last_active(user_id: uuid.UUID, observed_at: datetime) -> None:
    """Blocking last-active update. Runs in a worker thread, not the event loop."""
    try:
        with SessionLocal() as db:
            updated = user_repo.touch_last_active_at_if_stale(
                db,
                user_id=user_id,
                observed_at=observed_at,
                min_interval_seconds=settings.USER_ACTIVITY_TOUCH_MIN_INTERVAL_SECONDS,
            )
            if updated:
                db.commit()
    except Exception:  # pragma: no cover - best-effort, never fail the request
        logger.warning("Failed to update last_active_at for user %s", user_id, exc_info=True)


class AuthActivityMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Response],
    ) -> Response:
        response = await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return response

        token = auth_header.removeprefix("Bearer ").strip()
        payload = decode_token(token)
        user_id_raw = payload.get("sub") if payload else None
        if not user_id_raw:
            return response

        try:
            user_id = uuid.UUID(str(user_id_raw))
        except ValueError:
            return response

        # Offload the synchronous DB write to a worker thread so it never blocks
        # the event loop (which would serialize every other request on this worker).
        observed_at = datetime.now(timezone.utc)
        await anyio.to_thread.run_sync(_touch_last_active, user_id, observed_at)

        return response
