from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy import Enum as PgEnum
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, MappedColumn, Session, mapped_column, sessionmaker
from sqlalchemy.pool import QueuePool

from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    poolclass=QueuePool,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_pre_ping=True,
    pool_recycle=settings.DB_POOL_RECYCLE,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    """Adds created_at and updated_at columns to any model."""

    created_at: MappedColumn = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: MappedColumn = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class SoftDeleteMixin:
    """Adds soft-delete fields. Actual filtering is enforced at the service layer."""

    is_deleted: MappedColumn = mapped_column(
        default=False,
        nullable=False,
    )
    deleted_at: MappedColumn = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )


def as_pg_enum(enum_cls, **kwargs):
    """Helper to create a PostgreSQL-native Enum that uses .value strings."""
    return PgEnum(enum_cls, values_callable=lambda x: [e.value for e in x], **kwargs)


def get_db():
    """FastAPI dependency that yields a database session."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
