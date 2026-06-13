from typing import Annotated, List

from langchain_core.tools import tool
from app.domains.ai.cache import tool_cache

from app.core.database import SessionLocal
from app.domains.ai import repository as repo


@tool
@tool_cache()
def get_streaks_and_drawdown(
    question: Annotated[str, "The trader's question"],
    account_ids: Annotated[List[str], "List of account UUIDs to scope the query to"],
) -> str:
    """Get streaks, drawdown, day win %, recovery factor, and average drawdown.
    Use for: 'longest losing streak', 'max drawdown', 'current streak', 'day win %',
    'how many losses in a row', 'recovery factor', 'winning days percentage'."""
    with SessionLocal() as db:
        trades, days, first_balance_row = repo.analytics_streaks(db, account_ids)

    if not trades:
        return "No trades found."

    profits = [float(r.net_profit) for r in trades]
    daily_pnls = [float(r.daily_pnl) for r in days]

    # Trade streaks
    max_win_streak = max_loss_streak = cur_win = cur_loss = 0
    for p in profits:
        if p > 0:
            cur_win += 1; cur_loss = 0
            max_win_streak = max(max_win_streak, cur_win)
        elif p < 0:
            cur_loss += 1; cur_win = 0
            max_loss_streak = max(max_loss_streak, cur_loss)
        else:
            cur_win = cur_loss = 0

    current_streak = 0
    current_streak_type = "none"
    for p in reversed(profits):
        if p > 0 and current_streak_type in ("win", "none"):
            current_streak_type = "win"; current_streak += 1
        elif p < 0 and current_streak_type in ("loss", "none"):
            current_streak_type = "loss"; current_streak += 1
        else:
            break

    # Day streaks
    winning_days = sum(1 for p in daily_pnls if p > 0)
    total_days = len(daily_pnls)
    day_win_pct = round(winning_days / total_days * 100, 1) if total_days else 0

    max_win_day_streak = max_loss_day_streak = cur_win_d = cur_loss_d = 0
    for p in daily_pnls:
        if p > 0:
            cur_win_d += 1; cur_loss_d = 0
            max_win_day_streak = max(max_win_day_streak, cur_win_d)
        elif p < 0:
            cur_loss_d += 1; cur_win_d = 0
            max_loss_day_streak = max(max_loss_day_streak, cur_loss_d)
        else:
            cur_win_d = cur_loss_d = 0

    current_day_streak = 0
    current_day_streak_type = "none"
    for p in reversed(daily_pnls):
        if p > 0 and current_day_streak_type in ("win", "none"):
            current_day_streak_type = "win"; current_day_streak += 1
        elif p < 0 and current_day_streak_type in ("loss", "none"):
            current_day_streak_type = "loss"; current_day_streak += 1
        else:
            break

    # Drawdown
    cumulative = peak = max_dd = 0.0
    drawdown_periods = []
    for p in daily_pnls:
        cumulative += p
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
        if dd > 0:
            drawdown_periods.append(dd)

    avg_dd = round(sum(drawdown_periods) / len(drawdown_periods), 2) if drawdown_periods else 0.0
    starting_balance = float(first_balance_row[0]) if first_balance_row else None
    max_dd_pct = round(max_dd / starting_balance * 100, 2) if starting_balance and starting_balance > 0 else None
    total_pnl = sum(profits)
    recovery_factor = round(total_pnl / max_dd, 2) if max_dd > 0 else "∞"

    lines = ["=== STREAKS & DRAWDOWN ===", "\n[TRADE STREAKS]"]
    lines.append(f"Max winning streak: {max_win_streak} trades")
    lines.append(f"Max losing streak:  {max_loss_streak} trades")
    if current_streak_type == "win":
        lines.append(f"Current trade streak: {current_streak} wins in a row")
    elif current_streak_type == "loss":
        lines.append(f"Current trade streak: {current_streak} losses in a row")
    else:
        lines.append("Current trade streak: none")

    lines.append("\n[DAY STREAKS]")
    lines.append(f"Total trading days: {total_days} | Winning days: {winning_days}")
    lines.append(f"Day win %: {day_win_pct}%")
    lines.append(f"Max winning day streak: {max_win_day_streak} days")
    lines.append(f"Max losing day streak:  {max_loss_day_streak} days")
    if current_day_streak_type == "win":
        lines.append(f"Current day streak: {current_day_streak} winning days in a row")
    elif current_day_streak_type == "loss":
        lines.append(f"Current day streak: {current_day_streak} losing days in a row")
    else:
        lines.append("Current day streak: none")

    lines.append("\n[DRAWDOWN]")
    dd_str = f"${round(max_dd, 2)}"
    if max_dd_pct is not None:
        dd_str += f" ({max_dd_pct}% of starting balance)"
    lines.append(f"Max drawdown: {dd_str}")
    lines.append(f"Avg drawdown: ${avg_dd}")
    lines.append("\n[RECOVERY]")
    lines.append(f"Total net P&L: {round(total_pnl, 2)}")
    lines.append(f"Recovery factor: {recovery_factor} (net P&L / max drawdown)")
    return "\n".join(lines)
