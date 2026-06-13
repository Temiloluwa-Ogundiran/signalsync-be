import uuid
from datetime import datetime
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.domains.ai import repository as repo
from app.domains.ai.models import AiChatSession
from app.domains.ai.schemas import SessionListResponse, SessionResponse


def _ensure_account_scope(
    db: Session,
    *,
    user_id: uuid.UUID,
    account_id: Optional[uuid.UUID],
) -> None:
    if account_id is None:
        return

    allowed_ids = {account["id"] for account in repo.get_accounts_for_user(db, user_id=user_id)}
    if str(account_id) not in allowed_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")


def create_session(
    db: Session,
    *,
    user_id: uuid.UUID,
    title: Optional[str] = None,
    context_type: str = "general",
    context_ref: Optional[str] = None,
    account_id: Optional[uuid.UUID] = None,
) -> AiChatSession:
    _ensure_account_scope(db, user_id=user_id, account_id=account_id)
    session = repo.create_session(
        db,
        user_id=user_id,
        title=title,
        context_type=context_type,
        context_ref=context_ref,
        account_id=account_id,
    )
    db.commit()
    return session


def get_session(
    db: Session,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> AiChatSession:
    session = repo.get_session(db, session_id=session_id, user_id=user_id)
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    return session


def list_sessions(
    db: Session,
    *,
    user_id: uuid.UUID,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> Tuple[List[AiChatSession], Optional[str]]:
    cursor_last_message_at: Optional[datetime] = None
    cursor_id: Optional[uuid.UUID] = None

    if cursor:
        try:
            # cursor = "<iso_datetime>|<uuid>"
            parts = cursor.split("|", 1)
            cursor_last_message_at = datetime.fromisoformat(parts[0]) if parts[0] else None
            cursor_id = uuid.UUID(parts[1]) if len(parts) > 1 and parts[1] else None
        except (ValueError, IndexError):
            pass

    items = repo.list_sessions(
        db,
        user_id=user_id,
        limit=limit + 1,
        cursor_last_message_at=cursor_last_message_at,
        cursor_id=cursor_id,
    )

    next_cursor = None
    if len(items) > limit:
        items = items[:limit]
        last = items[-1]
        lma = last.last_message_at.isoformat() if last.last_message_at else ""
        next_cursor = f"{lma}|{last.id}"

    return items, next_cursor


def delete_session(
    db: Session,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    session = get_session(db, session_id=session_id, user_id=user_id)
    repo.soft_delete_session(db, session=session)
    db.commit()


def get_or_create_context_session(
    db: Session,
    *,
    user_id: uuid.UUID,
    context_type: str,
    context_ref: str,
    account_id: Optional[uuid.UUID] = None,
) -> AiChatSession:
    _ensure_account_scope(db, user_id=user_id, account_id=account_id)
    session = repo.get_context_session(
        db,
        user_id=user_id,
        context_type=context_type,
        context_ref=context_ref,
        account_id=account_id,
    )
    if session is None:
        session = repo.create_session(
            db,
            user_id=user_id,
            context_type=context_type,
            context_ref=context_ref,
            account_id=account_id,
        )
        db.commit()
    return session
