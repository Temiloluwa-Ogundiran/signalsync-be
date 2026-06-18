import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.domains.journal import repository as journal_repo
from app.domains.journal.schemas import (
    AnalyticsBestWorstDay,
    AnalyticsCalendarDayResponse,
    AnalyticsCalendarResponse,
    AnalyticsDashboardResponse,
    AnalyticsEquityCurvePointResponse,
    AnalyticsEquityCurveResponse,
    AnalyticsEvaluationResponse,
    AnalyticsInstrumentItemResponse,
    AnalyticsInstrumentsResponse,
    AnalyticsIntradayCurveDayResponse,
    AnalyticsIntradayCurvePointResponse,
    AnalyticsIntradayCurvesResponse,
    AnalyticsSummaryResponse,
    AnalyticsTimePerformancePointResponse,
    AnalyticsTimePerformanceResponse,
    JournalTradeListResponse,
)
from app.shared.utils.timezone import (
    to_account_local_date,
    to_account_local_datetime,
)

from ._helpers import (
    _build_trade_response,
    _estimate_starting_balance,
    _get_account_or_404,
    _get_ready_accounts_for_user,
    _resolve_date_window,
    _resolve_multi_account_date_window,
)


def get_analytics_summary(
    db: Session,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
) -> AnalyticsSummaryResponse:
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = journal_repo.list_trade_rows_for_analytics(
        db, account_ids=[account_id], closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
    )
    return _compute_summary(trades=trades, starting_balance=_estimate_starting_balance(db, account=account))


def _compute_summary(*, trades, starting_balance: Decimal) -> AnalyticsSummaryResponse:
    total_trades = len(trades)
    total_net_pnl = sum((t.net_profit for t in trades), Decimal("0"))
    wins = sum(1 for t in trades if t.net_profit > 0)
    losses = sum(1 for t in trades if t.net_profit < 0)
    gross_win = sum((t.net_profit for t in trades if t.net_profit > 0), Decimal("0"))
    gross_loss_negative = sum((t.net_profit for t in trades if t.net_profit < 0), Decimal("0"))
    gross_loss_abs = abs(gross_loss_negative)

    win_rate = (wins / total_trades) * 100 if total_trades else 0.0
    # Trade-level PF: gross win / |gross loss| over individual closed trades.
    # None when there are no losing trades — the client renders that as "∞".
    # (Returning float("inf") here is not JSON-safe and breaks the response.)
    profit_factor = float(gross_win / gross_loss_abs) if gross_loss_abs else None
    avg_win = float(gross_win / wins) if wins else 0.0
    avg_loss = float(gross_loss_negative / losses) if losses else 0.0
    avg_duration_seconds = (
        sum((t.duration_seconds for t in trades), 0) / total_trades if total_trades else 0.0
    )

    running_max = Decimal("0")
    max_drawdown = Decimal("0")
    cumulative = Decimal("0")
    for t in trades:
        cumulative += t.net_profit
        running_max = max(running_max, cumulative)
        max_drawdown = max(max_drawdown, running_max - cumulative)

    by_day: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for t in trades:
        by_day[t.closed_at.date()] += t.net_profit

    best_day = worst_day = None
    if by_day:
        best_date, best_pnl = max(by_day.items(), key=lambda item: item[1])
        worst_date, worst_pnl = min(by_day.items(), key=lambda item: item[1])
        best_day = AnalyticsBestWorstDay(date=best_date, pnl=best_pnl)
        worst_day = AnalyticsBestWorstDay(date=worst_date, pnl=worst_pnl)

    return AnalyticsSummaryResponse(
        total_trades=total_trades,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_win=avg_win,
        avg_loss=avg_loss,
        avg_trade_duration_seconds=float(avg_duration_seconds),
        total_net_pnl=float(total_net_pnl),
        starting_balance=float(starting_balance),
        max_drawdown=float(max_drawdown),
        best_day=best_day,
        worst_day=worst_day,
    )


def get_analytics_time_performance(
    db: Session,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
    time_basis: str = "close",
) -> AnalyticsTimePerformanceResponse:
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = journal_repo.list_trade_rows_for_analytics(
        db, account_ids=[account_id], closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
    )
    return _compute_time_performance(trades=trades, account_timezone=account.timezone, time_basis=time_basis)


