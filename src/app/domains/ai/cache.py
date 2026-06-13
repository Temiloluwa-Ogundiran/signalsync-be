"""
Tool result cache keyed by (tool_name, args, account_ids, data_version).

data_version is a per-account Redis counter bumped by the core backend after each
successful sync (journal_sync_tasks.py). Stale cache entries are naturally bypassed
(version mismatch) and expire via TTL — no explicit purge needed.

Two Redis clients:
- Async (`aioredis`) for use in the async request path (router, quota, etc.)
- Sync (`redis.Redis`) for use inside LangGraph tools, which run synchronously
  in a thread pool (anyio.to_thread) and cannot await.
"""
import hashlib
import json
import logging
from functools import wraps
from typing import Callable, List

import redis as sync_redis
import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger("synctrades.ai.cache")

# ── Async client (router / quota) ─────────────────────────────────────────────

_async_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _async_redis
    if _async_redis is None:
        _async_redis = aioredis.from_url(settings.AI_REDIS_URL, decode_responses=True)
    return _async_redis


# ── Sync client (tool functions running in threads) ───────────────────────────

_sync_redis: sync_redis.Redis | None = None


def _get_sync_redis() -> sync_redis.Redis:
    global _sync_redis
    if _sync_redis is None:
        _sync_redis = sync_redis.from_url(settings.AI_REDIS_URL, decode_responses=True)
    return _sync_redis


# ── Cache-key helpers ─────────────────────────────────────────────────────────

def make_tool_cache_key(
    tool_name: str, kwargs: dict, account_ids: List[str], ver: str
) -> str:
    raw = (
        f"{tool_name}:{json.dumps(kwargs, sort_keys=True, default=str)}"
        f":{sorted(account_ids)}:{ver}"
    )
    return "tool:" + hashlib.sha1(raw.encode()).hexdigest()


def _sync_data_version(account_ids: List[str]) -> str:
    """Read per-account data_version counters synchronously (for tool threads)."""
    if not account_ids:
        return "0"
    try:
        r = _get_sync_redis()
        vals = r.mget([f"acct:ver:{a}" for a in account_ids])
        return ":".join((v or "0") for v in vals)
    except Exception:
        logger.debug("Redis unavailable — cache bypassed for this tool call")
        return "uncached"


# ── Async helpers (request path) ─────────────────────────────────────────────

async def data_version(account_ids: List[str]) -> str:
    if not account_ids:
        return "0"
    try:
        r = get_redis()
        vals = await r.mget([f"acct:ver:{a}" for a in account_ids])
        return ":".join(v or "0" for v in vals)
    except Exception:
        logger.warning("Redis unavailable — bypassing tool cache")
        return "uncached"


async def get_cached(key: str) -> str | None:
    try:
        return await get_redis().get(key)
    except Exception:
        return None


async def set_cached(key: str, value: str, ttl: int = 6 * 3600) -> None:
    try:
        await get_redis().set(key, value, ex=ttl)
    except Exception:
        pass


# ── tool_cache decorator (sync tools in thread pool) ─────────────────────────

def tool_cache(ttl: int = 6 * 3600) -> Callable:
    """
    Decorator for sync LangGraph tool functions.

    Extracts `account_ids` from kwargs, reads the current data_version from
    Redis, and caches the string result keyed by (tool_name, kwargs, version).
    A Redis failure is silenced — the tool runs normally on cache miss.
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            account_ids: List[str] = kwargs.get("account_ids", [])
            # Strip non-serialisable args (LangGraph sometimes passes ToolCall objects)
            cache_kwargs = {
                k: v for k, v in kwargs.items() if k != "account_ids"
            }
            try:
                ver = _sync_data_version(account_ids)
                # Skip caching when Redis is unavailable
                if ver == "uncached":
                    return fn(*args, **kwargs)

                key = make_tool_cache_key(fn.__name__, cache_kwargs, account_ids, ver)
                r = _get_sync_redis()

                hit = r.get(key)
                if hit is not None:
                    logger.debug("Tool cache HIT  %s (ver=%s)", fn.__name__, ver)
                    return hit

                result = fn(*args, **kwargs)

                try:
                    r.set(key, result if isinstance(result, str) else json.dumps(result, default=str), ex=ttl)
                    logger.debug("Tool cache MISS %s (ver=%s)", fn.__name__, ver)
                except Exception:
                    pass

                return result

            except Exception:
                # Defensive: never let caching break the tool
                return fn(*args, **kwargs)

        wrapper._cacheable = True
        wrapper._cache_ttl = ttl
        return wrapper

    return decorator
