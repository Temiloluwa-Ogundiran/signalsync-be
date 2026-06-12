"""
Shared pure helpers used by multiple service sub-modules.
No intra-package imports — safe to import from any sub-module.
"""
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from bisect import bisect_right
from app.domains.accounts import repository as account_repo
from app.domains.accounts.models import SyncProvider, TradingAccount
from app.domains.journal import repository as journal_repo
from app.domains.journal.schemas import (
    AnalyticsBalanceHistoryPointResponse,
    JournalAttachmentResponse,
    JournalMessageResponse,
    JournalOpenPositionResponse,
    JournalTradeResponse,
)
from app.domains.users.models import User
from app.shared.utils.storage import generate_signed_url
from app.shared.utils.timezone import local_date_to_utc_range, to_account_local_date

_HASHTAG_PATTERN = r"#([A-Za-z][A-Za-z0-9_-]*)"


def build_snapshot_lookup(db: Session, *, account_id: uuid.UUID) -> tuple[list[date], list[Decimal]]:
    snaps = journal_repo.list_account_snapshots(db, account_id=account_id, from_date=None, to_date=None)
    return [s.snapshot_date for s in snaps], [s.balance for s in snaps]


def latest_balance_on_or_before(lookup: tuple[list[date], list[Decimal]], target: date) -> Decimal | None:
    dates, balances = lookup
    idx = bisect_right(dates, target)
    return balances[idx - 1] if idx else None


def extract_tags(content: str | None) -> list[str]:
    if not content:
        return []
    return [match.lower() for match in re.findall(_HASHTAG_PATTERN, content)]



def _serialize_message(message, *, attachments: list) -> JournalMessageResponse:
    audio_url = None
    audio_url_expires_at = None

    if message.audio_storage_path:
        audio_url, audio_url_expires_at = generate_signed_url(
            bucket=settings.JOURNAL_VOICE_BUCKET,
            storage_path=message.audio_storage_path,
            expires_in=settings.VOICE_SIGNED_URL_TTL_SECONDS,
        )

    attachments_payload: list[JournalAttachmentResponse] = []
    for attachment in attachments:
        signed_url, signed_url_expires_at = generate_signed_url(
            bucket=settings.JOURNAL_IMAGES_BUCKET,
            storage_path=attachment.storage_path,
            expires_in=settings.IMAGE_SIGNED_URL_TTL_SECONDS,
        )
        attachments_payload.append(
            JournalAttachmentResponse(
                id=attachment.id,
                storage_path=attachment.storage_path,
                media_type=attachment.media_type,
                mime_type=attachment.mime_type,
                original_filename=attachment.original_filename,
                caption=attachment.caption,
                signed_url=signed_url,
                signed_url_expires_at=signed_url_expires_at,
            )
        )

    return JournalMessageResponse(
        id=message.id,
        daily_journal_id=message.daily_journal_id,
        trade_journal_id=message.trade_journal_id,
        author_id=message.author_id,
        message_type=message.message_type,
        content=message.content,
        tags=message.tags,
        system_data=message.system_data,
        audio_storage_path=message.audio_storage_path,
        audio_duration_seconds=message.audio_duration_seconds,
        audio_url=audio_url,
        audio_url_expires_at=audio_url_expires_at,
        attachments=attachments_payload,
        is_edited=message.is_edited,
        edited_at=message.edited_at,
        created_at=message.created_at,
    )


def _serialize_message_single(db: Session, message) -> JournalMessageResponse:
    attachments = journal_repo.list_attachments_by_message(db, message.id)
    return _serialize_message(message, attachments=attachments)


def _get_trade_owned_by_user(db: Session, *, trade_id: uuid.UUID, current_user: User):
    trade = account_repo.get_trade_by_id(db, trade_id)
    if trade is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found.")
    account = account_repo.get_account_by_id_for_user(db, trade.account_id, current_user.id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found.")
    return trade, account


def _get_daily_journal_owned_by_user(
    db: Session, *, daily_journal_id: uuid.UUID, current_user: User
):
    daily_journal = journal_repo.get_daily_journal_by_id(db, daily_journal_id)
    if daily_journal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Daily journal not found."
        )
    account = account_repo.get_account_by_id_for_user(
        db, daily_journal.account_id, current_user.id
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Daily journal not found."
        )
    return daily_journal, account


