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
def detect_patterns(
    question: Annotated[str, "The trader's question"],
    account_ids: Annotated[List[str], "List of account UUIDs to scope the query to"],
    config: RunnableConfig,
) -> str:
    """Detect harmful behavioural patterns in trading history.
    Use for: 'am I revenge trading', 'do I overtrade', 'do I size up after losses',
    'do I lose more after big wins', 'what bad habits do I have', 'green to red days',
    'discipline check'."""
    now = datetime.now(timezone.utc)
    lookback = now - timedelta(days=30)
    account_ids = enforce_account_scope(account_ids, config)

    with SessionLocal() as db:
        trades, avg_volume_all = repo.analytics_detect_patterns(db, account_ids, lookback)

    if not trades:
        return "No trades found in the last 30 days."

    profits = [float(r.net_profit) for r in trades]
    volumes = [float(r.volume) for r in trades]

    # Revenge trading: trade opened <5 min after a losing trade closes
    revenge_count = sum(
        1 for i in range(1, len(trades))
        if float(trades[i - 1].net_profit) < 0
        and 0 <= (trades[i].opened_at - trades[i - 1].closed_at).total_seconds() < 300
    )

    # Overtrading: days with more than 6 trades
    daily_counts: dict = defaultdict(int)
    for r in trades:
        daily_counts[r.day] += 1
    overtrade_days = {d: c for d, c in daily_counts.items() if c > 6}

    # Lot size creep after losses
    post_loss_vols = [
        float(trades[i].volume)
        for i in range(1, len(trades))
        if float(trades[i - 1].net_profit) < 0
    ]
    avg_post_loss_vol = round(sum(post_loss_vols) / len(post_loss_vols), 2) if post_loss_vols else None

    # Loss clustering after big wins (top 10% wins)
    top10_threshold = None
    post_bigwin_losses = 0
    pos_profits = sorted([p for p in profits if p > 0], reverse=True)
    if pos_profits:
        top10_threshold = pos_profits[max(0, len(pos_profits) // 10 - 1)]
        for i, r in enumerate(trades):
            if float(r.net_profit) >= top10_threshold:
                for j in range(i + 1, min(i + 4, len(trades))):
                    if float(trades[j].net_profit) < 0:
                        post_bigwin_losses += 1

    # Green-to-red days
    daily_profits: dict = defaultdict(list)
    for r in trades:
        daily_profits[r.day].append(float(r.net_profit))
    green_to_red_days = [
        (day, round(sum(ps), 2))
        for day, ps in daily_profits.items()
        if any(sum(ps[:k+1]) > 0 for k in range(len(ps))) and sum(ps) < 0
    ]

    recent_5_avg = round(sum(volumes[-5:]) / min(5, len(volumes)), 2) if volumes else 0
    size_creep = avg_volume_all > 0 and recent_5_avg > avg_volume_all * 1.5

    lines = ["=== BEHAVIOURAL PATTERN ANALYSIS (last 30 days) ==="]
    lines.append(f"\n[REVENGE TRADING]")
    lines.append(f"Trades entered <5 min after a loss: {revenge_count}")
    lines.append("⚠ High" if revenge_count >= 5 else ("Moderate" if revenge_count >= 2 else "Low — looks controlled"))

    lines.append(f"\n[OVERTRADING]")
    if overtrade_days:
        lines.append(f"Days with >6 trades: {len(overtrade_days)}")
        for d, c in sorted(overtrade_days.items()):
            lines.append(f"  {d}: {c} trades")
    else:
        lines.append("No overtrading days detected (threshold: >6 trades/day)")

    lines.append(f"\n[LOT SIZE CREEP AFTER LOSSES]")
    if avg_post_loss_vol is not None:
        ratio = round(avg_post_loss_vol / avg_volume_all, 2) if avg_volume_all else "n/a"
        lines.append(f"Avg lot after a loss: {avg_post_loss_vol} | All-time avg: {avg_volume_all}")
        lines.append(f"Ratio: {ratio}x {'⚠ Sizing up after losses' if isinstance(ratio, float) and ratio > 1.2 else '— within normal range'}")
    else:
        lines.append("No post-loss trades to analyse")

    lines.append(f"\n[RECENT LOT SIZE vs ALL-TIME AVG]")
    lines.append(f"Last 5 trades avg lot: {recent_5_avg} | All-time avg: {avg_volume_all}")
    lines.append("⚠ Position size creep detected" if size_creep else "Position sizing looks consistent")

    lines.append(f"\n[LOSS CLUSTERING AFTER BIG WINS]")
    lines.append(f"Losses within 3 trades after a top-10% win: {post_bigwin_losses}")
    lines.append("⚠ Possible complacency after big wins" if post_bigwin_losses >= 3 else "No significant clustering detected")

    lines.append(f"\n[GREEN-TO-RED DAYS]")
    if green_to_red_days:
        lines.append(f"Days that went positive then closed negative: {len(green_to_red_days)}")
        for d, pnl in green_to_red_days:
            lines.append(f"  {d}: ended at {pnl}")
    else:
        lines.append("No green-to-red days in this period")

    return "\n".join(lines)
