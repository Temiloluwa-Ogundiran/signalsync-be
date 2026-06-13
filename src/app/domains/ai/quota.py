"""
Per-user monthly credit quota checked before every AI turn.
Credits are mirrored to ai_usage for billing reconciliation.
"""
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import HTTPException, status

from app.core.config import settings
from app.core.database import SessionLocal
from app.domains.ai import repository as ai_repo

logger = logging.getLogger("synctrades.ai.quota")

_redis: aioredis.Redis | None = None

PLAN_CREDITS: dict = {
    "free": settings.AI_CREDITS_FREE,
    "essential": settings.AI_CREDITS_ESSENTIAL,
    "pro": settings.AI_CREDITS_PRO,
}


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.AI_REDIS_URL, decode_responses=True)
    return _redis


def _period_month() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _credit_key(user_id: str) -> str:
    month = _period_month().strftime("%Y-%m")
    return f"ai:cred:{user_id}:{month}"


async def check(user_id) -> None:
    """Raise HTTP 402 if the user has exhausted their monthly credit allowance."""
    if not settings.AI_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI features are currently disabled.",
        )
    # TODO: replace with real plan lookup from users/subscriptions domain
    plan = "free"
    limit = PLAN_CREDITS.get(plan, PLAN_CREDITS["free"])

    try:
        r = get_redis()
        used = int(await r.get(_credit_key(str(user_id))) or 0)
    except Exception:
        logger.warning("Redis unavailable — skipping quota check for user %s", user_id)
        return

    if used >= limit:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Monthly AI credits used up. Upgrade your plan for more.",
        )


async def debit(user_id, credits: int, input_tokens: int = 0, output_tokens: int = 0) -> None:
    """Debit credits from Redis counter and mirror to ai_usage table."""
    key = _credit_key(str(user_id))
    try:
        r = get_redis()
        new_val = await r.incrby(key, credits)
        if new_val == credits:
            # First debit this month — set a safety TTL past month end
            await r.expire(key, 40 * 86400)
    except Exception:
        logger.warning("Redis unavailable — credit debit skipped for user %s", user_id)

    # Mirror to DB for billing reconciliation (best-effort)
    try:
        with SessionLocal() as db:
            ai_repo.increment_usage(
                db,
                user_id=user_id,
                period_month=_period_month(),
                credits=credits,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            db.commit()
    except Exception:
        logger.warning("Failed to persist ai_usage for user %s", user_id, exc_info=True)


async def get_usage_response(user_id) -> dict:
    plan = "free"
    limit = PLAN_CREDITS.get(plan, PLAN_CREDITS["free"])
    try:
        r = get_redis()
        used = int(await r.get(_credit_key(str(user_id))) or 0)
    except Exception:
        used = 0

    with SessionLocal() as db:
        usage_row = ai_repo.get_or_create_usage(
            db, user_id=user_id, period_month=_period_month()
        )
        message_count = usage_row.message_count

    return {
        "credits_used": used,
        "credits_limit": limit,
        "credits_remaining": max(0, limit - used),
        "period_month": _period_month(),
        "message_count": message_count,
    }