def _compute_time_performance(*, trades, account_timezone: str, time_basis: str) -> AnalyticsTimePerformanceResponse:
    hourly_groups: dict[str, list] = defaultdict(list)
    weekday_groups: dict[str, list] = defaultdict(list)
    weekday_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    account_zone = ZoneInfo(account_timezone)

    for t in trades:
        basis_time = t.opened_at if time_basis == "open" else t.closed_at
        closed_at_utc = basis_time if basis_time.tzinfo is not None else basis_time.replace(tzinfo=timezone.utc)
        local_closed_at = closed_at_utc.astimezone(account_zone)
        hourly_groups[f"{local_closed_at.hour:02d}"].append(t)
        weekday_groups[weekday_order[local_closed_at.weekday()]].append(t)

    def build_point(bucket: str, bucket_trades: list) -> AnalyticsTimePerformancePointResponse:
        count = len(bucket_trades)
        wins = sum(1 for item in bucket_trades if item.net_profit > 0)
        total_pnl = sum((item.net_profit for item in bucket_trades), Decimal("0"))
        return AnalyticsTimePerformancePointResponse(
            bucket=bucket,
            trade_count=count,
            total_pnl=float(total_pnl),
            win_rate=(wins / count) * 100 if count else 0.0,
            avg_pnl=float(total_pnl / count) if count else 0.0,
        )

    return AnalyticsTimePerformanceResponse(
        hourly=[build_point(f"{hour:02d}", hourly_groups.get(f"{hour:02d}", [])) for hour in range(24)],
        daily=[build_point(day, weekday_groups.get(day, [])) for day in weekday_order],
    )


def get_analytics_equity_curve(
    db: Session,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
) -> AnalyticsEquityCurveResponse:
    """Cumulative net realized P&L over time, starting from zero.

    Derived purely from closed trades (no account snapshots / starting balance):
    bucket each trade's net P&L by its account-local close date, then walk the
    days in order keeping a running total. The final point equals the period's
    total net P&L, and the curve crosses zero exactly when the account goes
    net-negative — letting the client split the area green (>=0) / red (<0).
    """
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = journal_repo.list_trade_rows_for_analytics(
        db, account_ids=[account_id], closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
    )

    # Sum net P&L per account-local trading day.
    daily_pnl: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for trade in trades:
        local_day = to_account_local_date(trade.closed_at, account.timezone)
        daily_pnl[local_day] += trade.net_profit

    running = Decimal("0")
    points: list[AnalyticsEquityCurvePointResponse] = []
    for day in sorted(daily_pnl.keys()):
        running += daily_pnl[day]
        points.append(
            AnalyticsEquityCurvePointResponse(
                date=day,
                cumulative_pnl=float(running),
                daily_pnl=float(daily_pnl[day]),
            )
        )

    return AnalyticsEquityCurveResponse(points=points)


def get_analytics_intraday_curves(
    db: Session,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
) -> AnalyticsIntradayCurvesResponse:
    """Per-day intraday running-P&L curves for a date range, in one payload.

    For each account-local trading day, take that day's closed trades, order
    them by close time, and walk through accumulating net P&L from zero. The
    resulting per-day series is a time sequence (one point per trade close) that
    the client renders directly as a day sparkline — no per-day fetches needed.
    """
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = journal_repo.list_trade_rows_for_analytics(
        db, account_ids=[account_id], closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
    )

    # Bucket trades by account-local close date.
    by_day: dict[date, list] = defaultdict(list)
    for trade in trades:
        local_day = to_account_local_date(trade.closed_at, account.timezone)
        by_day[local_day].append(trade)

    days: list[AnalyticsIntradayCurveDayResponse] = []
    for day in sorted(by_day.keys()):
        # Order matters: the sparkline is a time sequence. Sort by close time
        # (tie-break by id for stable ordering on equal timestamps).
        day_trades = sorted(by_day[day], key=lambda t: (t.closed_at, str(t.id)))
        running = Decimal("0")
        points: list[AnalyticsIntradayCurvePointResponse] = []
        for trade in day_trades:
            running += trade.net_profit
            points.append(
                AnalyticsIntradayCurvePointResponse(
                    t=to_account_local_datetime(trade.closed_at, account.timezone),
                    cumulative_pnl=float(running),
                )
            )
        days.append(
            AnalyticsIntradayCurveDayResponse(
                date=day,
                net_pnl=float(running),
                points=points,
            )
        )

    return AnalyticsIntradayCurvesResponse(days=days)



