import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.journal_template import JournalTemplate, JournalTemplateType


DAILY_REFLECTION_TEMPLATE_ID = uuid.UUID("ee5ad00f-0de2-4ce9-a8f3-f89a0a8f1f3a")
POST_TRADE_REVIEW_TEMPLATE_ID = uuid.UUID("228eb5ba-30a0-4b36-95b2-7e82ff931f91")


def seed_system_templates(db: Session) -> tuple[int, int]:
    now = datetime.now(timezone.utc)

    templates = [
        {
            "id": DAILY_REFLECTION_TEMPLATE_ID,
            "name": "Daily reflection",
            "template_type": JournalTemplateType.daily,
            "is_system": True,
            "owner_id": None,
            "questions": [
                {"id": 1, "order": 1, "text": "How did you feel going into today's session?"},
                {"id": 2, "order": 2, "text": "Did you stick to your trading plan today?"},
                {"id": 3, "order": 3, "text": "What was your best decision today?"},
                {"id": 4, "order": 4, "text": "What would you do differently tomorrow?"},
                {"id": 5, "order": 5, "text": "Rate your discipline today from 1-10."},
            ],
            "created_at": now,
        },
        {
            "id": POST_TRADE_REVIEW_TEMPLATE_ID,
            "name": "Post-trade review",
            "template_type": JournalTemplateType.trade,
            "is_system": True,
            "owner_id": None,
            "questions": [
                {"id": 1, "order": 1, "text": "What was your entry reason for this trade?"},
                {"id": 2, "order": 2, "text": "Did you follow your entry rules exactly?"},
                {"id": 3, "order": 3, "text": "How did you manage the trade (SL, TP, partials)?"},
                {"id": 4, "order": 4, "text": "What would you have done differently?"},
                {
                    "id": 5,
                    "order": 5,
                    "text": "Rate this trade: A (followed plan perfectly) / B (minor deviation) / C (broke rules)",
                },
            ],
            "created_at": now,
        },
    ]

    target_ids = [tpl["id"] for tpl in templates]
    existing_ids = set(
        db.scalars(
            select(JournalTemplate.id).where(JournalTemplate.id.in_(target_ids))
        ).all()
    )

    created_count = 0
    for template in templates:
        if template["id"] in existing_ids:
            continue
        db.add(JournalTemplate(**template))
        created_count += 1

    return created_count, len(templates) - created_count


def create(
    db: Session,
    *,
    owner_id: uuid.UUID,
    name: str,
    template_type: JournalTemplateType,
    questions: list,
) -> JournalTemplate:
    template = JournalTemplate(
        owner_id=owner_id,
        name=name,
        template_type=template_type,
        is_system=False,
        questions=questions,
    )
    db.add(template)
    db.flush()
    return template


def get_by_id_visible_to_user(
    db: Session,
    *,
    template_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Optional[JournalTemplate]:
    stmt = select(JournalTemplate).where(
        JournalTemplate.id == template_id,
        or_(JournalTemplate.is_system.is_(True), JournalTemplate.owner_id == user_id),
    )
    return db.execute(stmt).scalar_one_or_none()


def list_visible_to_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    template_type: Optional[JournalTemplateType] = None,
) -> list[JournalTemplate]:
    stmt = select(JournalTemplate).where(
        or_(JournalTemplate.is_system.is_(True), JournalTemplate.owner_id == user_id)
    )

    if template_type is not None:
        stmt = stmt.where(JournalTemplate.template_type == template_type)

    stmt = stmt.order_by(JournalTemplate.is_system.desc(), JournalTemplate.created_at.asc())
    return list(db.execute(stmt).scalars().all())


def delete_user_template(db: Session, *, template: JournalTemplate, user_id: uuid.UUID) -> bool:
    if template.is_system or template.owner_id != user_id:
        return False
    db.delete(template)
    db.flush()
    return True
