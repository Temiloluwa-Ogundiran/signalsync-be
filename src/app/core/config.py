import json
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "SyncTrades API"
    DEBUG: bool = False
    # Emit structured JSON logs (recommended in production for log aggregation).
    LOG_JSON: bool = True


    DATABASE_URL: str

    # -------------------------------------------------------------------
    # Database connection pool (per worker process).
    # Total cluster connections = web/worker processes * (POOL_SIZE + MAX_OVERFLOW).
    # Keep that product below Postgres max_connections (front with PgBouncer at scale).
    # Behind a PgBouncer transaction pooler the app pool only gates per-worker
    # concurrency (not real Postgres connections), so a small pool is correct.
    # -------------------------------------------------------------------
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 300

    # Max accepted upload size (bytes) for CSV/XLSX import. Default 25 MiB.
    MAX_UPLOAD_BYTES: int = 25 * 1024 * 1024

    # -------------------------------------------------------------------
    # Rate limiting (per client IP). RATE_LIMIT_DEFAULT is a global safety net;
    # the upload/sync limits are tighter caps on expensive endpoints.
    # Behind a proxy, set FORWARDED_ALLOW_IPS so the real client IP is used.
    # -------------------------------------------------------------------
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT: str = "300/minute"
    RATE_LIMIT_UPLOADS: str = "20/minute"
    RATE_LIMIT_SYNC: str = "12/minute"
    RATE_LIMIT_REDIS_URL: str = "redis://localhost:6379/2"

    # -------------------------------------------------------------------
    # -------------------------------------------------------------------
    # Object storage — AWS S3 (direct, via boto3). Private bucket; reads are
    # served via short-lived presigned GET URLs.
    # -------------------------------------------------------------------
    AWS_S3_BUCKET: str = ""
    AWS_S3_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    # Optional: custom/S3-compatible endpoint (leave blank for real AWS S3).
    AWS_S3_ENDPOINT_URL: str = ""

    # Auth
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    # Rolling 30-day window: each refresh rotates this forward, so an active
    # user effectively never has to sign in again (a journal should be sticky).
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    PASSWORD_RESET_EXPIRY_MINUTES: int = 15
    EMAIL_VERIFY_EXPIRY_HOURS: int = 24

    # App
    IS_PRODUCTION: bool = False
    # Canonical frontend URL used for email/deep links.
    FRONTEND_URL: str = "http://localhost:3000"
    # Google Sign-In: the OAuth client ID Google ID tokens are issued for. Used
    # as the audience when verifying tokens server-side. Empty disables it.
    GOOGLE_CLIENT_ID: str = ""
    # CORS origins as raw env string; parsed via get_cors_allowed_origins().
    # Supports CSV: "http://localhost:3000,https://app.example.com"
    # Supports JSON array: "[\"http://localhost:3000\",\"https://app.example.com\"]"
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000"
    BACKEND_URL: str = "http://localhost:8000"
    PORT: int = 8000
    # Seeding runs on app startup; keep off in production and seed via an explicit
    # one-shot command so it doesn't run on every worker/replica boot.
    AUTO_SEED_ON_STARTUP: bool = False

    # Mobile deep link scheme (e.g. "synctrades://")
    DEEP_LINK_SCHEME: Optional[str] = None

    # Email
    RESEND_API_KEY: str = ""
    EMAIL_FROM: str = ""
    EMAIL_FROM_NAME: str = "TradePartna"

    # S3 key prefixes per media kind (folders within the one bucket).
    S3_PREFIX_USER_AVATARS: str = "user-avatars"
    S3_PREFIX_JOURNAL_VOICE: str = "journal-voice-notes"
    S3_PREFIX_JOURNAL_IMAGES: str = "journal-images"
    # How long generated presigned GET URLs remain valid (seconds).
    MEDIA_SIGNED_URL_TTL_SECONDS: int = 3600  # 1 hour
    VOICE_SIGNED_URL_TTL_SECONDS: int = 3600
    IMAGE_SIGNED_URL_TTL_SECONDS: int = 3600

    INITIAL_SYNC_LOOKBACK_DAYS: int = 30

    # Encryption key for broker credential fields (Fernet base64 key)
    ENCRYPTION_KEY: str = ""

    # Sync (on-demand only; there is no scheduled/periodic sync)
    USER_ACTIVITY_TOUCH_MIN_INTERVAL_SECONDS: int = 60
    MANUAL_SYNC_COOLDOWN_SECONDS: int = 300
    MANUAL_SYNC_BURST_WINDOW_SECONDS: int = 60
    MANUAL_SYNC_BURST_MAX_ATTEMPTS: int = 5



    # DATABASE_URL_DIRECT: bypasses PgBouncer — used by Alembic and the LangGraph
    # checkpointer (which needs session-scoped features incompatible with transaction pooling).
    # Falls back to DATABASE_URL in local/dev setups without a pooler.
    DATABASE_URL_DIRECT: Optional[str] = None

    # -------------------------------------------------------------------
    # AI copilot (Partna AI)
    # -------------------------------------------------------------------
    OPENAI_API_KEY: str = ""
    AI_MODEL: str = "gpt-4.1-mini"
    AI_TEMPERATURE: float = 0.0
    AI_ENABLED: bool = True
    AI_REDIS_URL: str = "redis://localhost:6379/3"
    # Where the FE proxy routes ai/* requests. Empty => in-process (same app).
    AI_SERVICE_URL: str = ""
    RATE_LIMIT_AI: str = "12/minute"
    AI_CREDITS_FREE: int = 50
    AI_CREDITS_ESSENTIAL: int = 500
    AI_CREDITS_PRO: int = 1000

    # mt5-core service config
    MT5_CORE_URL: str = ""
    MT5_CORE_INTERNAL_SHARED_SECRET: str = ""
    MT5_CORE_POLL_TIMEOUT_SECONDS: int = 120
    MT5_CORE_POLL_INTERVAL_SECONDS: int = 2
    MT5_CORE_VERIFY_TIMEOUT_SECONDS: int = 150
    MT5_CORE_BOOTSTRAP_SYNC_TIMEOUT_SECONDS: int = 20

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def get_cors_allowed_origins(self) -> list[str]:
        raw = (self.CORS_ALLOWED_ORIGINS or "").strip()
        if not raw:
            return [self.FRONTEND_URL]

        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("CORS_ALLOWED_ORIGINS JSON is invalid") from exc

            if not isinstance(parsed, list):
                raise ValueError("CORS_ALLOWED_ORIGINS JSON must be an array")

            origins = [str(item).strip() for item in parsed if str(item).strip()]
            return origins or [self.FRONTEND_URL]

        origins = [item.strip() for item in raw.split(",") if item.strip()]
        return origins or [self.FRONTEND_URL]


settings = Settings()
