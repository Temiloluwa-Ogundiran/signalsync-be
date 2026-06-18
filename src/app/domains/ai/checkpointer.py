"""
LangGraph checkpointer on DATABASE_URL_DIRECT.

We bypass PgBouncer for the checkpointer because LangGraph's PostgresSaver relies
on server-prepared statements and session-level state — both incompatible with
PgBouncer's transaction pooling mode (RULES §3).

DATABASE_URL_DIRECT is already used by Alembic for the same reason.
"""
import logging
import os

from app.core.config import settings

logger = logging.getLogger("synctrades.ai.checkpointer")

_checkpointer = None


def get_direct_url() -> str:
    return settings.DATABASE_URL_DIRECT or settings.DATABASE_URL


async def init_checkpointer():
    """Open the async Postgres pool for LangGraph. Call once at lifespan startup."""
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    url = get_direct_url()
    # autocommit=True is required: LangGraph's setup() runs CREATE INDEX CONCURRENTLY
    # which Postgres forbids inside a transaction block. LangGraph manages its own
    # explicit transactions, so autocommit at the pool level is correct and safe.
    #
    # check + max_idle/max_lifetime defend against the server (Railway/Postgres)
    # silently dropping idle connections between AI requests: `check` validates a
    # connection before it's handed out (a dead one is recycled instead of
    # crashing the SSE stream mid-flight — see ai.router SSE stream errors), and
    # the idle/lifetime caps refresh connections before the server kills them.
    pool = AsyncConnectionPool(
        url,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={"autocommit": True},
        check=AsyncConnectionPool.check_connection,
        max_idle=120.0,      # recycle a connection idle longer than 2 min
        max_lifetime=1800.0,  # hard cap: replace any connection after 30 min
        reconnect_timeout=10.0,
    )
    await pool.open()
    _checkpointer = AsyncPostgresSaver(pool)
    await _checkpointer.setup()
    logger.info("LangGraph async checkpointer initialised on DATABASE_URL_DIRECT")
    return _checkpointer


def get_checkpointer():
    return _checkpointer