def get_analytics_curve(
    db: Session,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
    granularity: str,  # "daily" or "intraday"
):
    """Unified curve endpoint supporting both daily and intraday granularities.
    
    Deterministic sort: close_time ASC, numeric broker_trade_id ASC NULLS LAST,
    broker_trade_id ASC, id ASC.
    This ensures: same input -> same curve, every time. True execution order when the
    broker ticket exists; stable order when it doesn't.
    
    - granularity="daily": daily P&L bars + cumulative curve (range-scoped reset)
    - granularity="intraday": per-day sequences with trades, cumulative resets daily,
                              downsampled to ~20 points per day.
    """
    from sqlalchemy import BigInteger, case, cast
    from app.domains.accounts.models import Trade
    from app.domains.journal.schemas import (
        AnalyticsCurveDailyPointResponse,
        AnalyticsCurveDailyResponse,
        AnalyticsCurveIntradayDayResponse,
        AnalyticsCurveIntradayPointResponse,
        AnalyticsCurveIntradayResponse,
        AnalyticsCurveResponse,
    )
    
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    
    # Fetch trades with deterministic sort. MT5 tickets are numeric, but CSV/demo
    # imports and manual fixtures can carry ids like "demo-0005"; guard the cast.
    stmt = (
        db.query(Trade)
        .filter(
            Trade.account_id == account_id,
        )
    )

    if start_utc is not None:
        stmt = stmt.filter(Trade.closed_at >= start_utc)
    if end_utc is not None:
        stmt = stmt.filter(Trade.closed_at < end_utc)
    
    # Deterministic sort: close_time -> numeric ticket -> raw id -> UUID.
    numeric_broker_trade_id = case(
        (
            Trade.broker_trade_id.op("~")(r"^[0-9]+$"),
            cast(Trade.broker_trade_id, BigInteger),
        ),
        else_=None,
    )

    stmt = stmt.order_by(
        Trade.closed_at.asc(),
        numeric_broker_trade_id.asc().nullslast(),
        Trade.broker_trade_id.asc(),
        Trade.id.asc(),
    )
    
    trades = stmt.all()
    
    if granularity == "daily":
        return _build_daily_curve(trades, account.timezone)
    elif granularity == "intraday":
        return _build_intraday_curve(trades, account.timezone)
    else:
        raise ValueError(f"Invalid granularity: {granularity}. Must be 'daily' or 'intraday'.")


def _build_daily_curve(trades, account_timezone):
    """Build daily P&L curve (range-scoped cumulative)."""
    from app.domains.journal.schemas import (
        AnalyticsCurveDailyPointResponse,
        AnalyticsCurveDailyResponse,
        AnalyticsCurveResponse,
    )
    
    daily_pnl: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for trade in trades:
        local_day = to_account_local_date(trade.closed_at, account_timezone)
        daily_pnl[local_day] += trade.net_profit
    
    running = Decimal("0")
    points = []
    for day in sorted(daily_pnl.keys()):
        running += daily_pnl[day]
        points.append(
            AnalyticsCurveDailyPointResponse(
                date=day,
                daily_pnl=float(daily_pnl[day]),
                cumulative_pnl=float(running),
            )
        )

    # Prepend a $0 baseline node the calendar day before the first traded day,
    # so the cumulative line visibly starts at $0 (daily analogue of intraday's
    # "first close − 1h"). Consumers that want trading days only (bar chart, KPI
    # sparkline) filter on is_baseline.
    if points:
        baseline_day = points[0].date - timedelta(days=1)
        points = [
            AnalyticsCurveDailyPointResponse(
                date=baseline_day,
                daily_pnl=None,
                cumulative_pnl=0.0,
                is_baseline=True,
            )
        ] + points

    return AnalyticsCurveResponse(
        daily_curve=AnalyticsCurveDailyResponse(points=points),
        intraday_curve=None,
    )