def _enrich_trade_response(model: JournalTradeResponse, trade) -> JournalTradeResponse:
    if trade.sl is not None and trade.sl != 0 and trade.open_price is not None:
        try:
            risk_per_unit = abs(float(trade.open_price) - float(trade.sl))
            if risk_per_unit > 0:
                model.r_multiple = float(trade.net_profit) / risk_per_unit
        except (TypeError, ZeroDivisionError):
            pass
    return model


def _build_trade_response(trade, *, account_timezone: str) -> JournalTradeResponse:
    model = _enrich_trade_response(JournalTradeResponse.model_validate(trade), trade)
    model.trading_date = to_account_local_date(trade.closed_at, account_timezone)
    return model


def _parse_optional_iso_datetime(value) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


def _build_open_position_response(position: dict) -> JournalOpenPositionResponse:
    return JournalOpenPositionResponse(
        position_id=str(position.get("position_id") or ""),
        symbol=str(position.get("symbol") or ""),
        side=str(position.get("side") or "buy"),
        volume=float(position.get("volume") or 0.0),
        floating_profit=float(position.get("profit") or 0.0),
        opened_at=_parse_optional_iso_datetime(position.get("opened_at")),
        open_price=float(position.get("price_open") or 0.0),
        current_price=float(position.get("price_current") or 0.0),
        sl=float(position["sl"]) if "sl" in position and position["sl"] not in (None, "") else None,
        tp=float(position["tp"]) if "tp" in position and position["tp"] not in (None, "") else None,
        magic=int(position["magic"]) if "magic" in position and position["magic"] not in (None, "") else None,
        comment=str(position.get("comment") or "") or None,
    )


def _estimate_starting_balance(db: Session, *, account: TradingAccount) -> Decimal:
    """Implied balance before any recorded trades: current_balance - sum(all net_profit)."""
    account_id = account.id
    all_time_realized = account_repo.sum_trade_net_profit(db, account_id=account_id)

    if account.sync_provider == SyncProvider.csv_import:
        earliest_snapshot = account_repo.get_earliest_snapshot(db, account_id)
        if earliest_snapshot is not None:
            return earliest_snapshot.balance
        return Decimal("0")

    snap_balance = account_repo.get_latest_account_snapshot_balance(db, account_id=account_id)
    if snap_balance is not None:
        return snap_balance - all_time_realized
    return Decimal("0")


def _resolve_day_balances(
    db: Session,
    *,
    account_id: uuid.UUID,
    trading_date: date,
) -> tuple[Decimal | None, Decimal | None]:
    prev_day = trading_date - timedelta(days=1)
    start_snapshot = account_repo.get_latest_snapshot_on_or_before_date(
        db, account_id=account_id, snapshot_date=prev_day
    )
    end_snapshot = account_repo.get_latest_snapshot_on_or_before_date(
        db, account_id=account_id, snapshot_date=trading_date
    )
    return (
        start_snapshot.balance if start_snapshot is not None else None,
        end_snapshot.balance if end_snapshot is not None else None,
    )


def _resolve_date_window(from_date: date | None, to_date: date | None, tz: str):
    start_utc = None
    end_utc = None
    if from_date is not None:
        start_utc, _ = local_date_to_utc_range(from_date, tz)
    if to_date is not None:
        _, end_utc = local_date_to_utc_range(to_date, tz)
    return start_utc, end_utc


def _resolve_multi_account_date_window(from_date: date | None, to_date: date | None):
    start_utc = None
    end_utc = None
    if from_date is not None:
        start_utc = datetime.combine(from_date, time.min, tzinfo=timezone.utc)
    if to_date is not None:
        end_utc = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start_utc, end_utc


def _get_account_or_404(db: Session, account_id: uuid.UUID, user_id: uuid.UUID):
    account = account_repo.get_account_by_id_for_user(db, account_id, user_id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found."
        )
    return account


def _get_ready_accounts_for_user(db: Session, user_id: uuid.UUID):
    accounts = account_repo.list_accounts_for_user(db, user_id)
    return [
        account
        for account in accounts
        if account.is_data_ready_for_stats
        and str(account.connection_state.value) == "ready"
    ]
