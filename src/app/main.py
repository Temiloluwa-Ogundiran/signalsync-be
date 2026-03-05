from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth as auth_router
from app.api.routes import streams as streams_router
from app.api.routes import users as users_router
from app.core.config import settings
from app.core.logging import configure_logging

configure_logging(debug=settings.DEBUG)

app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def health_check():
    return {"status": "ok", "app": settings.APP_NAME}


# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(streams_router.router)