from typing import Annotated, List, Optional

from langchain_core.tools import tool
from app.domains.ai.cache import tool_cache

from app.core.database import SessionLocal
from app.domains.ai import repository as repo


@tool
@tool_cache()
def summarize_journal(
    question: Annotated[str, "The trader's question"],
    account_ids: Annotated[List[str], "List of account UUIDs to scope the query to"],
    from_date: Annotated[Optional[str], "Start date YYYY-MM-DD"] = None,
    to_date: Annotated[Optional[str], "End date YYYY-MM-DD (inclusive)"] = None,
) -> str:
    """Fetch all daily journal entries for a date range so you can summarise them.
    Use for: 'summarise my journal last week', 'what have I been writing about',
    'give me a recap of my notes this month', 'what themes come up in my journal'."""
    date_filter = ""
    extra: dict = {}
    if from_date:
        date_filter += " AND dj.trading_date >= :from_date"
        extra["from_date"] = from_date
    if to_date:
        date_filter += " AND dj.trading_date <= :to_date"
        extra["to_date"] = to_date

    with SessionLocal() as db:
        rows = repo.analytics_summarize_journal(db, account_ids, date_filter, extra)

    if not rows:
        return "No journal entries found for the given date range."

    lines = ["=== JOURNAL SUMMARY DATA ==="]
    current_date = None
    for r in rows:
        if r.trading_date != current_date:
            current_date = r.trading_date
            stats = ""
            if r.trade_count is not None:
                stats = f" | {r.trade_count} trades, {r.win_count}W/{r.loss_count}L, P&L {r.total_pnl}"
            lines.append(f"\n=== {r.trading_date}{stats} ===")
        tags_str = f" [tags: {', '.join(r.tags)}]" if r.tags else ""
        lines.append(f"{tags_str}{(r.content or '').strip()}")

    unique_days = len(set(r.trading_date for r in rows))
    lines.append(f"\n[{len(rows)} journal messages across {unique_days} days]")
    return "\n".join(lines)
