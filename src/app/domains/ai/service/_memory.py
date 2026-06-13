import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.domains.ai import repository as repo


def get_memory_block(db: Session, *, user_id: uuid.UUID) -> Optional[str]:
    """Return a compact system-prompt block from ai_user_memory, or None if empty."""
    memory = repo.get_user_memory(db, user_id=user_id)
    if not memory or not memory.profile:
        return None

    profile = memory.profile
    parts = []
    if profile.get("style"):
        parts.append(f"Trading style: {profile['style']}")
    if profile.get("goals"):
        parts.append("Goals: " + "; ".join(profile["goals"]))
    if profile.get("recurring_mistakes"):
        parts.append("Known mistakes: " + "; ".join(profile["recurring_mistakes"]))
    if profile.get("prefs"):
        parts.append(f"Preferences: {profile['prefs']}")

    if not parts:
        return None

    return "== WHAT YOU KNOW ABOUT THIS TRADER ==\n" + "\n".join(parts)
