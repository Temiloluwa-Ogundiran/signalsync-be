from typing import Annotated, List, Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from app.domains.ai.cache import tool_cache
from app.domains.ai.tools.scope import enforce_account_scope

from app.core.database import SessionLocal
from app.domains.ai import repository as repo


@tool
@tool_cache()
def find_tagged_trades(
    question: Annotated[str, "The trader's question"],
    account_ids: Annotated[List[str], "List of account UUIDs to scope the query to"],
    config: RunnableConfig,
    from_date: Annotated[Optional[str], "Start date YYYY-MM-DD"] = None,
    to_date: Annotated[Optional[str], "End date YYYY-MM-DD (inclusive)"] = None,
) -> str:
    """Analyse performance broken down by trade journal SETUPS and TAGS, returned
    as two SEPARATE sections so they are never conflated.

    - SETUPS are the trader's named strategies/playbooks (e.g. 'trend pullback').
      This is what 'which setup is most profitable' means.
    - TAGS are descriptive labels grouped by category (e.g. Mental: 'Hectic',
      Indicator: 'RSI OS'). These describe a trade's context, NOT a strategy —
      a profitable tag is a correlation, not a setup the trader chose.

    Use for: 'which setups are making money', 'what tags perform best',
    'which setup has the best win rate', 'are my A+ setups profitable'."""
    account_ids = enforce_account_scope(account_ids, config)
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
        return "No tagged trades or setups found for the given accounts and date range."

    def _fmt(r) -> str:
        pf = (
            round(float(r.gross_win) / float(r.gross_loss), 2)
            if r.gross_loss and float(r.gross_loss) > 0
            else "∞"
        )
        return (
            f"{r.tag} — {r.trades} trades | {r.win_rate}% WR | "
            f"P&L {r.total_pnl} | avg {r.avg_pnl} | PF {pf}"
        )

    # Split setups (category == 'Setup') from descriptive tags so the model never
    # reports a Mental/Indicator tag as a profitable "setup".
    setups = [r for r in rows if (r.category or "").strip().lower() == "setup"]
    tags = [r for r in rows if (r.category or "").strip().lower() != "setup"]

    out: list[str] = []
    if setups:
        out.append("=== PERFORMANCE BY SETUP (named strategies) ===")
        out.extend(_fmt(r) for r in setups)
    else:
        out.append("=== PERFORMANCE BY SETUP ===\nNo named setups assigned to trades yet.")

    if tags:
        # Group descriptive tags under their category for clarity.
        out.append("")
        out.append("=== CONTEXT TAGS (descriptive — NOT setups; correlation only) ===")
        current_cat = None
        for r in sorted(tags, key=lambda r: ((r.category or ""), -float(r.total_pnl))):
            cat = r.category or "Other"
            if cat != current_cat:
                out.append(f"-- {cat} --")
                current_cat = cat
            out.append(_fmt(r))

    return "\n".join(out)
