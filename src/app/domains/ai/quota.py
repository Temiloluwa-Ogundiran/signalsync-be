"""Per-user monthly AI credit quota and usage accounting."""

import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import HTTPException, status

from app.core.config import settings
from app.core.database import SessionLocal
from app.domains.ai import repository as ai_repo
from app.domains.billing import service as billing_service

logger = logging.getLogger("synctrades.ai.quota")

_redis: aioredis.Redis | None = None

PLAN_CREDITS: dict[str, int] = {
    "free": settings.AI_CREDITS_FREE,
    "journal": settings.AI_CREDITS_ESSENTIAL,
    "copy": settings.AI_CREDITS_PRO,
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


def _plan_for_user(db, user_id) -> str:
    subscription = billing_service.get_subscription(db, user_id=user_id)
    access = billing_service.subscription_response(subscription)
    if access.has_copy_access:
        return "copy"
    if access.has_journal_access:
        return "journal"
    return "free"


def _db_snapshot(user_id) -> tuple[str, int, int]:
    with SessionLocal() as db:
        plan = _plan_for_user(db, user_id)
        usage = ai_repo.get_or_create_usage(
            db,
            user_id=user_id,
            period_month=_period_month(),
        )
        return (
            plan,
            int(usage.credits_used or 0),
            int(getattr(usage, "message_count", 0) or 0),
        )


async def _authoritative_usage(user_id) -> tuple[str, int, int]:
    plan, db_used, message_count = _db_snapshot(user_id)
    try:
        redis_used = int(await get_redis().get(_credit_key(str(user_id))) or 0)
    except Exception:
        logger.warning("Redis unavailable; using DB-backed quota for user %s", user_id)
        redis_used = 0
    return plan, max(db_used, redis_used), message_count


async def check(user_id) -> None:
    """Raise HTTP 402 if the user has exhausted their monthly credit allowance."""
    if not settings.AI_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI features are currently disabled.",
        )
    plan, used, _message_count = await _authoritative_usage(user_id)
    limit = PLAN_CREDITS.get(plan, PLAN_CREDITS["free"])
    if used >= limit:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Monthly AI credits used up. Upgrade your plan for more.",
        )


async def debit(
    user_id,
    credits: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """Persist usage in the database, then refresh the Redis accelerator."""
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
            usage = ai_repo.get_or_create_usage(
                db,
                user_id=user_id,
                period_month=_period_month(),
            )
            authoritative_total = int(usage.credits_used or 0)
    except Exception as exc:
        logger.exception("Failed to persist ai_usage for user %s", user_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI usage accounting is temporarily unavailable.",
        ) from exc

    try:
        await get_redis().set(
            _credit_key(str(user_id)),
            authoritative_total,
            ex=40 * 86400,
        )
    except Exception:
        logger.warning(
            "Redis unavailable; DB-backed credit debit retained for user %s",
            user_id,
        )


async def get_usage_response(user_id) -> dict:
    plan, used, message_count = await _authoritative_usage(user_id)
    limit = PLAN_CREDITS.get(plan, PLAN_CREDITS["free"])
    return {
        "credits_used": used,
        "credits_limit": limit,
        "credits_remaining": max(0, limit - used),
        "period_month": _period_month(),
        "message_count": message_count,
    }
