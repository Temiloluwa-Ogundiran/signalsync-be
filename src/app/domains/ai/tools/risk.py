from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated, List

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from app.domains.ai.cache import tool_cache
from app.domains.ai.tools.scope import enforce_account_scope

from app.core.database import SessionLocal
from app.domains.ai import repository as repo


@tool
@tool_cache()
def get_risk_snapshot(
    question: Annotated[str, "The trader's question"],
    account_ids: Annotated[List[str], "List of account UUIDs to scope the query to"],
    config: RunnableConfig,
) -> str:
    """Get recent trading behavior to assess risk and discipline.
    Use for: 'how many trades today', 'am I overtrading', 'am I trading larger than usual',
    'should I stop trading', 'daily P&L'."""
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - timedelta(days=7)
    account_ids = enforce_account_scope(account_ids, config)

    with SessionLocal() as db:
        today_trades, week_trades, avg_volume = repo.analytics_risk_snapshot(
            db, account_ids, today, week_ago
        )

    lines = ["=== TODAY ==="]
    today_pnl = sum(float(r.net_profit) for r in today_trades)
    lines.append(f"Trades: {len(today_trades)}")
    lines.append(f"P&L: {today_pnl:.2f}")

    if today_trades:
        avg_vol_today = sum(float(r.volume) for r in today_trades) / len(today_trades)
        lines.append(f"Avg lot size today: {avg_vol_today:.2f} (all-time avg: {avg_volume:.2f})")

        consec = max_consec = 0
        for r in today_trades:
            if float(r.net_profit) < 0:
                consec += 1
                max_consec = max(max_consec, consec)
            else:
                consec = 0
        lines.append(f"Max consecutive losses today: {max_consec}")

        revenge = sum(
            1 for i in range(1, len(today_trades))
            if float(today_trades[i - 1].net_profit) < 0
            and (today_trades[i].opened_at - today_trades[i - 1].closed_at).total_seconds() < 300
        )
        lines.append(f"Trades entered <5 min after a loss: {revenge}")

    lines.append("\n=== LAST 7 DAYS ===")
    week_pnl = sum(float(r.net_profit) for r in week_trades)
    week_wins = sum(1 for r in week_trades if float(r.net_profit) > 0)
    lines.append(f"Total trades: {len(week_trades)}")
    lines.append(f"Total P&L: {week_pnl:.2f}")
    if week_trades:
        lines.append(f"Win rate: {week_wins / len(week_trades) * 100:.1f}%")

    daily: dict = defaultdict(list)
    for r in week_trades:
        daily[r.day].append(float(r.net_profit))
    for day in sorted(daily.keys()):
        dpnl = sum(daily[day])
        lines.append(f"  {day}: {len(daily[day])} trades, P&L {dpnl:.2f}")

    return "\n".join(lines)