def _build_intraday_curve(trades, account_timezone):
    """Build intraday curves (daily reset, sequence-indexed, downsampled ~20 per day)."""
    from app.domains.journal.schemas import (
        AnalyticsCurveIntradayDayResponse,
        AnalyticsCurveIntradayPointResponse,
        AnalyticsCurveIntradayResponse,
        AnalyticsCurveResponse,
    )
    
    # Bucket trades by account-local close date
    by_day: dict[date, list] = defaultdict(list)
    for trade in trades:
        local_day = to_account_local_date(trade.closed_at, account_timezone)
        by_day[local_day].append(trade)
    
    days = []
    for day in sorted(by_day.keys()):
        # Trades already arrive in the deterministic shared order
        # (close_time ASC, numeric ticket NULLS LAST, raw id ASC, id ASC); bucketing
        # preserves it, so no per-day re-sort is needed.
        day_trades = by_day[day]
        running = Decimal("0")
        points = []

        # Per-day summary stats (standard defs) accumulated in the same pass.
        gross = Decimal("0")
        commissions = Decimal("0")
        volume = Decimal("0")
        win_count = 0
        loss_count = 0
        gross_win = Decimal("0")  # sum of positive net P&L
        gross_loss = Decimal("0")  # abs sum of negative net P&L

        # One node per trade (no downsampling): each point carries its account-local
        # close time `t`. The frontend plots on the sequence index `i`, deriving the
        # HH:MM:SS label from `t`.
        for idx, trade in enumerate(day_trades):
            running += trade.net_profit
            gross += trade.profit
            commissions += trade.commission
            volume += trade.volume
            if trade.net_profit > 0:
                win_count += 1
                gross_win += trade.net_profit
            elif trade.net_profit < 0:
                loss_count += 1
                gross_loss += -trade.net_profit
            points.append(
                AnalyticsCurveIntradayPointResponse(
                    i=idx + 1,  # 1-indexed (0 is the baseline)
                    t=to_account_local_datetime(trade.closed_at, account_timezone),
                    symbol=trade.symbol,
                    cumulative_pnl=float(running),
                )
            )

        trades_count = len(day_trades)
        profit_factor = (
            float(gross_win / gross_loss) if gross_loss > 0 else None
        )
        win_rate = (win_count / trades_count * 100) if trades_count else 0.0

        # Prepend a $0 baseline node one hour before the first trade's local close
        # time, clamped so it never crosses below midnight of that local day.
        first_local = to_account_local_datetime(day_trades[0].closed_at, account_timezone)
        midnight = first_local.replace(hour=0, minute=0, second=0, microsecond=0)
        baseline_t = max(first_local - timedelta(hours=1), midnight)
        points = [
            AnalyticsCurveIntradayPointResponse(i=0, t=baseline_t, cumulative_pnl=0.0)
        ] + points

        days.append(
            AnalyticsCurveIntradayDayResponse(
                date=day,
                net_pnl=float(running),
                trades_count=trades_count,
                gross_pnl=float(gross),
                win_count=win_count,
                loss_count=loss_count,
                commissions=float(commissions),
                win_rate=win_rate,
                volume=float(volume),
                profit_factor=profit_factor,
                points=points,
            )
        )
    
    return AnalyticsCurveResponse(
        daily_curve=None,
        intraday_curve=AnalyticsCurveIntradayResponse(days=days),
    )


def _downsample_points(points: list, max_points: int = 20) -> list:
    """Downsample points to max_points, always keeping first and last."""
    if len(points) <= max_points:
        return points
    
    # Always include first and last
    sampled = [points[0]]
    
    # Sample evenly between first and last (exclusive)
    step = (len(points) - 1) / (max_points - 1)
    for i in range(1, max_points - 1):
        idx = int(i * step)
        sampled.append(points[idx])
    
    # Add last
    sampled.append(points[-1])
    
    return sampled

