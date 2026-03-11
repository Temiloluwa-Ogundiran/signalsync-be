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
    FRONTEND_URL: str = "http://localhost:3000"
    BACKEND_URL: str = "http://localhost:8000"
    PORT: int = 8000

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
    # How long generated signed URLs for post media remain valid (seconds).
    MEDIA_SIGNED_URL_TTL_SECONDS: int = 3600  # 1 hour

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
