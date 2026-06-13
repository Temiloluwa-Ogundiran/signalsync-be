from typing import Annotated, List, Optional

from langchain_core.tools import tool
from app.domains.ai.cache import tool_cache

from app.core.database import SessionLocal
from app.domains.ai import repository as repo


@tool
@tool_cache()
def search_daily_journal(
    question: Annotated[str, "The trader's question"],
    account_ids: Annotated[List[str], "List of account UUIDs to scope the query to"],
    keyword: Annotated[Optional[str], "Keyword to search in journal content (case-insensitive)"] = None,
    tag: Annotated[Optional[str], "Filter by a specific tag on the message"] = None,
    from_date: Annotated[Optional[str], "Start date YYYY-MM-DD"] = None,
    to_date: Annotated[Optional[str], "End date YYYY-MM-DD (inclusive)"] = None,
    limit: Annotated[int, "Max number of messages to return"] = 20,
) -> str:
    """Search daily journal messages by keyword, tag, or date range.
    Use for: 'find notes where I mentioned tilt', 'when did I write about FOMO',
    'search journal for revenge', 'find days I mentioned being emotional',
    'show me notes tagged discipline'."""
    filters = """
        WHERE dj.account_id = ANY(:aids_placeholder)
          AND jm.daily_journal_id IS NOT NULL
          AND jm.message_type IN ('text', 'prompt', 'ai_response')
    """
    extra: dict = {"limit": limit}

    if keyword:
        filters += " AND jm.content ILIKE :keyword"
        extra["keyword"] = f"%{keyword}%"
    if tag:
        filters += " AND :tag = ANY(jm.tags)"
        extra["tag"] = tag.lower()
    if from_date:
        filters += " AND dj.trading_date >= :from_date"
        extra["from_date"] = from_date
    if to_date:
        filters += " AND dj.trading_date <= :to_date"
        extra["to_date"] = to_date

    with SessionLocal() as db:
        rows = repo.analytics_search_journal(db, account_ids, filters, extra)

    if not rows:
        return "No journal entries found matching those filters."

    lines = [f"=== DAILY JOURNAL SEARCH ({len(rows)} results) ==="]
    current_date = None
    for r in rows:
        if r.trading_date != current_date:
            current_date = r.trading_date
            lines.append(f"\n--- {r.trading_date} ---")
        tags_str = f" [tags: {', '.join(r.tags)}]" if r.tags else ""
        content = (r.content or "").strip()
        if len(content) > 400:
            content = content[:400] + "…"
        lines.append(f"[{r.message_type}]{tags_str} {content}")
    return "\n".join(lines)
