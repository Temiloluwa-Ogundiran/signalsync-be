import json
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "SyncTrades API"
    DEBUG: bool = False

    
    DATABASE_URL: str

    # -------------------------------------------------------------------
    # Supabase
    # -------------------------------------------------------------------
    SUPABASE_URL: str
    SUPABASE_SERVICE_KEY: str

    # Auth
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    PASSWORD_RESET_EXPIRY_MINUTES: int = 15
    EMAIL_VERIFY_EXPIRY_HOURS: int = 24

    # App
    IS_PRODUCTION: bool = False
    # Canonical frontend URL used for email/deep links.
    FRONTEND_URL: str = "http://localhost:3000"
    # CORS origins as raw env string; parsed via get_cors_allowed_origins().
    # Supports CSV: "http://localhost:3000,https://app.example.com"
    # Supports JSON array: "[\"http://localhost:3000\",\"https://app.example.com\"]"
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000"
    BACKEND_URL: str = "http://localhost:8000"
    PORT: int = 8000
    AUTO_SEED_ON_STARTUP: bool = True

    # Mobile deep link scheme (e.g. "synctrades://")
    DEEP_LINK_SCHEME: Optional[str] = None

    # SMTP Email
    SMTP_SERVER: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    EMAIL_FROM: Optional[str] = None
    EMAIL_FROM_NAME: str = "SyncTrades"

    # Supabase Storage — bucket names
    SUPABASE_STREAM_AVATARS_BUCKET: str = "stream-avatars"
    SUPABASE_STREAM_BANNERS_BUCKET: str = "stream-banners"
    SUPABASE_POST_MEDIA_BUCKET: str = "post-media"
    JOURNAL_VOICE_BUCKET: str = "journal-voice-notes"
    JOURNAL_IMAGES_BUCKET: str = "journal-images"
    # How long generated signed URLs for post media remain valid (seconds).
    MEDIA_SIGNED_URL_TTL_SECONDS: int = 3600  # 1 hour
    VOICE_SIGNED_URL_TTL_SECONDS: int = 3600
    IMAGE_SIGNED_URL_TTL_SECONDS: int = 3600

    # MetaAPI
    METAAPI_TOKEN: str = ""
    METAAPI_VERIFY_SSL: bool = True
    METAAPI_PROVISIONING_BASE_URL: str = "https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai"
    METAAPI_CLIENT_BASE_URL: str = "https://mt-client-api-v1.london.agiliumtrade.ai"
    METAAPI_ACCOUNT_ENGINE: str = "cloud-g2"
    METAAPI_ACCOUNT_MAGIC: int = 10001
    METAAPI_DEALS_TIMEOUT_SECONDS: float = 45.0
    METAAPI_DEALS_MAX_RETRIES: int = 3
    METAAPI_DEALS_RETRY_BACKOFF_SECONDS: float = 1.5
    METAAPI_DEALS_CHUNK_DAYS: int = 7
    INITIAL_SYNC_LOOKBACK_DAYS: int = 14

    # Encryption key for broker credential fields (Fernet base64 key)
    ENCRYPTION_KEY: str = ""

    # Sync
    SYNC_INTERVAL_MINUTES: int = 5

    # Headless MT5 microservice
    MT5_SERVICE_URL: str = ""
    # Shared secret for authenticating requests to/from the headless MT5 service.
    # Must match SHARED_SECRET in the headless-mt5-service .env.
    MT5_SERVICE_SHARED_SECRET: str = ""

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
