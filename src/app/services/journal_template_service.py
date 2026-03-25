import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.journal_template import JournalTemplateType
from app.models.user import User
from app.repositories import journal_template_repo
from app.schemas.journal_template import JournalTemplateCreateRequest


def list_templates(
    db: Session,
    *,
    current_user: User,
    template_type: JournalTemplateType | None,
):
    return journal_template_repo.list_visible_to_user(
        db,
        user_id=current_user.id,
        template_type=template_type,
    )


def create_template(
    db: Session,
    *,
    current_user: User,
    payload: JournalTemplateCreateRequest,
):
    template = journal_template_repo.create(
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
    template = journal_template_repo.get_by_id_visible_to_user(
        db,
        template_id=template_id,
        user_id=current_user.id,
    )
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found.")

    deleted = journal_template_repo.delete_user_template(db, template=template, user_id=current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete this template.")

    db.commit()