def get_analytics_evaluation(
    db: Session,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
) -> AnalyticsEvaluationResponse:
    """Detailed evaluation stats for the journal sidebar, all trade-derived."""
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = journal_repo.list_trade_rows_for_analytics(
        db, account_ids=[account_id], closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
    )

    if not trades:
        return AnalyticsEvaluationResponse(
            total_trades=0, avg_profit_per_trading_day=0.0, biggest_winner=0.0,
            biggest_loser=0.0, total_fees=0.0, avg_hold_seconds=0.0,
            winrate_wo_be=0.0, roi=0.0, max_drawdown_pct=0.0,
            winning_days=0, losing_days=0, trades_per_day=0.0,
            trades_per_week=0.0, recent_streak=[],
        )

    total_trades = len(trades)
    total_net_pnl = sum((t.net_profit for t in trades), Decimal("0"))
    total_fees = sum(
        ((t.commission or Decimal("0")) + (t.swap or Decimal("0")) for t in trades),
        Decimal("0"),
    )
    biggest_winner = max((t.net_profit for t in trades), default=Decimal("0"))
    biggest_loser = min((t.net_profit for t in trades), default=Decimal("0"))
    avg_hold_seconds = (
        sum((t.duration_seconds or 0) for t in trades) / total_trades
    )

    wins = sum(1 for t in trades if t.net_profit > 0)
    losses = sum(1 for t in trades if t.net_profit < 0)
    winrate_wo_be = (wins / (wins + losses) * 100) if (wins + losses) else 0.0

    starting_balance = _estimate_starting_balance(db, account=account)
    roi = (
        float(total_net_pnl / starting_balance * Decimal("100"))
        if starting_balance and starting_balance != 0
        else 0.0
    )

    # Per-account-local-day aggregation for day-based stats.
    by_day: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for t in trades:
        by_day[to_account_local_date(t.closed_at, account.timezone)] += t.net_profit
    trading_days = len(by_day)
    winning_days = sum(1 for v in by_day.values() if v > 0)
    losing_days = sum(1 for v in by_day.values() if v < 0)
    avg_profit_per_trading_day = (
        float(total_net_pnl) / trading_days if trading_days else 0.0
    )
    trades_per_day = total_trades / trading_days if trading_days else 0.0
    # Span the calendar from first to last trading day for a per-week rate.
    day_keys = sorted(by_day.keys())
    span_days = (day_keys[-1] - day_keys[0]).days + 1
    weeks = max(1.0, span_days / 7)
    trades_per_week = total_trades / weeks

    # Max drawdown as % of the running cumulative-P&L peak (peak-to-trough).
    running_max = Decimal("0")
    max_dd_abs = Decimal("0")
    cumulative = Decimal("0")
    for t in trades:
        cumulative += t.net_profit
        running_max = max(running_max, cumulative)
        max_dd_abs = max(max_dd_abs, running_max - cumulative)
    dd_basis = (
        starting_balance if starting_balance and starting_balance > 0 else running_max
    )
    max_drawdown_pct = (
        float(max_dd_abs / dd_basis * Decimal("100")) if dd_basis and dd_basis > 0 else 0.0
    )

    # Most recent trades, oldest→newest (trades are already closed_at-ascending).
    recent = trades[-5:]
    recent_streak = [
        "W" if t.net_profit > 0 else ("L" if t.net_profit < 0 else "B")
        for t in recent
    ]

    return AnalyticsEvaluationResponse(
        total_trades=total_trades,
        avg_profit_per_trading_day=avg_profit_per_trading_day,
        biggest_winner=float(biggest_winner),
        biggest_loser=float(biggest_loser),
        total_fees=float(total_fees),
        avg_hold_seconds=float(avg_hold_seconds),
        winrate_wo_be=winrate_wo_be,
        roi=roi,
        max_drawdown_pct=max_drawdown_pct,
        winning_days=winning_days,
        losing_days=losing_days,
        trades_per_day=trades_per_day,
        trades_per_week=trades_per_week,
        recent_streak=recent_streak,
    )


