"""
Chat-session auto-titling.

Generates a short, human title for a session from its first user message using
a cheap model. Best-effort: any failure returns None and the caller keeps the
placeholder title (the truncated first message), so titling never breaks chat.
"""
import logging
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings

logger = logging.getLogger(__name__)

_TITLE_SYSTEM = (
    "You name a chat thread. Given the user's first message, reply with a short, "
    "specific title of at most 6 words. No quotes, no trailing punctuation, no "
    "emoji. Title case. If the message is empty or unclear, reply 'New chat'."
)

_llm: Optional[ChatOpenAI] = None


def _get_llm() -> Optional[ChatOpenAI]:
    global _llm
    if not settings.OPENAI_API_KEY:
        return None
    if _llm is None:
        _llm = ChatOpenAI(
            model=settings.AI_TITLE_MODEL,
            temperature=0.0,
            max_tokens=20,
            openai_api_key=settings.OPENAI_API_KEY,
        )
    return _llm


def generate_title(first_message: str) -> Optional[str]:
    """Return a short title for the thread, or None on any failure."""
    text = (first_message or "").strip()
    if not text:
        return None

    llm = _get_llm()
    if llm is None:
        return None

    try:
        resp = llm.invoke(
            [
                SystemMessage(content=_TITLE_SYSTEM),
                HumanMessage(content=text[:1000]),
            ]
        )
        title = (resp.content or "").strip().strip('"').strip()
        # Guard against an over-long or empty response.
        if not title or len(title) > 80:
            return title[:80] or None
        return title
    except Exception:
        logger.exception("ai title generation failed")
        return None
