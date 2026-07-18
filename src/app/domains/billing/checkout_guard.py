import uuid

import redis.asyncio as redis

from app.core.config import settings


_CREATING = "creating"
_CHECKOUT_TTL_SECONDS = 60 * 60
_redis: redis.Redis | None = None


class CheckoutGuardUnavailable(RuntimeError):
    pass


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.from_url(settings.AI_REDIS_URL, decode_responses=True)
    return _redis


def _key(user_id: uuid.UUID) -> str:
    return f"billing:checkout:{user_id}"


async def acquire(user_id: uuid.UUID) -> str | None:
    client = get_redis()
    try:
        acquired = await client.set(
            _key(user_id),
            _CREATING,
            ex=_CHECKOUT_TTL_SECONDS,
            nx=True,
        )
        if acquired:
            return None
        current = await client.get(_key(user_id))
    except redis.RedisError as exc:
        raise CheckoutGuardUnavailable("Checkout protection is temporarily unavailable") from exc
    return current if isinstance(current, str) and current.startswith("https://") else _CREATING


async def store(user_id: uuid.UUID, checkout_url: str) -> None:
    try:
        await get_redis().set(
            _key(user_id), checkout_url, ex=_CHECKOUT_TTL_SECONDS
        )
    except redis.RedisError as exc:
        raise CheckoutGuardUnavailable("Checkout protection is temporarily unavailable") from exc


async def release(user_id: uuid.UUID) -> None:
    try:
        await get_redis().delete(_key(user_id))
    except redis.RedisError:
        pass
