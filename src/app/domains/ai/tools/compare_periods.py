from datetime import datetime, timedelta, timezone
from typing import Annotated, List

from langchain_core.tools import tool
from app.domains.ai.cache import tool_cache

from app.core.database import SessionLocal
from app.domains.ai import repository as repo


@tool
@tool_cache()
def compare_periods(
    question: Annotated[str, "The trader's question"],
    account_ids: Annotated[List[str], "List of account UUIDs to scope the query to"],
    days: Annotated[int, "Number of days per period to compare, e.g. 30 for last-30 vs prior-30"] = 30,
) -> str:
    """Compare trading performance between the current period and the prior equal period.
    Use for: 'am I improving', 'how does this month compare to last month',
    'last 30 days vs prior 30', 'am I getting better or worse'."""
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    current_start = now - timedelta(days=days)
    prior_start = current_start - timedelta(days=days)

    with SessionLocal() as db:
        curr = repo.analytics_period_stats(db, account_ids, current_start, now)
        prior = repo.analytics_period_stats(db, account_ids, prior_start, current_start)

    def pf(gross_win, gross_loss):
        if gross_loss and float(gross_loss) > 0:
            return round(float(gross_win) / float(gross_loss), 2)
        return "∞"

    def delta(curr_val, prior_val, suffix=""):
        if curr_val is None or prior_val is None:
            return "n/a"
        diff = float(curr_val) - float(prior_val)
        sign = "+" if diff >= 0 else ""
        return f"{sign}{round(diff, 2)}{suffix}"

    lines = [f"=== LAST {days} DAYS ==="]
    lines.append(f"Trades: {curr.total_trades} | Win rate: {curr.win_rate}% | P&L: {curr.total_pnl}")
    lines.append(f"Avg win: {curr.avg_win} | Avg loss: {curr.avg_loss}")
    lines.append(f"Profit factor: {pf(curr.gross_win, curr.gross_loss)}")
    lines.append(f"\n=== PRIOR {days} DAYS ===")
    lines.append(f"Trades: {prior.total_trades} | Win rate: {prior.win_rate}% | P&L: {prior.total_pnl}")
    lines.append(f"Avg win: {prior.avg_win} | Avg loss: {prior.avg_loss}")
    lines.append(f"Profit factor: {pf(prior.gross_win, prior.gross_loss)}")
    lines.append("\n=== CHANGE ===")
    lines.append(f"Trades: {delta(curr.total_trades, prior.total_trades)}")
    lines.append(f"Win rate: {delta(curr.win_rate, prior.win_rate, '%')}")
    lines.append(f"Total P&L: {delta(curr.total_pnl, prior.total_pnl)}")
    lines.append(f"Avg P&L/trade: {delta(curr.avg_pnl, prior.avg_pnl)}")
    return "\n".join(lines)
