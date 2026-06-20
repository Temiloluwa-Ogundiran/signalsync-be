from typing import Annotated, List, Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from app.domains.ai.cache import tool_cache
from app.domains.ai.tools.scope import enforce_account_scope

from app.core.database import SessionLocal
from app.domains.ai import repository as repo


@tool
@tool_cache()
def find_trades(
    question: Annotated[str, "The trader's question"],
    account_ids: Annotated[List[str], "List of account UUIDs to scope the query to"],
    config: RunnableConfig,
    symbol: Annotated[Optional[str], "Filter by symbol e.g. 'EURUSD'"] = None,
    direction: Annotated[Optional[str], "Filter by 'buy' or 'sell'"] = None,
    result: Annotated[Optional[str], "Filter by 'win' or 'loss'"] = None,
    session: Annotated[Optional[str], "Filter by session: asian, london, new_york, london_ny_overlap, off_hours"] = None,
    from_date: Annotated[Optional[str], "Optional start date YYYY-MM-DD"] = None,
    to_date: Annotated[Optional[str], "Optional end date YYYY-MM-DD (inclusive)"] = None,
    sort_by: Annotated[str, "Sort order: 'worst', 'best', or 'recent'"] = "recent",
    limit: Annotated[int, "Max number of trades to return"] = 10,
) -> str:
    """Fetch and LIST individual trade rows. Use ONLY when the user wants to see actual trades.
    Use for: 'show my worst trades', 'best 10 trades', 'list my XAUUSD losses', 'last 5 trades'.
    Do NOT use for aggregate questions — use get_performance_metrics or get_breakdown for those."""
    account_ids = enforce_account_scope(account_ids, config)
    filters = "WHERE account_id = ANY(:aids_placeholder)"
    extra: dict = {"limit": limit}

    if symbol:
        filters += " AND UPPER(symbol) = :symbol"
        extra["symbol"] = symbol.upper()
    if direction and direction.lower() in ("buy", "sell"):
        filters += " AND direction = :direction"
        extra["direction"] = direction.lower()
    if result == "win":
        filters += " AND net_profit > 0"
    elif result == "loss":
        filters += " AND net_profit < 0"
    if session:
        filters += " AND session = :session"
        extra["session"] = session.lower()
    if from_date:
        filters += " AND closed_at >= :from_date"
        extra["from_date"] = from_date
    if to_date:
        filters += " AND closed_at <= :to_date"
        extra["to_date"] = to_date

    order = {"worst": "net_profit ASC", "best": "net_profit DESC", "recent": "closed_at DESC"}.get(
        sort_by.lower(), "closed_at DESC"
    )

    with SessionLocal() as db:
        rows = repo.analytics_find_trades(db, account_ids, filters, order, extra)

    if not rows:
        return "No trades found matching those filters."

    lines = [f"=== TRADES ({len(rows)} results, sorted by {sort_by}) ==="]
    for r in rows:
        hold_min = round((r.duration_seconds or 0) / 60, 1)
        # Win/loss is derived from net_profit — there's no stored 'result' column.
        outcome = "WIN" if float(r.net_profit) > 0 else "LOSS"
        sl_str = f"SL {r.sl}" if r.sl is not None else "SL —"
        tp_str = f"TP {r.tp}" if r.tp is not None else "TP —"
        setup_str = f" | setup: {r.setup}" if r.setup else ""
        lines.append(
            f"[{outcome}] {r.symbol} {r.direction.upper()} {r.volume}lot | "
            f"P&L {r.net_profit} | {sl_str} | {tp_str} | "
            f"{r.session} | hold {hold_min}min | "
            f"open {r.open_price} → close {r.close_price}{setup_str} | "
            f"closed {str(r.closed_at)[:16]} | id:{r.id}"
        )
    return "\n".join(lines)
