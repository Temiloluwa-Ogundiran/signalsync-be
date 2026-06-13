import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response as FastAPIResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi import _rate_limit_exceeded_handler

from app.domains.ai.router import router as ai_router
from app.domains.ai.checkpointer import init_checkpointer
from app.domains.ai.agent import build_compiled
from app.domains.auth.router import router as auth_router
from app.domains.users.router import router as users_router
from app.domains.streams.router import router as streams_router
from app.domains.uploads.router import router as uploads_router
from app.domains.posts.router import posts_router, stream_posts_router
from app.domains.accounts.router import router as accounts_router
from app.domains.csv_import.router import router as csv_import_router
from app.domains.journal.router import (
    analytics_router as journal_analytics_router,
    daily_router as journal_daily_router,
    messages_router as journal_messages_router,
    tags_router as journal_tags_router,
    templates_router as journal_templates_router,
    trades_router as journal_trades_router,
)
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging, request_id_ctx
from app.domains.journal import service as journal_service
from app.domains.journal.repository import seed_system_tags
from app.core.rate_limit import limiter
from app.shared.activity import AuthActivityMiddleware
from app.shared.request_id import RequestIdMiddleware

configure_logging(debug=settings.DEBUG, json_logs=settings.LOG_JSON)
logger = logging.getLogger("synctrades")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.AUTO_SEED_ON_STARTUP:
        logger.info("Seeding system journal templates and tags on startup")
        with SessionLocal() as db:
            journal_service.seed_system_journal_templates(db)
            seed_system_tags(db)
            db.commit()

    if settings.AI_ENABLED and settings.OPENAI_API_KEY:
        try:
            checkpointer = await init_checkpointer()
            build_compiled(checkpointer=checkpointer)
        except Exception:
            logger.exception("AI checkpointer failed to initialise; compiling AI engine without checkpointing")
            try:
                build_compiled(checkpointer=None)
            except Exception:
                logger.exception("AI engine failed to initialise after checkpointer fallback")

    yield


app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    lifespan=lifespan,
)

# Per-IP rate limiting. SlowAPIMiddleware enforces the global default limit on every
# route; routes may tighten it with @limiter.limit(...).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Middleware is applied bottom-up: RequestIdMiddleware is added last so it is the
# outermost layer and a correlation id is set before any other middleware runs.
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(AuthActivityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
)
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(RequestIdMiddleware)


MAX_JSON_BODY = 1 * 1024 * 1024  # 1 MiB


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH") and not request.url.path.startswith("/uploads"):
        cl = request.headers.get("content-length")
        if cl and int(cl) > MAX_JSON_BODY:
            return JSONResponse(status_code=413, content={"detail": "Request body too large."})
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all so unhandled errors return a clean 500 instead of leaking internals."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error.",
            "request_id": request_id_ctx.get(),
        },
    )


@app.get("/")
def health_check():
    return {"status": "ok", "app": settings.APP_NAME}


# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(streams_router)
app.include_router(uploads_router)
app.include_router(stream_posts_router)
app.include_router(posts_router)
app.include_router(accounts_router)
app.include_router(csv_import_router)
app.include_router(journal_trades_router)
app.include_router(journal_daily_router)
app.include_router(journal_messages_router)
app.include_router(journal_templates_router)
app.include_router(journal_analytics_router)
app.include_router(journal_tags_router)
app.include_router(ai_router)
