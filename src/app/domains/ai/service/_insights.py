import uuid
from typing import List, Optional

from sqlalchemy.orm import Session

from app.domains.ai import repository as repo
from app.domains.ai.models import AiInsight


def get_insights(
    db: Session,
    *,
    user_id: uuid.UUID,
    account_id: Optional[uuid.UUID] = None,
    kind: Optional[str] = None,
) -> List[AiInsight]:
    return repo.get_insights(db, user_id=user_id, account_id=account_id, kind=kind)
