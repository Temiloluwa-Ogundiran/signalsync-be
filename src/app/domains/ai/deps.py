"""AI-specific FastAPI dependencies."""
import uuid
from typing import List

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.domains.ai import repository as ai_repo
from app.domains.users.models import User
from app.shared.deps import get_current_user


def get_current_user_id(user: User = Depends(get_current_user)) -> uuid.UUID:
    return user.id


def get_account_ids(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> List[str]:
    """Resolve all active trading account IDs for the authenticated user."""
    return ai_repo.get_account_ids_for_user(db, user_id=user.id)
