from typing import Annotated, List, Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.core.database import SessionLocal
from app.domains.ai import repository as repo
from app.domains.ai.cache import tool_cache
from app.domains.ai.tools.scope import enforce_account_scope


@tool
@tool_cache()
def get_performance_metrics(
    question: Annotated[str, "The trader's question"],
    account_ids: Annotated[List[str], "List of account UUIDs to scope the query to"],
    config: RunnableConfig,
    symbol: Annotated[Optional[str], "Filter to a specific symbol e.g. 'EURUSD'"] = None,
    from_date: Annotated[Optional[str], "Optional start date YYYY-MM-DD"] = None,
    to_date: Annotated[Optional[str], "Optional end date YYYY-MM-DD (inclusive)"] = None,
) -> str:
    """Get complete performance metrics including profit factor, expectancy, win/loss ratio, avg hold time.
    Use for: 'what is my profit factor', 'what is my expectancy', 'give me my full stats',
    'how is my risk reward', 'what is my win rate', 'overall performance summary'."""
    account_ids = enforce_account_scope(account_ids, config)
    with SessionLocal() as db:
        r = repo.analytics_performance_metrics(db, account_ids, symbol, from_date, to_date)

    if not r or r.total_trades == 0:
        return "No trades found for the given filters."

    profit_factor = (
        round(float(r.gross_win) / float(r.gross_loss), 2)
        if r.gross_loss and float(r.gross_loss) > 0
        else "∞"
    )
    win_rate = float(r.win_rate or 0) / 100
    loss_rate = 1 - win_rate
    avg_win = float(r.avg_win or 0)
    avg_loss = float(r.avg_loss or 0)
    expectancy = round((win_rate * avg_win) + (loss_rate * avg_loss), 2)
    rr = round(abs(avg_win / avg_loss), 2) if avg_loss and avg_loss != 0 else "∞"

    scope = f" — {symbol.upper()}" if symbol else ""
    lines = [f"=== PERFORMANCE METRICS{scope} ==="]
    lines.append(f"Total trades: {r.total_trades} ({r.wins}W / {r.losses}L)")
    lines.append(f"Win rate: {r.win_rate}%")
    lines.append(f"Total P&L: {r.total_pnl}")
    lines.append(f"Avg P&L per trade: {r.avg_pnl}")
    lines.append(f"Avg win: {r.avg_win} | Avg loss: {r.avg_loss}")
    lines.append(f"Win/loss ratio (RR): {rr}")
    lines.append(f"Profit factor: {profit_factor}")
    lines.append(f"Expectancy per trade: {expectancy}")
    lines.append(f"Gross win: {r.gross_win} | Gross loss: {r.gross_loss}")
    lines.append(f"Best trade: {r.best_trade} | Worst trade: {r.worst_trade}")
    lines.append(f"Avg hold time: {r.avg_hold_min} min")
    return "\n".join(lines)
