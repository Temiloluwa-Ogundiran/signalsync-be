from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.domains.auth.router import router as auth_router
from app.domains.users.router import router as users_router
from app.domains.streams.router import router as streams_router
from app.domains.uploads.router import router as uploads_router
from app.domains.posts.router import posts_router, stream_posts_router
from app.domains.accounts.router import router as accounts_router
from app.domains.journal.router import (
    analytics_router as journal_analytics_router,
    daily_router as journal_daily_router,
    messages_router as journal_messages_router,
    templates_router as journal_templates_router,
    trades_router as journal_trades_router,
)
from app.domains.journal.router_tags import router as journal_tags_router
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.domains.journal import service as journal_service

configure_logging(debug=settings.DEBUG)

app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from app.domains.journal.repository_tags import seed_system_tags


@app.on_event("startup")
def run_startup_tasks() -> None:
    if settings.AUTO_SEED_ON_STARTUP:
        with SessionLocal() as db:
            journal_service.seed_system_journal_templates(db)
            seed_system_tags(db)
            db.commit()


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
app.include_router(journal_trades_router)
app.include_router(journal_daily_router)
app.include_router(journal_messages_router)
app.include_router(journal_templates_router)
app.include_router(journal_analytics_router)
app.include_router(journal_tags_router)
