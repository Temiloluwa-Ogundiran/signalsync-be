import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.journal_template import JournalTemplateType
from app.models.user import User
from app.schemas.journal_template import JournalTemplateCreateRequest, JournalTemplateResponse
from app.services import journal_template_service

router = APIRouter(prefix="/journal/templates", tags=["journal-templates"])


@router.get("", response_model=list[JournalTemplateResponse])
def list_journal_templates(
    template_type: JournalTemplateType | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[JournalTemplateResponse]:
    templates = journal_template_service.list_templates(
        db,
        current_user=current_user,
        template_type=template_type,
    )
    return [JournalTemplateResponse.model_validate(t) for t in templates]


@router.post("", response_model=JournalTemplateResponse, status_code=status.HTTP_201_CREATED)
def create_journal_template(
    payload: JournalTemplateCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalTemplateResponse:
    template = journal_template_service.create_template(
        db,
        current_user=current_user,
        payload=payload,
    )
    return JournalTemplateResponse.model_validate(template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_journal_template(
    template_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    journal_template_service.delete_template(db, current_user=current_user, template_id=template_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
