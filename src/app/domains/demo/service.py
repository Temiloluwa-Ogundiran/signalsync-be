"""Demo-account seeding: persist generated demo data for a new user.

A demo account is a normal TradingAccount with account_type=demo and a sentinel
meta_account_id, so it is detectable and deletable. All its trades, daily
journals, and per-trade journals cascade-delete with it (FK ondelete=CASCADE),
which makes "clear demo" a single delete.
"""

from __future__ import annotations

import logging
import uuid
import zlib
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.accounts.models import (
    Trade,
    TradeDirection,
    TradeSession,
    TradeSource,
    TradingAccount,
    TradingAccountConnectionState,
    TradingAccountProvisioningStatus,
    TradingAccountStatus,
    TradingAccountType,
    TradingPlatform,
    SyncProvider,
)
from app.domains.journal.models import DailyJournal, TradeJournal
from app.domains.demo.generator import DemoData, TradeSpec, generate_demo_data

logger = logging.getLogger(__name__)

# Sentinel that marks the seeded account as demo data, independent of name.
DEMO_META_ACCOUNT_ID = "DEMO-SEED"
DEMO_DISPLAY_NAME = "FTMO Demo $25K"
DEMO_BROKER_NAME = "TradePartna Demo"
DEMO_STARTING_BALANCE = 25_000.0


def _seed_for_user(user_id: uuid.UUID) -> int:
    """Stable per-user seed so two users don't get identical histories, while a
    given user's demo is reproducible (idempotent re-generation)."""
    return zlib.crc32(str(user_id).encode("utf-8"))


def get_demo_account(db: Session, user_id: uuid.UUID) -> TradingAccount | None:
    return db.execute(
        select(TradingAccount).where(
            TradingAccount.user_id == user_id,
            TradingAccount.meta_account_id == DEMO_META_ACCOUNT_ID,
            TradingAccount.is_deleted.is_(False),
        )
    ).scalar_one_or_none()


def _direction_enum(value: str) -> TradeDirection:
    return TradeDirection.buy if value == "buy" else TradeDirection.sell


def _session_enum(value: str) -> TradeSession:
    return TradeSession(value)


def _trade_to_row(spec: TradeSpec, account_id: uuid.UUID, idx: int) -> dict:
    """Map a generated TradeSpec to a Trade row dict (exact column names)."""
    return {
        "account_id": account_id,
        "broker_trade_id": f"demo-{idx:04d}",
        "symbol": spec.symbol,
        "direction": _direction_enum(spec.direction),
        "open_price": spec.open_price,
        "close_price": spec.close_price,
        "volume": spec.lots,
        "profit": spec.gross_profit,
        "commission": spec.commission,
        "swap": spec.swap,
        "net_profit": spec.net_profit,
        "duration_seconds": spec.duration_seconds,
        "session": _session_enum(spec.session),
        "opened_at": spec.open_time,
        "closed_at": spec.close_time,
        "sl": spec.sl,
        "tp": spec.tp,
        "magic_number": 0,
        "position_id": spec.position_id,
        "trade_source": TradeSource.personal,
        "is_manual": True,
        "is_missed": False,
        "setup": spec.setup,
        "plan_followed": spec.plan_followed,
    }


def seed_demo_account(
    db: Session,
    user_id: uuid.UUID,
    *,
    signup_date: date | None = None,
) -> TradingAccount | None:
    """Create + populate a demo account for the user. Idempotent: if a demo
    account already exists, returns it without duplicating. Uses the caller's
    open session and does NOT commit (the signup flow commits)."""
    existing = get_demo_account(db, user_id)
    if existing is not None:
        return existing

    signup_date = signup_date or datetime.now(timezone.utc).date()
    data: DemoData = generate_demo_data(
        seed=_seed_for_user(user_id),
        signup_date=signup_date,
        starting_balance=DEMO_STARTING_BALANCE,
    )

    account = TradingAccount(
        user_id=user_id,
        meta_account_id=DEMO_META_ACCOUNT_ID,
        broker_name=DEMO_BROKER_NAME,
        broker_login="0000000",
        broker_server="TradePartna-Demo",
        encrypted_investor_password="",  # no credentials — synthetic account
        account_type=TradingAccountType.demo,
        platform=TradingPlatform.mt5,
        currency="USD",
        timezone="UTC",
        broker_utc_offset=0,
        display_name=DEMO_DISPLAY_NAME,
        status=TradingAccountStatus.synced,
        provisioning_status=TradingAccountProvisioningStatus.provisioned,
        sync_provider=SyncProvider.csv_import,
        connection_state=TradingAccountConnectionState.ready,
        is_data_ready_for_stats=True,
        last_synced_at=datetime.now(timezone.utc),
    )
    db.add(account)
    db.flush()  # need account.id

    # Insert trades, keeping a TradeSpec→Trade map for the per-trade journals.
    trade_objs: list[tuple[Trade, TradeSpec]] = []
    idx = 0
    for day in data.days:
        for spec in day.trades:
            row = _trade_to_row(spec, account.id, idx)
            trade = Trade(**row)
            db.add(trade)
            trade_objs.append((trade, spec))
            idx += 1
    db.flush()

    # Daily journals: pre-write notes/moods on the flagged days; leave the rest
    # blank so the empty state is still visible. Discipline lives per-trade.
    for day in data.days:
        if not day.trades:
            continue
        if day.note_html or day.journaled:
            dj = DailyJournal(
                account_id=account.id,
                trading_date=day.day,
                note_html=day.note_html,
                note_updated_at=datetime.now(timezone.utc) if day.note_html else None,
                reviewed_at=datetime.now(timezone.utc) if day.journaled else None,
            )
            db.add(dj)

    # Per-trade discipline scores (1..5 scale on the model) derived from the
    # day's discipline_score (1..10) so the coach's read has truth to read.
    day_disc = {}
    for day in data.days:
        for spec in day.trades:
            day_disc[id(spec)] = day.discipline_score
    for trade, spec in trade_objs:
        score10 = day_disc.get(id(spec), 6)
        score5 = max(1, min(5, round(score10 / 2)))
        tj = TradeJournal(
            trade_id=trade.id,
            discipline_score=score5,
            setup_quality=5 if spec.plan_followed else 2,
            execution_quality=4 if spec.plan_followed else 2,
        )
        db.add(tj)

    db.flush()
    logger.info(
        "Seeded demo account %s for user %s: %d trades across %d active days",
        account.id, user_id, len(trade_objs),
        sum(1 for d in data.days if d.trades),
    )
    return account


def clear_demo_account(db: Session, user_id: uuid.UUID) -> bool:
    """Delete the user's demo account (cascades to trades + journals). Returns
    True if one was removed. Used on first real account connect / clear action."""
    account = get_demo_account(db, user_id)
    if account is None:
        return False
    db.delete(account)
    db.flush()
    logger.info("Cleared demo account %s for user %s", account.id, user_id)
    return True
