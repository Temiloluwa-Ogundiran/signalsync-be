import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import DefaultDict

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.repositories import analytics_repo, trading_account_repo
from app.services.metaapi_service import metaapi_service
from app.schemas.analytics import (
    AnalyticsBestWorstDay,
    AnalyticsCalendarDayResponse,
    AnalyticsCalendarResponse,
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
)
from app.utils.timezone import local_date_to_utc_range


def _resolve_date_window(from_date: date | None, to_date: date | None, tz: str):
    start_utc = None
    end_utc = None

    if from_date is not None:
        start_utc, _ = local_date_to_utc_range(from_date, tz)

    if to_date is not None:
        _, end_utc = local_date_to_utc_range(to_date, tz)

    return start_utc, end_utc


def _get_account_or_404(db: Session, account_id: uuid.UUID, user_id: uuid.UUID):
    account = trading_account_repo.get_by_id_for_user(db, account_id, user_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found.")
    return account


def get_summary(db: Session, *, account_id: uuid.UUID, user_id: uuid.UUID, from_date: date | None, to_date: date | None):
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = analytics_repo.list_trades_filtered(
        db,
        account_id=account_id,
        closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
    )

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
        drawdown = running_max - cumulative
        max_drawdown = max(max_drawdown, drawdown)

    by_day: DefaultDict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for t in trades:
        by_day[t.closed_at.date()] += t.net_profit

    best_day = None
    worst_day = None
    if by_day:
        best_date, best_pnl = max(by_day.items(), key=lambda item: item[1])
        worst_date, worst_pnl = min(by_day.items(), key=lambda item: item[1])
        best_day = AnalyticsBestWorstDay(date=best_date, pnl=best_pnl)
        worst_day = AnalyticsBestWorstDay(date=worst_date, pnl=worst_pnl)

    # Estimate starting balance as current balance minus all-time realized pnl.
    # This keeps month pnl% meaningful for UI without mixing in win-rate.
    starting_balance = Decimal("0")
    try:
        account_info = metaapi_service.get_account_info(account.meta_account_id)
        current_balance = Decimal(str(account_info.get("balance") or 0))
        all_time_trades = analytics_repo.list_trades_filtered(
            db,
            account_id=account_id,
            closed_from_utc=None,
            closed_to_utc_exclusive=None,
        )
        all_time_realized = sum((t.net_profit for t in all_time_trades), Decimal("0"))
        starting_balance = current_balance - all_time_realized
    except Exception:  # noqa: BLE001
        starting_balance = Decimal("0")

    net_pnl_percent = (
        float((total_net_pnl / starting_balance) * Decimal("100"))
        if starting_balance != 0
        else 0.0
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


def get_calendar(db: Session, *, account_id: uuid.UUID, user_id: uuid.UUID, from_date: date | None, to_date: date | None):
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)

    rows = analytics_repo.list_daily_pnl(
        db,
        account_id=account_id,
        account_timezone=account.timezone,
        closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
    )

    days = []
    for row in rows:
        outcome = "breakeven"
        total_pnl = float(row.total_pnl or 0)
        if row.trade_count == 0:
            outcome = "no_trades"
        elif total_pnl > 0:
            outcome = "win"
        elif total_pnl < 0:
            outcome = "loss"

        days.append(
            AnalyticsCalendarDayResponse(
                date=row.trading_date,
                trade_count=int(row.trade_count or 0),
                total_pnl=total_pnl,
                win_count=int(row.win_count or 0),
                loss_count=int(row.loss_count or 0),
                outcome=outcome,
            )
        )

    month_value = from_date.strftime("%Y-%m") if from_date else "all"
    return AnalyticsCalendarResponse(month=month_value, days=days)


def get_sessions(db: Session, *, account_id: uuid.UUID, user_id: uuid.UUID, from_date: date | None, to_date: date | None):
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = analytics_repo.list_trades_filtered(
        db,
        account_id=account_id,
        closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
    )

    grouped = defaultdict(list)
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


def get_instruments(db: Session, *, account_id: uuid.UUID, user_id: uuid.UUID, from_date: date | None, to_date: date | None):
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = analytics_repo.list_trades_filtered(
        db,
        account_id=account_id,
        closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
    )

    grouped = defaultdict(list)
    for t in trades:
        grouped[t.symbol].append(t)

    items = []
    for symbol, symbol_trades in grouped.items():
        trade_count = len(symbol_trades)
        wins = sum(1 for t in symbol_trades if t.net_profit > 0)
        total_pnl = sum((t.net_profit for t in symbol_trades), Decimal("0"))
        items.append(
            AnalyticsInstrumentItemResponse(
                symbol=symbol,
                trade_count=trade_count,
                win_rate=(wins / trade_count) * 100 if trade_count else 0.0,
                total_pnl=float(total_pnl),
                avg_pnl=float(total_pnl / trade_count) if trade_count else 0.0,
            )
        )

    items.sort(key=lambda x: x.total_pnl, reverse=True)
    return AnalyticsInstrumentsResponse(instruments=items)


def get_equity(db: Session, *, account_id: uuid.UUID, user_id: uuid.UUID, from_date: date | None, to_date: date | None):
    _get_account_or_404(db, account_id, user_id)
    points = analytics_repo.list_snapshots_filtered(
        db,
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
    )
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


def get_setups(db: Session, *, account_id: uuid.UUID, user_id: uuid.UUID, from_date: date | None, to_date: date | None):
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    rows = analytics_repo.list_setups(
        db,
        account_id=account_id,
        closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
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


def get_report(db: Session, *, account_id: uuid.UUID, user_id: uuid.UUID, from_date: date | None, to_date: date | None):
    return AnalyticsReportResponse(
        summary=get_summary(db, account_id=account_id, user_id=user_id, from_date=from_date, to_date=to_date),
        sessions=get_sessions(db, account_id=account_id, user_id=user_id, from_date=from_date, to_date=to_date),
        instruments=get_instruments(db, account_id=account_id, user_id=user_id, from_date=from_date, to_date=to_date),
        setups=get_setups(db, account_id=account_id, user_id=user_id, from_date=from_date, to_date=to_date),
    )
