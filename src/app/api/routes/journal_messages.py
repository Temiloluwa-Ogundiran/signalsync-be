import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.journal_message import JournalMessageResponse, JournalMessageUpdateRequest
from app.services import journal_service

router = APIRouter(prefix="/journal/messages", tags=["journal-messages"])


@router.patch("/{message_id}", response_model=JournalMessageResponse)
def update_journal_message(
    message_id: uuid.UUID,
    payload: JournalMessageUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JournalMessageResponse:
    message = journal_service.update_message(
        db,
        message_id=message_id,
        current_user=current_user,
        content=payload.content,
    )
    return JournalMessageResponse.model_validate(message)


@router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_journal_message(
    message_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    journal_service.delete_message(db, message_id=message_id, current_user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
