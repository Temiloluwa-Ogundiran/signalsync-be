from typing import Annotated, List, Optional

from langchain_core.tools import tool
from app.domains.ai.cache import tool_cache

from app.core.database import SessionLocal
from app.domains.ai import repository as repo


@tool
@tool_cache()
def get_trade_notes(
    question: Annotated[str, "The trader's question"],
    account_ids: Annotated[List[str], "List of account UUIDs to scope the query to"],
    trade_id: Annotated[Optional[str], "Specific trade UUID to fetch notes for"] = None,
    symbol: Annotated[Optional[str], "Filter by symbol e.g. 'EURUSD'"] = None,
    from_date: Annotated[Optional[str], "Start date YYYY-MM-DD"] = None,
    to_date: Annotated[Optional[str], "End date YYYY-MM-DD (inclusive)"] = None,
    keyword: Annotated[Optional[str], "Search within trade note content"] = None,
) -> str:
    """Fetch journal notes written on specific trades.
    Use for: 'what did I write about this trade', 'show trade notes for XAUUSD',
    'find notes on my losing EURUSD trades', 'show me the note on trade X'."""
    filters = "WHERE t.account_id = ANY(:aids_placeholder)"
    extra: dict = {}

    if trade_id:
        filters += " AND t.id = :trade_id"
        extra["trade_id"] = trade_id
    if symbol:
        filters += " AND UPPER(t.symbol) = :symbol"
        extra["symbol"] = symbol.upper()
    if from_date:
        filters += " AND t.closed_at >= :from_date"
        extra["from_date"] = from_date
    if to_date:
        filters += " AND t.closed_at <= :to_date"
        extra["to_date"] = to_date
    if keyword:
        filters += " AND jm.content ILIKE :keyword"
        extra["keyword"] = f"%{keyword}%"

    with SessionLocal() as db:
        rows = repo.analytics_trade_notes(db, account_ids, filters, extra)

    if not rows:
        return "No trade journal notes found matching those filters."

    lines = ["=== TRADE NOTES ==="]
    current_trade = None
    for r in rows:
        if r.trade_id != current_trade:
            current_trade = r.trade_id
            outcome = "WIN" if float(r.net_profit) > 0 else "LOSS"
            lines.append(
                f"\n[{outcome}] {r.symbol} {r.direction.upper()} {r.volume}lot | "
                f"P&L {r.net_profit} | closed {str(r.closed_at)[:16]} | id:{r.trade_id}"
            )
        tags_str = f" [tags: {', '.join(r.tags)}]" if r.tags else ""
        content = (r.content or "").strip()
        if len(content) > 500:
            content = content[:500] + "…"
        lines.append(f"  → [{r.message_type}]{tags_str} {content}")
    return "\n".join(lines)
