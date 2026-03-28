import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class JournalTemplateType(str, enum.Enum):
    daily = "daily"
    trade = "trade"


class JournalTemplate(Base):
    __tablename__ = "journal_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)

    template_type: Mapped[JournalTemplateType] = mapped_column(
        Enum(
            JournalTemplateType,
            values_callable=lambda x: [e.value for e in x],
            name="journaltemplatetypeenum",
        ),
        nullable=False,
    )

    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    questions: Mapped[list] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    owner: Mapped[Optional["User"]] = relationship(back_populates="journal_templates")  # noqa: F821
