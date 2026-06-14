import uuid
from collections import defaultdict
from datetime import date, datetime, time, timezone
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
    AnalyticsInstrumentItemResponse,
    AnalyticsInstrumentsResponse,
    AnalyticsSummaryResponse,
    AnalyticsTimePerformancePointResponse,
    AnalyticsTimePerformanceResponse,
    JournalTradeListResponse,
)
from app.shared.utils.timezone import to_account_local_date

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
    include_manual: bool = True,
) -> AnalyticsSummaryResponse:
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = journal_repo.list_trade_rows_for_analytics(
        db, account_ids=[account_id], closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc, include_manual=include_manual,
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
    profit_factor = float(gross_win / gross_loss_abs) if gross_loss_abs else float("inf")
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
    include_manual: bool = True,
) -> AnalyticsTimePerformanceResponse:
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = journal_repo.list_trade_rows_for_analytics(
        db, account_ids=[account_id], closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc, include_manual=include_manual,
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
    include_manual: bool = True,
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
        closed_to_utc_exclusive=end_utc, include_manual=include_manual,
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


def get_analytics_dashboard(
    db: Session,
    *,
    account_id: uuid.UUID | None,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
    recent_limit: int = 8,
    time_basis: str = "close",
    include_manual: bool = True,
) -> AnalyticsDashboardResponse:
    if account_id is not None:
        account = _get_account_or_404(db, account_id, user_id)
        selected_accounts = [account]
        account_timezone = account.timezone
        start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
        trades = journal_repo.list_trade_rows_for_analytics(
            db, account_ids=[account_id], closed_from_utc=start_utc,
            closed_to_utc_exclusive=end_utc, include_manual=include_manual,
        )
        starting_balance = _estimate_starting_balance(db, account=account)
    else:
        selected_accounts = _get_ready_accounts_for_user(db, user_id)
        account_ids = [a.id for a in selected_accounts]
        start_utc, end_utc = _resolve_multi_account_date_window(from_date, to_date)
        trades = journal_repo.list_trade_rows_for_analytics(
            db, account_ids=account_ids, closed_from_utc=start_utc,
            closed_to_utc_exclusive=end_utc, include_manual=include_manual,
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
        include_manual=include_manual,
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
