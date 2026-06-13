from typing import Annotated, List, Optional

from langchain_core.tools import tool
from app.domains.ai.cache import tool_cache

from app.core.database import SessionLocal
from app.domains.ai import repository as repo


@tool
@tool_cache()
def find_tagged_trades(
    question: Annotated[str, "The trader's question"],
    account_ids: Annotated[List[str], "List of account UUIDs to scope the query to"],
    from_date: Annotated[Optional[str], "Start date YYYY-MM-DD"] = None,
    to_date: Annotated[Optional[str], "End date YYYY-MM-DD (inclusive)"] = None,
) -> str:
    """Analyse performance broken down by trade journal tags/setups.
    Use for: 'which setups are making money', 'what tags perform best',
    'which setup has the best win rate', 'tag performance', 'are my A+ setups profitable'."""
    date_filter = ""
    extra: dict = {}
    if from_date:
        date_filter += " AND t.closed_at >= :from_date"
        extra["from_date"] = from_date
    if to_date:
        date_filter += " AND t.closed_at <= :to_date"
        extra["to_date"] = to_date

    with SessionLocal() as db:
        rows = repo.analytics_tagged_trades(db, account_ids, date_filter, extra)

    if not rows:
        return "No tagged trades found for the given accounts and date range."

    lines = ["=== PERFORMANCE BY TAG / SETUP ==="]
    for r in rows:
        pf = (
            round(float(r.gross_win) / float(r.gross_loss), 2)
            if r.gross_loss and float(r.gross_loss) > 0
            else "∞"
        )
        label = f"{r.category}: {r.tag}" if r.category else r.tag
        lines.append(
            f"{label} — {r.trades} trades | {r.win_rate}% WR | "
            f"P&L {r.total_pnl} | avg {r.avg_pnl} | PF {pf}"
        )
    return "\n".join(lines)
