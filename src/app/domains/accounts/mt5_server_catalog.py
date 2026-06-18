import re
import uuid

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin


_SERVER_KEY_PATTERN = re.compile(r"[^a-z0-9]+")


def normalize_mt5_server_key(value: str) -> str:
    """Build a forgiving lookup key for MT5 server-name formatting variants."""
    return _SERVER_KEY_PATTERN.sub("", value.strip().lower())


class Mt5ServerCatalog(Base, TimestampMixin):
    __tablename__ = "mt5_server_catalog"
    __table_args__ = (
        UniqueConstraint("canonical_server_name", name="uq_mt5_server_catalog_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    canonical_server_name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_server_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="csv")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
