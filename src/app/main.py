from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth as auth_router
from app.api.routes import streams as streams_router
from app.api.routes import uploads as uploads_router
from app.api.routes import users as users_router
from app.api.routes import journal_trades as journal_trades_router
from app.api.routes import journal_accounts as journal_accounts_router
from app.api.routes import journal_daily as journal_daily_router
from app.api.routes import journal_messages as journal_messages_router
from app.api.routes import journal_templates as journal_templates_router
from app.api.routes import journal_analytics as journal_analytics_router
from app.api.routes.posts import posts_router, stream_posts_router
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.services import startup_service

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


@app.on_event("startup")
def run_startup_tasks() -> None:
    if settings.AUTO_SEED_ON_STARTUP:
        with SessionLocal() as db:
            startup_service.seed_system_journal_templates(db)


@app.get("/")
def health_check():
    return {"status": "ok", "app": settings.APP_NAME}


# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(streams_router.router)
app.include_router(uploads_router.router)
app.include_router(stream_posts_router)
app.include_router(posts_router)
app.include_router(journal_trades_router.router)
app.include_router(journal_accounts_router.router)
app.include_router(journal_daily_router.router)
app.include_router(journal_messages_router.router)
app.include_router(journal_templates_router.router)
app.include_router(journal_analytics_router.router)