from typing import Annotated, List, Optional

from langchain_core.tools import tool
from app.domains.ai.cache import tool_cache

from app.core.database import SessionLocal
from app.domains.ai import repository as repo


@tool
@tool_cache()
def get_breakdown(
    question: Annotated[str, "The trader's question"],
    account_ids: Annotated[List[str], "List of account UUIDs to scope the query to"],
    group_by: Annotated[str, "One of: symbol, direction, weekday, hour, session, hold_time_bucket, duration_scatter"] = "symbol",
    symbol: Annotated[Optional[str], "Filter to a specific symbol e.g. 'EURUSD'"] = None,
    from_date: Annotated[Optional[str], "Optional start date YYYY-MM-DD"] = None,
    to_date: Annotated[Optional[str], "Optional end date YYYY-MM-DD (inclusive)"] = None,
) -> str:
    """Break down performance by a specific dimension, optionally scoped to one symbol.
    Use for: 'which symbols make money', 'am I better long or short', 'what weekday am I worst',
    'what hour do I lose most', 'do short holds hurt me', 'compare sessions',
    'trade duration scatter', 'trade time performance'."""
    date_filter = ""
    extra: dict = {}
    if symbol:
        date_filter += " AND UPPER(symbol) = :symbol"
        extra["symbol"] = symbol.upper()
    if from_date:
        date_filter += " AND closed_at >= :from_date"
        extra["from_date"] = from_date
    if to_date:
        date_filter += " AND closed_at <= :to_date"
        extra["to_date"] = to_date

    gb = group_by.lower().strip()

    if gb == "duration_scatter":
        with SessionLocal() as db:
            rows = repo.analytics_breakdown_duration_scatter(db, account_ids, date_filter, extra)
        if not rows:
            return "No trades found for the given filters."
        lines = ["=== TRADE DURATION vs P&L (scatter data) ===",
                 "duration_seconds | net_profit | symbol | direction"]
        for r in rows:
            lines.append(f"{r.duration_seconds} | {r.net_profit} | {r.symbol} | {r.direction}")
        return "\n".join(lines)

    group_map = {
        "symbol": ("symbol", "Symbol"),
        "direction": ("direction", "Direction"),
        "weekday": ("TO_CHAR(closed_at, 'Day')", "Weekday"),
        "hour": ("EXTRACT(HOUR FROM closed_at)::int", "Hour (UTC)"),
        "session": ("session", "Session"),
        "hold_time_bucket": (
            "CASE WHEN duration_seconds < 300 THEN '0-5 min' "
            "WHEN duration_seconds < 900 THEN '5-15 min' "
            "WHEN duration_seconds < 3600 THEN '15-60 min' "
            "WHEN duration_seconds < 14400 THEN '1-4 hr' "
            "ELSE '4+ hr' END",
            "Hold time",
        ),
    }
    if gb not in group_map:
        return f"Unknown group_by '{gb}'. Use: symbol, direction, weekday, hour, session, hold_time_bucket, duration_scatter"

    group_expr, label = group_map[gb]

    with SessionLocal() as db:
        rows = repo.analytics_breakdown(db, account_ids, group_expr, date_filter, extra)

    if not rows:
        return "No trades found for the given filters."

    scope = f" ({symbol.upper()})" if symbol else ""
    lines = [f"=== BREAKDOWN BY {label.upper()}{scope} ==="]
    for r in rows:
        lines.append(
            f"{r.bucket}: {r.trades} trades | {r.win_rate}% WR | "
            f"P&L {r.total_pnl} | avg {r.avg_pnl} | "
            f"avg win {r.avg_win} / avg loss {r.avg_loss}"
        )
    return "\n".join(lines)
