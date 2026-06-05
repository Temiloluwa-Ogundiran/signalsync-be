from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import decode_token
from app.domains.users import repository as user_repo


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

        observed_at = datetime.now(timezone.utc)
        with SessionLocal() as db:
            updated = user_repo.touch_last_active_at_if_stale(
                db,
                user_id=user_id,
                observed_at=observed_at,
                min_interval_seconds=settings.USER_ACTIVITY_TOUCH_MIN_INTERVAL_SECONDS,
            )
            if updated:
                db.commit()

        return response
