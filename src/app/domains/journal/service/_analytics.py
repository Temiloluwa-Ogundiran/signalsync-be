import uuid
from collections import defaultdict
from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.domains.accounts import repository as account_repo
from app.domains.journal import repository as journal_repo
from app.domains.journal.schemas import (
    AnalyticsBalanceHistoryPointResponse,
    AnalyticsBalanceHistoryResponse,
    AnalyticsBestWorstDay,
    AnalyticsCalendarDayResponse,
    AnalyticsCalendarResponse,
    AnalyticsDashboardResponse,
    AnalyticsEquityPointResponse,
    AnalyticsEquityResponse,
    AnalyticsInstrumentItemResponse,
    AnalyticsInstrumentsResponse,
    AnalyticsReportResponse,
    AnalyticsSessionItemResponse,
    AnalyticsSessionsResponse,
    AnalyticsSetupItemResponse,
    AnalyticsSetupsResponse,
    AnalyticsSummaryResponse,
    AnalyticsTimePerformancePointResponse,
    AnalyticsTimePerformanceResponse,
    AnalyticsTradeSourceItemResponse,
    AnalyticsTradeSourceResponse,
    JournalTradeListResponse,
)
from app.shared.utils.timezone import local_date_to_utc_range, to_account_local_date

from ._helpers import (
    _build_trade_response,
    _estimate_starting_balance,
    _get_account_or_404,
    _get_ready_accounts_for_user,
    _resolve_date_window,
    _resolve_day_balances,
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
    trades = journal_repo.list_trades_filtered(
        db, account_id=account_id, closed_from_utc=start_utc,
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

    net_pnl_percent = (
        float((total_net_pnl / starting_balance) * Decimal("100")) if starting_balance != 0 else 0.0
    )
    return AnalyticsSummaryResponse(
        total_trades=total_trades,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_win=avg_win,
        avg_loss=avg_loss,
        avg_trade_duration_seconds=float(avg_duration_seconds),
        total_net_pnl=float(total_net_pnl),
        starting_balance=float(starting_balance),
        net_pnl_percent=net_pnl_percent,
        max_drawdown=float(max_drawdown),
        best_day=best_day,
        worst_day=worst_day,
    )


def get_analytics_calendar(
    db: Session,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
    include_manual: bool = True,
) -> AnalyticsCalendarResponse:
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    rows = journal_repo.list_daily_pnl(
        db,
        account_id=account_id,
        account_timezone=account.timezone,
        closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
        include_manual=include_manual,
    )

    journal_active_dates: set[date] = set()
    if rows:
        jd_from = min(row.trading_date for row in rows)
        jd_to = max(row.trading_date for row in rows)
        journal_active_dates = journal_repo.list_trading_dates_with_journal_activity(
            db, account_id=account_id, account_timezone=account.timezone,
            from_date=jd_from, to_date=jd_to,
        )

    days = []
    for row in rows:
        total_pnl = float(row.total_pnl or 0)
        if row.trade_count == 0:
            outcome = "no_trades"
        elif total_pnl > 0:
            outcome = "win"
        elif total_pnl < 0:
            outcome = "loss"
        else:
            outcome = "breakeven"
        days.append(
            AnalyticsCalendarDayResponse(
                date=row.trading_date,
                trade_count=int(row.trade_count or 0),
                total_pnl=total_pnl,
                win_count=int(row.win_count or 0),
                loss_count=int(row.loss_count or 0),
                outcome=outcome,
                has_journal_activity=row.trading_date in journal_active_dates,
            )
        )
    return AnalyticsCalendarResponse(
        month=from_date.strftime("%Y-%m") if from_date else "all", days=days
    )


def get_analytics_sessions(
    db: Session,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
    include_manual: bool = True,
) -> AnalyticsSessionsResponse:
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = journal_repo.list_trades_filtered(
        db, account_id=account_id, closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc, include_manual=include_manual,
    )
    grouped: dict = defaultdict(list)
    for t in trades:
        grouped[t.session.value].append(t)
    items = []
    for session_name, session_trades in grouped.items():
        trade_count = len(session_trades)
        wins = sum(1 for t in session_trades if t.net_profit > 0)
        total_pnl = sum((t.net_profit for t in session_trades), Decimal("0"))
        items.append(
            AnalyticsSessionItemResponse(
                session=session_name,
                trade_count=trade_count,
                win_rate=(wins / trade_count) * 100 if trade_count else 0.0,
                total_pnl=float(total_pnl),
                avg_pnl=float(total_pnl / trade_count) if trade_count else 0.0,
            )
        )
    items.sort(key=lambda x: x.total_pnl, reverse=True)
    return AnalyticsSessionsResponse(sessions=items)


def get_analytics_instruments(
    db: Session,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
    include_manual: bool = True,
) -> AnalyticsInstrumentsResponse:
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = journal_repo.list_trades_filtered(
        db, account_id=account_id, closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc, include_manual=include_manual,
    )
    grouped: dict = defaultdict(list)
    for t in trades:
        grouped[t.symbol].append(t)
    items = []
    for symbol, symbol_trades in grouped.items():
        trade_count = len(symbol_trades)
        wins = sum(1 for t in symbol_trades if t.net_profit > 0)
        total_pnl = sum((t.net_profit for t in symbol_trades), Decimal("0"))
        mfe_values = [float(t.mfe) for t in symbol_trades if t.mfe is not None]
        mae_values = [float(t.mae) for t in symbol_trades if t.mae is not None]
        items.append(
            AnalyticsInstrumentItemResponse(
                symbol=symbol,
                trade_count=trade_count,
                win_rate=(wins / trade_count) * 100 if trade_count else 0.0,
                total_pnl=float(total_pnl),
                avg_pnl=float(total_pnl / trade_count) if trade_count else 0.0,
                avg_mfe=(sum(mfe_values) / len(mfe_values)) if mfe_values else None,
                avg_mae=(sum(mae_values) / len(mae_values)) if mae_values else None,
            )
        )
    items.sort(key=lambda x: x.total_pnl, reverse=True)
    return AnalyticsInstrumentsResponse(instruments=items)


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
    trades = journal_repo.list_trades_filtered(
        db, account_id=account_id, closed_from_utc=start_utc,
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


def get_analytics_equity(
    db: Session,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
) -> AnalyticsEquityResponse:
    _get_account_or_404(db, account_id, user_id)
    points = journal_repo.list_account_snapshots(db, account_id=account_id, from_date=from_date, to_date=to_date)
    return AnalyticsEquityResponse(
        points=[
            AnalyticsEquityPointResponse(
                date=p.snapshot_date,
                balance=float(p.balance),
                equity=float(p.equity),
                floating_pnl=float(p.floating_pnl),
            )
            for p in points
        ]
    )


def _balance_history_points_from_trade_closes(
    db: Session,
    *,
    account_id: uuid.UUID,
    account,
    from_date: date,
    to_date: date,
    include_manual: bool = True,
) -> list[AnalyticsBalanceHistoryPointResponse]:
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = journal_repo.list_trades_filtered(
        db, account_id=account_id, closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc, include_manual=include_manual,
    )
    if not trades:
        return []

    sorted_trades = sorted(trades, key=lambda t: (t.closed_at, t.id))
    first_in_window = next(
        (t for t in sorted_trades if from_date <= to_account_local_date(t.closed_at, account.timezone) <= to_date),
        None,
    )
    if first_in_window is None:
        return []

    seed_before_first = _estimate_starting_balance(db, account=account) + account_repo.sum_trade_net_profit(
        db, account_id=account_id, closed_before_utc=first_in_window.closed_at,
    )

    points: list[AnalyticsBalanceHistoryPointResponse] = []
    last_local_date: date | None = None
    running: Decimal | None = None

    for trade in sorted_trades:
        ld = to_account_local_date(trade.closed_at, account.timezone)
        if ld < from_date or ld > to_date:
            continue
        if ld != last_local_date:
            ds, _ = _resolve_day_balances(db, account_id=account_id, trading_date=ld)
            if ds is not None:
                running = ds
            elif last_local_date is None:
                running = seed_before_first
            last_local_date = ld
            points.append(
                AnalyticsBalanceHistoryPointResponse(
                    timestamp=datetime.combine(ld, time.min, tzinfo=timezone.utc),
                    balance=float(running or Decimal("0")),
                    equity=None,
                    source="trade_day_anchor",
                )
            )
        running = (running or Decimal("0")) + trade.net_profit
        points.append(
            AnalyticsBalanceHistoryPointResponse(
                timestamp=trade.closed_at,
                balance=float(running),
                equity=None,
                source="trade_close",
            )
        )
    return points


def get_analytics_balance_history(
    db: Session,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
    granularity: str = "day",
    include_manual: bool = True,
) -> AnalyticsBalanceHistoryResponse:
    account = _get_account_or_404(db, account_id, user_id)
    effective_granularity = (granularity or "day").lower()

    if (
        effective_granularity == "intraday"
        and from_date is not None
        and to_date is not None
        and from_date == to_date
    ):
        trades = account_repo.list_trades_by_account_local_date(
            db, account_id=account_id, trading_date=from_date,
            account_timezone=account.timezone, include_manual=include_manual,
        )
        day_start_balance, _ = _resolve_day_balances(db, account_id=account_id, trading_date=from_date)
        if day_start_balance is not None:
            running = day_start_balance
        else:
            closed_from_utc, _ = local_date_to_utc_range(from_date, account.timezone)
            running = _estimate_starting_balance(db, account=account) + account_repo.sum_trade_net_profit(
                db, account_id=account_id, closed_before_utc=closed_from_utc,
            )
        points: list[AnalyticsBalanceHistoryPointResponse] = [
            AnalyticsBalanceHistoryPointResponse(
                timestamp=datetime.combine(from_date, time.min, tzinfo=timezone.utc),
                balance=float(running),
                equity=None,
                source="intraday_anchor",
            )
        ]
        for trade in sorted(trades, key=lambda t: (t.closed_at, t.id)):
            running += trade.net_profit
            points.append(
                AnalyticsBalanceHistoryPointResponse(
                    timestamp=trade.closed_at, balance=float(running), equity=None, source="trade_close",
                )
            )
        return AnalyticsBalanceHistoryResponse(points=points)

    snapshots = journal_repo.list_account_snapshots(
        db, account_id=account_id, from_date=from_date, to_date=to_date,
    )
    snapshot_points: list[AnalyticsBalanceHistoryPointResponse] = [
        AnalyticsBalanceHistoryPointResponse(
            timestamp=datetime.combine(p.snapshot_date, time.min, tzinfo=timezone.utc),
            balance=float(p.balance),
            equity=float(p.equity),
            source="daily_snapshot",
        )
        for p in snapshots
    ]

    if from_date is None or to_date is None:
        return AnalyticsBalanceHistoryResponse(points=snapshot_points)

    trade_points = _balance_history_points_from_trade_closes(
        db, account_id=account_id, account=account,
        from_date=from_date, to_date=to_date, include_manual=include_manual,
    )
    if trade_points:
        return AnalyticsBalanceHistoryResponse(points=trade_points)
    if snapshot_points:
        return AnalyticsBalanceHistoryResponse(points=snapshot_points)
    return AnalyticsBalanceHistoryResponse(points=[])


def get_analytics_setups(
    db: Session,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
    include_manual: bool = True,
) -> AnalyticsSetupsResponse:
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    rows = journal_repo.list_trade_setups(
        db, account_id=account_id, closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc, include_manual=include_manual,
    )
    setups = []
    for row in rows:
        trade_count = int(row.trade_count or 0)
        win_count = int(row.win_count or 0)
        setups.append(
            AnalyticsSetupItemResponse(
                tag=row.tag,
                trade_count=trade_count,
                win_rate=(win_count / trade_count) * 100 if trade_count else 0.0,
                total_pnl=float(row.total_pnl or 0),
            )
        )
    return AnalyticsSetupsResponse(setups=setups)


def get_analytics_trade_sources(
    db: Session,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
    include_manual: bool = True,
) -> AnalyticsTradeSourceResponse:
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = journal_repo.list_trades_filtered(
        db, account_id=account_id, closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc, include_manual=include_manual,
    )
    grouped: dict = defaultdict(list)
    for t in trades:
        grouped[t.trade_source.value if t.trade_source else "unknown"].append(t)
    sources = []
    for source_name, source_trades in grouped.items():
        trade_count = len(source_trades)
        wins = sum(1 for t in source_trades if t.net_profit > 0)
        total_pnl = sum((t.net_profit for t in source_trades), Decimal("0"))
        sources.append(
            AnalyticsTradeSourceItemResponse(
                trade_source=source_name,
                trade_count=trade_count,
                win_rate=(wins / trade_count) * 100 if trade_count else 0.0,
                total_pnl=float(total_pnl),
                avg_pnl=float(total_pnl / trade_count) if trade_count else 0.0,
            )
        )
    sources.sort(key=lambda x: x.total_pnl, reverse=True)
    return AnalyticsTradeSourceResponse(sources=sources)


def get_analytics_report(
    db: Session,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
    include_manual: bool = True,
) -> AnalyticsReportResponse:
    return AnalyticsReportResponse(
        summary=get_analytics_summary(db, account_id=account_id, user_id=user_id, from_date=from_date, to_date=to_date, include_manual=include_manual),
        sessions=get_analytics_sessions(db, account_id=account_id, user_id=user_id, from_date=from_date, to_date=to_date, include_manual=include_manual),
        instruments=get_analytics_instruments(db, account_id=account_id, user_id=user_id, from_date=from_date, to_date=to_date, include_manual=include_manual),
        setups=get_analytics_setups(db, account_id=account_id, user_id=user_id, from_date=from_date, to_date=to_date, include_manual=include_manual),
        trade_sources=get_analytics_trade_sources(db, account_id=account_id, user_id=user_id, from_date=from_date, to_date=to_date, include_manual=include_manual),
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
    include_manual: bool = True,
) -> AnalyticsDashboardResponse:
    if account_id is not None:
        account = _get_account_or_404(db, account_id, user_id)
        selected_accounts = [account]
        account_timezone = account.timezone
        start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
        trades = journal_repo.list_trades_filtered(
            db, account_id=account_id, closed_from_utc=start_utc,
            closed_to_utc_exclusive=end_utc, include_manual=include_manual,
        )
        starting_balance = _estimate_starting_balance(db, account=account)
    else:
        selected_accounts = _get_ready_accounts_for_user(db, user_id)
        account_ids = [a.id for a in selected_accounts]
        start_utc, end_utc = _resolve_multi_account_date_window(from_date, to_date)
        trades = journal_repo.list_trades_filtered_multi(
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
    for trade in trades:
        local_day = (
            to_account_local_date(trade.closed_at, account_timezone)
            if account_id is not None
            else (trade.closed_at.astimezone(timezone.utc).date() if trade.closed_at.tzinfo is not None else trade.closed_at.date())
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

    recent_models = [
        _build_trade_response(trade, account_timezone=account_timezone)
        for trade in sorted(trades, key=lambda item: (item.closed_at, item.id), reverse=True)[:recent_limit]
    ]

    return AnalyticsDashboardResponse(
        summary=summary,
        calendar=calendar,
        instruments=instruments,
        time_performance=time_performance,
        recent_trades=JournalTradeListResponse(items=recent_models, next_cursor=None),
    )