def get_analytics_dashboard(
    db: Session,
    *,
    account_id: uuid.UUID | None,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
    recent_limit: int = 8,
    time_basis: str = "close",
) -> AnalyticsDashboardResponse:
    if account_id is not None:
        account = _get_account_or_404(db, account_id, user_id)
        selected_accounts = [account]
        account_timezone = account.timezone
        start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
        trades = journal_repo.list_trade_rows_for_analytics(
            db, account_ids=[account_id], closed_from_utc=start_utc,
            closed_to_utc_exclusive=end_utc,
        )
        starting_balance = _estimate_starting_balance(db, account=account)
    else:
        selected_accounts = _get_ready_accounts_for_user(db, user_id)
        account_ids = [a.id for a in selected_accounts]
        start_utc, end_utc = _resolve_multi_account_date_window(from_date, to_date)
        trades = journal_repo.list_trade_rows_for_analytics(
            db, account_ids=account_ids, closed_from_utc=start_utc,
            closed_to_utc_exclusive=end_utc,
        )
        account_timezone = "UTC"
        starting_balance = (
            sum((_estimate_starting_balance(db, account=a) for a in selected_accounts), Decimal("0"))
            if selected_accounts else Decimal("0")
        )

    summary = _compute_summary(trades=trades, starting_balance=starting_balance)

    calendar_map: dict[date, dict] = {}
    tz_by_account = {a.id: a.timezone for a in selected_accounts} if account_id is None else {}
    for trade in trades:
        local_day = (
            to_account_local_date(trade.closed_at, account_timezone)
            if account_id is not None
            else to_account_local_date(trade.closed_at, tz_by_account.get(trade.account_id, "UTC"))
        )
        if local_day not in calendar_map:
            calendar_map[local_day] = {"trade_count": 0, "total_pnl": Decimal("0"), "win_count": 0, "loss_count": 0}
        bucket = calendar_map[local_day]
        bucket["trade_count"] += 1
        bucket["total_pnl"] = Decimal(bucket["total_pnl"]) + trade.net_profit
        if trade.net_profit > 0:
            bucket["win_count"] += 1
        elif trade.net_profit < 0:
            bucket["loss_count"] += 1

    journal_active_dates: set[date] = set()
    if account_id is not None and calendar_map:
        journal_active_dates = journal_repo.list_trading_dates_with_journal_activity(
            db, account_id=account_id, account_timezone=account_timezone,
            from_date=min(calendar_map.keys()), to_date=max(calendar_map.keys()),
        )

    calendar_days = []
    for trading_day in sorted(calendar_map.keys()):
        row = calendar_map[trading_day]
        total_pnl_for_day = float(Decimal(row["total_pnl"]))
        outcome = "win" if total_pnl_for_day > 0 else ("loss" if total_pnl_for_day < 0 else "breakeven")
        calendar_days.append(
            AnalyticsCalendarDayResponse(
                date=trading_day,
                trade_count=int(row["trade_count"]),
                total_pnl=total_pnl_for_day,
                win_count=int(row["win_count"]),
                loss_count=int(row["loss_count"]),
                outcome=outcome,
                has_journal_activity=trading_day in journal_active_dates,
            )
        )
    calendar = AnalyticsCalendarResponse(
        month=from_date.strftime("%Y-%m") if from_date else "all", days=calendar_days,
    )

    symbol_groups: dict[str, list] = defaultdict(list)
    for trade in trades:
        symbol_groups[trade.symbol].append(trade)
    instruments_items = []
    for symbol, symbol_trades in symbol_groups.items():
        trade_count = len(symbol_trades)
        sym_wins = sum(1 for t in symbol_trades if t.net_profit > 0)
        sym_pnl = sum((t.net_profit for t in symbol_trades), Decimal("0"))
        mfe_values = [float(t.mfe) for t in symbol_trades if t.mfe is not None]
        mae_values = [float(t.mae) for t in symbol_trades if t.mae is not None]
        instruments_items.append(
            AnalyticsInstrumentItemResponse(
                symbol=symbol,
                trade_count=trade_count,
                win_rate=(sym_wins / trade_count) * 100 if trade_count else 0.0,
                total_pnl=float(sym_pnl),
                avg_pnl=float(sym_pnl / trade_count) if trade_count else 0.0,
                avg_mfe=(sum(mfe_values) / len(mfe_values)) if mfe_values else None,
                avg_mae=(sum(mae_values) / len(mae_values)) if mae_values else None,
            )
        )
    instruments_items.sort(key=lambda x: x.total_pnl, reverse=True)
    instruments = AnalyticsInstrumentsResponse(instruments=instruments_items)

    time_performance = _compute_time_performance(
        trades=trades, account_timezone=account_timezone, time_basis=time_basis
    )

    account_ids_to_fetch = [account_id] if account_id is not None else [a.id for a in selected_accounts]
    recent_db_trades = journal_repo.list_recent_trades_for_dashboard(
        db,
        account_ids=account_ids_to_fetch,
        closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
        limit=recent_limit,
    )
    recent_models = [
        _build_trade_response(trade, account_timezone=account_timezone)
        for trade in recent_db_trades
    ]

    return AnalyticsDashboardResponse(
        summary=summary,
        calendar=calendar,
        instruments=instruments,
        time_performance=time_performance,
        recent_trades=JournalTradeListResponse(items=recent_models, next_cursor=None),
    )
