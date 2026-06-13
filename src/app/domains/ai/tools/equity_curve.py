from typing import Annotated, List, Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from app.domains.ai.cache import tool_cache
from app.domains.ai.tools.scope import enforce_account_scope

from app.core.database import SessionLocal
from app.domains.ai import repository as repo


@tool
@tool_cache()
def get_equity_curve(
    question: Annotated[str, "The trader's question"],
    account_ids: Annotated[List[str], "List of account UUIDs to scope the query to"],
    config: RunnableConfig,
    group_by: Annotated[str, "Granularity: 'day' (default), 'week', or 'month'"] = "day",
    from_date: Annotated[Optional[str], "Optional start date YYYY-MM-DD"] = None,
    to_date: Annotated[Optional[str], "Optional end date YYYY-MM-DD (inclusive)"] = None,
) -> str:
    """Get cumulative P&L over time grouped by day, week, or month.
    Use for: 'show my equity curve', 'P&L over time', 'weekly P&L', 'monthly P&L',
    'am I trending up or down', 'how has my balance grown'."""
    account_ids = enforce_account_scope(account_ids, config)
    date_filter = ""
    extra: dict = {}
    if from_date:
        date_filter += " AND closed_at >= :from_date"
        extra["from_date"] = from_date
    if to_date:
        date_filter += " AND closed_at <= :to_date"
        extra["to_date"] = to_date

    gb = group_by.lower().strip()
    if gb == "week":
        period_expr = "DATE_TRUNC('week', closed_at)::date"
        label = "Week of"
    elif gb == "month":
        period_expr = "DATE_TRUNC('month', closed_at)::date"
        label = "Month"
    else:
        period_expr = "DATE(closed_at)"
        label = "Day"

    with SessionLocal() as db:
        rows = repo.analytics_equity_curve(db, account_ids, period_expr, date_filter, extra)

    if not rows:
        return "No trades found for the given filters."

    lines = [f"=== P&L BY {gb.upper()} (equity curve) ==="]
    cumulative = 0.0
    for r in rows:
        cumulative += float(r.period_pnl)
        sign = "+" if float(r.period_pnl) >= 0 else ""
        lines.append(
            f"{label} {r.period}  |  {sign}{r.period_pnl} this {gb}  |  "
            f"cumulative {round(cumulative, 2)}  |  {r.trades} trades ({r.wins}W/{r.losses}L)"
        )
    lines.append(f"\nTotal P&L: {round(cumulative, 2)}")
    lines.append(f"Trading {gb}s: {len(rows)}")
    return "\n".join(lines)
