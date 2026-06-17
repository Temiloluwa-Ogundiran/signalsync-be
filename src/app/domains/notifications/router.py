import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.shared.deps import get_current_user
from app.domains.users.models import User
from app.domains.notifications import service as notif_service
from app.domains.notifications.schemas import (
    NotificationListResponse,
    NotificationResponse,
    UnreadCountResponse,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationListResponse:
    data = notif_service.list_notifications(db, user=current_user, limit=min(limit, 100))
    return NotificationListResponse(
        items=[NotificationResponse.model_validate(n) for n in data["items"]],
        unread_count=data["unread_count"],
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
def unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UnreadCountResponse:
    return UnreadCountResponse(
        unread_count=notif_service.get_unread_count(db, user=current_user)
    )


@router.put("/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    notif_service.mark_all_read(db, user=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(
    notification_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    notif_service.mark_read(db, user=current_user, notification_id=notification_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
