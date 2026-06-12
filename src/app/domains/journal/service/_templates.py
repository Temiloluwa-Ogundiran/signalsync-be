import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domains.journal import repository as journal_repo
from app.domains.journal.models import JournalTemplateType
from app.domains.journal.schemas import JournalTemplateCreateRequest
from app.domains.users.models import User


def list_templates(db: Session, *, current_user: User, template_type: JournalTemplateType | None):
    return journal_repo.list_templates_visible_to_user(
        db, user_id=current_user.id, template_type=template_type,
    )


def create_template(db: Session, *, current_user: User, payload: JournalTemplateCreateRequest):
    template = journal_repo.create_template(
        db,
        owner_id=current_user.id,
        name=payload.name,
        template_type=payload.template_type,
        questions=[q.model_dump() for q in payload.questions],
    )
    db.commit()
    db.refresh(template)
    return template


def delete_template(db: Session, *, current_user: User, template_id: uuid.UUID) -> None:
    template = journal_repo.get_template_visible_to_user(
        db, template_id=template_id, user_id=current_user.id,
    )
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found.")
    deleted = journal_repo.delete_user_template(db, template=template, user_id=current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete this template.")
    db.commit()


def seed_system_journal_templates(db: Session) -> dict[str, int]:
    created, skipped = journal_repo.seed_system_templates(db)
    if created:
        db.commit()
    return {"created": created, "skipped_existing": skipped}
