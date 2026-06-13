from typing import Annotated, List, Optional

from langchain_core.tools import tool
from app.domains.ai.cache import tool_cache

from app.core.database import SessionLocal
from app.domains.ai import repository as repo


@tool
@tool_cache()
def find_trades(
    question: Annotated[str, "The trader's question"],
    account_ids: Annotated[List[str], "List of account UUIDs to scope the query to"],
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
        hold_min = round(r.duration_seconds / 60, 1)
        sl_str = f"SL {r.stop_loss}" if r.stop_loss is not None else "SL —"
        tp_str = f"TP {r.take_profit}" if r.take_profit is not None else "TP —"
        pips_str = f"{r.pips}pips" if r.pips is not None else ""
        pct_str = f"{r.percent_gain}%" if r.percent_gain is not None else ""
        lines.append(
            f"[{r.result.upper()}] {r.symbol} {r.direction.upper()} {r.volume}lot | "
            f"P&L {r.net_profit} {pips_str} {pct_str} | {sl_str} | {tp_str} | "
            f"{r.session} | hold {hold_min}min | "
            f"open {r.open_price} → close {r.close_price} | "
            f"closed {str(r.closed_at)[:16]} | id:{r.id}"
        )
    return "\n".join(lines)
