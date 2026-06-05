import re
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domains.accounts import repository as account_repo
from app.domains.accounts.schemas import ManualTradeCreateRequest, ManualTradeUpdateRequest
from app.domains.accounts.metaapi import metaapi_service
from app.domains.accounts.models import SyncProvider, TradingAccount
from app.domains.journal import repository as journal_repo
from app.domains.journal.models import JournalMessageType, JournalTemplateType
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
    AdjacentTradedDatesResponse,
    DailyJournalResponse,
    DailyTradeChipResponse,
    JournalAttachmentResponse,
    JournalReviewedAtResponse,
    JournalMessageResponse,
    JournalTemplateCreateRequest,
    JournalTradeListResponse,
    JournalTradeResponse,
)
from app.domains.users.models import User
from app.shared.utils.storage import (
    generate_signed_url,
    upload_journal_voice_note,
    upload_media,
)
from app.shared.utils.timezone import (
    local_date_to_utc_range,
    to_account_local_date,
)

_HASHTAG_PATTERN = r"#([A-Za-z][A-Za-z0-9_-]*)"


def extract_tags(content: str | None) -> list[str]:
    if not content:
        return []
    return [match.lower() for match in re.findall(_HASHTAG_PATTERN, content)]


def _serialize_message(db: Session, message) -> JournalMessageResponse:
    audio_url = None
    audio_url_expires_at = None

    if message.audio_storage_path:
        audio_url, audio_url_expires_at = generate_signed_url(
            bucket=settings.JOURNAL_VOICE_BUCKET,
            storage_path=message.audio_storage_path,
            expires_in=settings.VOICE_SIGNED_URL_TTL_SECONDS,
        )

    attachments_payload: list[JournalAttachmentResponse] = []
    attachments = journal_repo.list_attachments_by_message(db, message.id)
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


# ---------------------------------------------------------------------------
# Trade journal
# ---------------------------------------------------------------------------

def get_or_create_trade_journal(
    db: Session, *, trade_id: uuid.UUID, current_user: User
):
    trade, account = _get_trade_owned_by_user(db, trade_id=trade_id, current_user=current_user)

    trade_journal = journal_repo.get_trade_journal_by_trade_id(db, trade_id)
    is_new = False

    if trade_journal is None:
        local_trade_date = to_account_local_date(trade.closed_at, account.timezone)
        daily_journal = journal_repo.get_daily_journal_by_account_and_date(
            db,
            account_id=account.id,
            trading_date=local_trade_date,
        )
        if daily_journal is None:
            daily_journal = journal_repo.create_daily_journal(
                db,
                account_id=account.id,
                trading_date=local_trade_date,
            )

        trade_journal = journal_repo.create_trade_journal(
            db,
            trade_id=trade.id,
            daily_journal_id=daily_journal.id,
        )
        is_new = True

        journal_repo.create_message(
            db,
            daily_journal_id=None,
            trade_journal_id=trade_journal.id,
            author_id=None,
            message_type=JournalMessageType.system,
            content=None,
            tags=[],
            system_data={
                "symbol": trade.symbol,
                "direction": trade.direction.value,
                "net_profit": str(trade.net_profit),
                "open_price": str(trade.open_price),
                "close_price": str(trade.close_price),
                "volume": str(trade.volume),
                "duration_seconds": trade.duration_seconds,
                "session": trade.session.value,
                "opened_at": trade.opened_at.isoformat(),
                "closed_at": trade.closed_at.isoformat(),
            },
        )

    if is_new:
        db.commit()
        db.refresh(trade_journal)

    messages = journal_repo.list_messages_by_trade_journal(db, trade_journal.id)
    return trade_journal, [_serialize_message(db, m) for m in messages]


def create_trade_journal_message(
    db: Session,
    *,
    trade_id: uuid.UUID,
    current_user: User,
    message_type: JournalMessageType,
    content: str | None,
    file: UploadFile | None = None,
):
    if message_type in {
        JournalMessageType.system,
        JournalMessageType.prompt,
        JournalMessageType.ai_response,
    }:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported message type.",
        )

    trade_journal, _ = get_or_create_trade_journal(
        db, trade_id=trade_id, current_user=current_user
    )
    tags = extract_tags(content)

    audio_storage_path = None
    audio_mime_type = None
    image_storage_path = None
    image_mime_type = None
    image_original_filename = None

    if message_type == JournalMessageType.voice:
        if file is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Voice message file is required.",
            )
        audio_storage_path, audio_mime_type = upload_journal_voice_note(
            file, user_prefix=str(current_user.id)
        )
    elif message_type == JournalMessageType.image:
        if file is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Image file is required.",
            )
        image_storage_path, media_type, image_mime_type = upload_media(
            file=file,
            bucket=settings.JOURNAL_IMAGES_BUCKET,
            prefix=str(current_user.id),
        )
        if str(media_type.value) != "image":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only image files are allowed for image messages.",
            )
        image_original_filename = file.filename or None
    elif message_type == JournalMessageType.text:
        if not content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Text message content is required.",
            )

    message = journal_repo.create_message(
        db,
        daily_journal_id=None,
        trade_journal_id=trade_journal.id,
        author_id=current_user.id,
        message_type=message_type,
        content=content,
        tags=tags,
        audio_storage_path=audio_storage_path,
        audio_duration_seconds=None,
    )

    if message_type == JournalMessageType.image and image_storage_path and image_mime_type:
        journal_repo.create_attachment(
            db,
            message_id=message.id,
            storage_path=image_storage_path,
            media_type="image",
            mime_type=image_mime_type,
            original_filename=image_original_filename,
            caption=content,
        )

    if trade_journal.reviewed_at is None:
        trade_journal.reviewed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(message)
    return _serialize_message(db, message)


# ---------------------------------------------------------------------------
# Daily journal
# ---------------------------------------------------------------------------

def get_or_create_daily_journal(
    db: Session,
    *,
    account_id: uuid.UUID,
    trading_date: date,
    current_user: User,
    include_messages: bool = True,
    include_manual: bool = True,
) -> DailyJournalResponse:
    account = account_repo.get_account_by_id_for_user(db, account_id, current_user.id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found."
        )

    daily_journal = journal_repo.get_daily_journal_by_account_and_date(
        db,
        account_id=account_id,
        trading_date=trading_date,
    )
    if daily_journal is None:
        daily_journal = journal_repo.create_daily_journal(
            db,
            account_id=account_id,
            trading_date=trading_date,
        )
        db.commit()
        db.refresh(daily_journal)

    trades = account_repo.list_trades_by_account_local_date(
        db,
        account_id=account_id,
        trading_date=trading_date,
        account_timezone=account.timezone,
        include_manual=include_manual,
    )
    message_counts = journal_repo.get_trade_journal_message_counts(
        db,
        trade_ids=[trade.id for trade in trades],
    )
    trade_reviewed_map = journal_repo.map_trade_reviewed_at_by_trade_ids(
        db,
        trade_ids=[trade.id for trade in trades],
    )
    trade_rating_map = journal_repo.map_trade_journal_ratings_by_trade_ids(
        db,
        trade_ids=[trade.id for trade in trades],
    )
    trade_assessments_map = journal_repo.map_trade_journal_assessments_by_trade_ids(
        db,
        trade_ids=[trade.id for trade in trades],
    )

    trade_chips: list[DailyTradeChipResponse] = []
    for trade in trades:
        outcome = "breakeven"
        if trade.net_profit > Decimal("0"):
            outcome = "win"
        elif trade.net_profit < Decimal("0"):
            outcome = "loss"

        trade_chips.append(
            DailyTradeChipResponse(
                trade_id=trade.id,
                symbol=trade.symbol,
                direction=trade.direction.value,
                net_profit=trade.net_profit,
                outcome=outcome,
                journal_message_count=message_counts.get(trade.id, 0),
                is_manual=trade.is_manual,
                is_missed=trade.is_missed,
            )
        )

    messages: list[JournalMessageResponse] = []
    if include_messages:
        daily_messages = journal_repo.list_messages_by_daily_journal(db, daily_journal.id)
        messages = [_serialize_message(db, m) for m in daily_messages]

    gross_pnl = sum((trade.net_profit for trade in trades), Decimal("0"))
    day_start_balance, day_end_balance = _resolve_day_balances(
        db,
        account_id=account_id,
        trading_date=trading_date,
    )
    if day_end_balance is None and day_start_balance is not None:
        day_end_balance = day_start_balance + gross_pnl
    if day_start_balance is None and day_end_balance is not None:
        day_start_balance = day_end_balance - gross_pnl

    running_balance = day_start_balance
    if running_balance is None and trades:
        closed_from_utc, _ = local_date_to_utc_range(trading_date, account.timezone)
        est_equity = _estimate_starting_balance(db, account=account)
        realized_before_local_day = account_repo.sum_trade_net_profit(
            db,
            account_id=account_id,
            closed_before_utc=closed_from_utc,
        )
        running_balance = est_equity + realized_before_local_day
        if day_start_balance is None:
            day_start_balance = running_balance

    trade_models: list[JournalTradeResponse] = []
    for idx, trade in enumerate(trades):
        trade_model = _build_trade_response(trade, account_timezone=account.timezone)
        balance_before_trade = running_balance
        net_roi_percent = None
        if balance_before_trade is not None and balance_before_trade != 0:
            net_roi_percent = (trade.net_profit / balance_before_trade) * Decimal("100")
        elif day_start_balance is not None and day_start_balance > 0:
            net_roi_percent = (trade.net_profit / day_start_balance) * Decimal("100")
        trade_model.balance_before_trade = balance_before_trade
        trade_model.net_roi_percent = net_roi_percent
        trade_model.trade_reviewed_at = trade_reviewed_map.get(trade.id)
        trade_model.rating = trade_rating_map.get(trade.id)
        
        assess = trade_assessments_map.get(trade.id, {})
        trade_model.execution_quality = assess.get("execution_quality")
        trade_model.setup_quality = assess.get("setup_quality")
        trade_model.discipline_score = assess.get("discipline_score")
        
        trade_models.append(trade_model)
        if running_balance is not None:
            running_balance += trade.net_profit

    if day_end_balance is None and trades and running_balance is not None:
        day_end_balance = running_balance

    return DailyJournalResponse(
        id=daily_journal.id,
        trading_date=daily_journal.trading_date,
        account_timezone=account.timezone,
        reviewed_at=daily_journal.reviewed_at,
        day_start_balance=day_start_balance,
        day_end_balance=day_end_balance,
        trade_chips=trade_chips,
        trades=trade_models,
        messages=messages,
    )


def get_adjacent_traded_dates(
    db: Session,
    *,
    account_id: uuid.UUID,
    trading_date: date,
    current_user: User,
) -> AdjacentTradedDatesResponse:
    account = account_repo.get_account_by_id_for_user(db, account_id, current_user.id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found."
        )

    prev_date, next_date = journal_repo.adjacent_traded_local_dates(
        db,
        account_id=account_id,
        account_timezone=account.timezone,
        trading_date=trading_date,
    )
    return AdjacentTradedDatesResponse(prev_date=prev_date, next_date=next_date)


def mark_daily_journal_reviewed(
    db: Session,
    *,
    daily_journal_id: uuid.UUID,
    current_user: User,
) -> JournalReviewedAtResponse:
    daily_journal, _ = _get_daily_journal_owned_by_user(
        db,
        daily_journal_id=daily_journal_id,
        current_user=current_user,
    )
    now = datetime.now(timezone.utc)
    daily_journal.reviewed_at = now
    db.commit()
    db.refresh(daily_journal)
    return JournalReviewedAtResponse(reviewed_at=daily_journal.reviewed_at)


def mark_trade_journal_reviewed(
    db: Session,
    *,
    trade_id: uuid.UUID,
    current_user: User,
) -> JournalReviewedAtResponse:
    trade_journal, _ = get_or_create_trade_journal(
        db, trade_id=trade_id, current_user=current_user
    )
    now = datetime.now(timezone.utc)
    trade_journal.reviewed_at = now
    db.commit()
    db.refresh(trade_journal)
    return JournalReviewedAtResponse(reviewed_at=trade_journal.reviewed_at)


def create_daily_journal_message(
    db: Session,
    *,
    daily_journal_id: uuid.UUID,
    current_user: User,
    message_type: JournalMessageType,
    content: str | None,
    file: UploadFile | None = None,
):
    if message_type in {
        JournalMessageType.system,
        JournalMessageType.prompt,
        JournalMessageType.ai_response,
    }:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported message type.",
        )

    daily_journal, _ = _get_daily_journal_owned_by_user(
        db,
        daily_journal_id=daily_journal_id,
        current_user=current_user,
    )

    audio_storage_path = None
    image_storage_path = None
    image_mime_type = None
    image_original_filename = None

    if message_type == JournalMessageType.voice:
        if file is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Voice message file is required.",
            )
        audio_storage_path, _ = upload_journal_voice_note(
            file, user_prefix=str(current_user.id)
        )
    elif message_type == JournalMessageType.image:
        if file is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Image file is required.",
            )
        image_storage_path, media_type, image_mime_type = upload_media(
            file=file,
            bucket=settings.JOURNAL_IMAGES_BUCKET,
            prefix=str(current_user.id),
        )
        if str(media_type.value) != "image":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only image files are allowed for image messages.",
            )
        image_original_filename = file.filename or None
    elif message_type == JournalMessageType.text and not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Text message content is required.",
        )

    message = journal_repo.create_message(
        db,
        daily_journal_id=daily_journal.id,
        trade_journal_id=None,
        author_id=current_user.id,
        message_type=message_type,
        content=content,
        tags=extract_tags(content),
        audio_storage_path=audio_storage_path,
        audio_duration_seconds=None,
    )

    if message_type == JournalMessageType.image and image_storage_path and image_mime_type:
        journal_repo.create_attachment(
            db,
            message_id=message.id,
            storage_path=image_storage_path,
            media_type="image",
            mime_type=image_mime_type,
            original_filename=image_original_filename,
            caption=content,
        )

    if daily_journal.reviewed_at is None:
        daily_journal.reviewed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(message)
    return _serialize_message(db, message)


def update_message(
    db: Session,
    *,
    message_id: uuid.UUID,
    current_user: User,
    content: str | None,
):
    message = journal_repo.get_message_by_id(db, message_id)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")

    if message.author_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed.")

    if message.message_type in {JournalMessageType.system, JournalMessageType.prompt}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Message cannot be edited."
        )

    updated = journal_repo.update_message_content(
        db, message, content=content, tags=extract_tags(content)
    )
    db.commit()
    db.refresh(updated)
    return _serialize_message(db, updated)


def delete_message(db: Session, *, message_id: uuid.UUID, current_user: User) -> None:
    message = journal_repo.get_message_by_id(db, message_id)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")

    if message.author_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed.")

    if message.message_type in {JournalMessageType.system, JournalMessageType.prompt}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Message cannot be deleted."
        )

    journal_repo.delete_message(db, message)
    db.commit()


def list_daily_journal_feed(
    db: Session,
    *,
    account_id: uuid.UUID,
    current_user: User,
    limit: int,
    cursor_daily_journal_id: uuid.UUID | None,
):
    account = account_repo.get_account_by_id_for_user(db, account_id, current_user.id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found."
        )

    return journal_repo.list_daily_journal_feed(
        db,
        account_id=account_id,
        limit=limit,
        cursor_daily_journal_id=cursor_daily_journal_id,
    )


# ---------------------------------------------------------------------------
# Trade response helpers
# ---------------------------------------------------------------------------


def _enrich_trade_response(model: JournalTradeResponse, trade) -> JournalTradeResponse:
    """
    Compute derived fields (R-multiple) on a JournalTradeResponse.

    R-multiple = net_profit / risk_per_unit, where
    risk_per_unit = |open_price - sl|.

    Only computed when the SL field is present and non-zero, to avoid
    division by zero on MetaAPI-sourced trades that have no SL data.
    """
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


# ---------------------------------------------------------------------------
# Trade list
# ---------------------------------------------------------------------------

def list_account_trades(
    db: Session,
    *,
    current_user: User,
    account_id: uuid.UUID,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    symbol: Optional[str] = None,
    direction=None,
    session=None,
    limit: int = 50,
    cursor_trade_id: Optional[uuid.UUID] = None,
    include_manual: bool = True,
) -> list[JournalTradeResponse]:
    account = account_repo.get_account_by_id_for_user(db, account_id, current_user.id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found."
        )

    closed_from_utc = None
    closed_to_utc_exclusive = None

    if from_date is not None:
        closed_from_utc, _ = local_date_to_utc_range(from_date, account.timezone)

    if to_date is not None:
        _, to_date_end_exclusive = local_date_to_utc_range(to_date, account.timezone)
        closed_to_utc_exclusive = to_date_end_exclusive

    trades = account_repo.list_trades_by_account(
        db,
        account_id=account_id,
        closed_from_utc=closed_from_utc,
        closed_to_utc_exclusive=closed_to_utc_exclusive,
        symbol=symbol,
        direction=direction,
        session=session,
        limit=limit,
        cursor_trade_id=cursor_trade_id,
        include_manual=include_manual,
    )

    if not trades:
        return []

    base_models = [
        _build_trade_response(trade, account_timezone=account.timezone)
        for trade in trades
    ]
    trade_reviewed_map = journal_repo.map_trade_reviewed_at_by_trade_ids(
        db,
        trade_ids=[trade.id for trade in trades],
    )
    trade_rating_map = journal_repo.map_trade_journal_ratings_by_trade_ids(
        db,
        trade_ids=[trade.id for trade in trades],
    )
    trade_assessments_map = journal_repo.map_trade_journal_assessments_by_trade_ids(
        db,
        trade_ids=[trade.id for trade in trades],
    )
    for model in base_models:
        model.trade_reviewed_at = trade_reviewed_map.get(model.id)
        model.rating = trade_rating_map.get(model.id)
        
        assess = trade_assessments_map.get(model.id, {})
        model.execution_quality = assess.get("execution_quality")
        model.setup_quality = assess.get("setup_quality")
        model.discipline_score = assess.get("discipline_score")

    if closed_from_utc is None:
        return base_models

    starting_balance = _estimate_starting_balance(db, account=account)
    realized_before_window = account_repo.sum_trade_net_profit(
        db,
        account_id=account.id,
        closed_before_utc=closed_from_utc,
    )
    running_balance = starting_balance + realized_before_window

    roi_by_trade_id: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    for trade in sorted(trades, key=lambda item: (item.closed_at, item.id)):
        balance_before_trade = running_balance
        net_roi_percent = Decimal("0")
        if balance_before_trade != 0:
            net_roi_percent = (trade.net_profit / balance_before_trade) * Decimal("100")

        roi_by_trade_id[trade.id] = (balance_before_trade, net_roi_percent)
        running_balance += trade.net_profit

    for model in base_models:
        balance_before, roi = roi_by_trade_id.get(model.id, (None, None))
        model.balance_before_trade = balance_before
        model.net_roi_percent = roi

    return base_models


def _estimate_starting_balance(db: Session, *, account: TradingAccount) -> Decimal:
    """Implied balance before any recorded trades: current_balance - sum(all net_profit)."""
    account_id = account.id
    all_time_realized = account_repo.sum_trade_net_profit(db, account_id=account_id)

    if account.sync_provider == SyncProvider.csv_import:
        from app.domains.accounts.models import AccountSnapshot
        earliest_snapshot = (
            db.query(AccountSnapshot)
            .filter(AccountSnapshot.account_id == account_id)
            .order_by(AccountSnapshot.snapshot_date.asc())
            .first()
        )
        if earliest_snapshot is not None:
            return earliest_snapshot.balance
        return Decimal("0")

    if account.sync_provider == SyncProvider.headless_mt5:
        snap_balance = account_repo.get_latest_account_snapshot_balance(
            db, account_id=account_id
        )
        if snap_balance is not None:
            return snap_balance - all_time_realized
        return Decimal("0")

    try:
        account_info = metaapi_service.get_account_info(account.meta_account_id)
        current_balance = Decimal(str(account_info.get("balance") or 0))
        return current_balance - all_time_realized
    except Exception:  # noqa: BLE001
        snap_balance = account_repo.get_latest_account_snapshot_balance(
            db, account_id=account_id
        )
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
    start_snapshot = journal_repo.get_latest_account_snapshot_on_or_before(
        db,
        account_id=account_id,
        snapshot_date=prev_day,
    )
    end_snapshot = journal_repo.get_latest_account_snapshot_on_or_before(
        db,
        account_id=account_id,
        snapshot_date=trading_date,
    )
    day_start_balance = start_snapshot.balance if start_snapshot is not None else None
    day_end_balance = end_snapshot.balance if end_snapshot is not None else None
    return day_start_balance, day_end_balance


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def list_templates(
    db: Session,
    *,
    current_user: User,
    template_type: JournalTemplateType | None,
):
    return journal_repo.list_templates_visible_to_user(
        db,
        user_id=current_user.id,
        template_type=template_type,
    )


def create_template(
    db: Session,
    *,
    current_user: User,
    payload: JournalTemplateCreateRequest,
):
    template = journal_repo.create_template(
        db,
        owner_id=current_user.id,
        name=payload.name,
        template_type=payload.template_type,
        questions=[q.model_dump() for q in payload.questions],
    )
    db.commit()
    db.refresh(template)
    return template


def delete_template(
    db: Session, *, current_user: User, template_id: uuid.UUID
) -> None:
    template = journal_repo.get_template_visible_to_user(
        db,
        template_id=template_id,
        user_id=current_user.id,
    )
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Template not found."
        )

    deleted = journal_repo.delete_user_template(
        db, template=template, user_id=current_user.id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete this template."
        )

    db.commit()


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def _resolve_date_window(from_date: date | None, to_date: date | None, tz: str):
    start_utc = None
    end_utc = None

    if from_date is not None:
        start_utc, _ = local_date_to_utc_range(from_date, tz)

    if to_date is not None:
        _, end_utc = local_date_to_utc_range(to_date, tz)

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


def _resolve_multi_account_date_window(from_date: date | None, to_date: date | None):
    start_utc = None
    end_utc = None

    if from_date is not None:
        start_utc = datetime.combine(from_date, time.min, tzinfo=timezone.utc)

    if to_date is not None:
        end_utc = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=timezone.utc)

    return start_utc, end_utc


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
        db,
        account_id=account_id,
        closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
        include_manual=include_manual,
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

    by_day: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for t in trades:
        by_day[t.closed_at.date()] += t.net_profit

    best_day = None
    worst_day = None
    if by_day:
        best_date, best_pnl = max(by_day.items(), key=lambda item: item[1])
        worst_date, worst_pnl = min(by_day.items(), key=lambda item: item[1])
        best_day = AnalyticsBestWorstDay(date=best_date, pnl=best_pnl)
        worst_day = AnalyticsBestWorstDay(date=worst_date, pnl=worst_pnl)

    starting_balance = _estimate_starting_balance(db, account=account)

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
            db,
            account_id=account_id,
            account_timezone=account.timezone,
            from_date=jd_from,
            to_date=jd_to,
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
                has_journal_activity=row.trading_date in journal_active_dates,
            )
        )

    month_value = from_date.strftime("%Y-%m") if from_date else "all"
    return AnalyticsCalendarResponse(month=month_value, days=days)


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
        db,
        account_id=account_id,
        closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
        include_manual=include_manual,
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
        db,
        account_id=account_id,
        closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
        include_manual=include_manual,
    )

    grouped: dict = defaultdict(list)
    for t in trades:
        grouped[t.symbol].append(t)

    items = []
    for symbol, symbol_trades in grouped.items():
        trade_count = len(symbol_trades)
        wins = sum(1 for t in symbol_trades if t.net_profit > 0)
        total_pnl = sum((t.net_profit for t in symbol_trades), Decimal("0"))

        # MFE/MAE averages — only for trades that have MT5 enrichment data.
        mfe_values = [float(t.mfe) for t in symbol_trades if t.mfe is not None]
        mae_values = [float(t.mae) for t in symbol_trades if t.mae is not None]
        avg_mfe = (sum(mfe_values) / len(mfe_values)) if mfe_values else None
        avg_mae = (sum(mae_values) / len(mae_values)) if mae_values else None

        items.append(
            AnalyticsInstrumentItemResponse(
                symbol=symbol,
                trade_count=trade_count,
                win_rate=(wins / trade_count) * 100 if trade_count else 0.0,
                total_pnl=float(total_pnl),
                avg_pnl=float(total_pnl / trade_count) if trade_count else 0.0,
                avg_mfe=avg_mfe,
                avg_mae=avg_mae,
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
        db,
        account_id=account_id,
        closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
        include_manual=include_manual,
    )

    hourly_groups: dict[str, list] = defaultdict(list)
    weekday_groups: dict[str, list] = defaultdict(list)
    weekday_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    account_zone = ZoneInfo(account.timezone)
    for t in trades:
        basis_time = t.opened_at if time_basis == "open" else t.closed_at
        closed_at_utc = (
            basis_time
            if basis_time.tzinfo is not None
            else basis_time.replace(tzinfo=timezone.utc)
        )
        local_closed_at = closed_at_utc.astimezone(account_zone)
        hour_bucket = f"{local_closed_at.hour:02d}"
        weekday_bucket = weekday_order[local_closed_at.weekday()]
        hourly_groups[hour_bucket].append(t)
        weekday_groups[weekday_bucket].append(t)

    def build_point(bucket: str, bucket_trades: list) -> AnalyticsTimePerformancePointResponse:
        trade_count = len(bucket_trades)
        wins = sum(1 for trade in bucket_trades if trade.net_profit > 0)
        total_pnl = sum((trade.net_profit for trade in bucket_trades), Decimal("0"))
        return AnalyticsTimePerformancePointResponse(
            bucket=bucket,
            trade_count=trade_count,
            total_pnl=float(total_pnl),
            win_rate=(wins / trade_count) * 100 if trade_count else 0.0,
            avg_pnl=float(total_pnl / trade_count) if trade_count else 0.0,
        )

    hourly = [
        build_point(f"{hour:02d}", hourly_groups.get(f"{hour:02d}", []))
        for hour in range(24)
    ]
    daily = [build_point(day, weekday_groups.get(day, [])) for day in weekday_order]

    return AnalyticsTimePerformanceResponse(hourly=hourly, daily=daily)


def get_analytics_equity(
    db: Session,
    *,
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
) -> AnalyticsEquityResponse:
    _get_account_or_404(db, account_id, user_id)
    points = journal_repo.list_account_snapshots(
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


def _balance_history_points_from_trade_closes(
    db: Session,
    *,
    account_id: uuid.UUID,
    account: TradingAccount,
    from_date: date,
    to_date: date,
    include_manual: bool = True,
) -> list[AnalyticsBalanceHistoryPointResponse]:
    """
    Per-trade closing equity in range: day anchor + balance after each close
    (same reconstruction as journal day balance curve, multi-day window).

    When snapshot-based day opens are missing, seed from implied equity before
    the first close in the window (estimate + realized PnL before that close)
    and carry running balance across local days so the series is account equity,
    not cumulative PnL from zero.
    """
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = journal_repo.list_trades_filtered(
        db,
        account_id=account_id,
        closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
        include_manual=include_manual,
    )
    if not trades:
        return []

    sorted_trades = sorted(trades, key=lambda t: (t.closed_at, t.id))
    first_in_window = None
    for t in sorted_trades:
        ld0 = to_account_local_date(t.closed_at, account.timezone)
        if from_date <= ld0 <= to_date:
            first_in_window = t
            break
    if first_in_window is None:
        return []

    seed_before_first = _estimate_starting_balance(db, account=account) + account_repo.sum_trade_net_profit(
        db,
        account_id=account_id,
        closed_before_utc=first_in_window.closed_at,
    )

    points: list[AnalyticsBalanceHistoryPointResponse] = []
    last_local_date: date | None = None
    running: Decimal | None = None

    for trade in sorted_trades:
        ld = to_account_local_date(trade.closed_at, account.timezone)
        if ld < from_date or ld > to_date:
            continue

        if ld != last_local_date:
            ds, _ = _resolve_day_balances(
                db,
                account_id=account_id,
                trading_date=ld,
            )
            if ds is not None:
                running = ds
            elif last_local_date is None:
                running = seed_before_first
            # else: carry `running` from previous local day (end-of-day equity)
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
            db,
            account_id=account_id,
            trading_date=from_date,
            account_timezone=account.timezone,
            include_manual=include_manual,
        )
        day_start_balance, _ = _resolve_day_balances(
            db,
            account_id=account_id,
            trading_date=from_date,
        )
        if day_start_balance is not None:
            running = day_start_balance
        else:
            closed_from_utc, _ = local_date_to_utc_range(from_date, account.timezone)
            est = _estimate_starting_balance(db, account=account)
            realized_before_day = account_repo.sum_trade_net_profit(
                db,
                account_id=account_id,
                closed_before_utc=closed_from_utc,
            )
            running = est + realized_before_day
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
                    timestamp=trade.closed_at,
                    balance=float(running),
                    equity=None,
                    source="trade_close",
                )
            )
        return AnalyticsBalanceHistoryResponse(points=points)

    snapshots = journal_repo.list_account_snapshots(
        db,
        account_id=account_id,
        from_date=from_date,
        to_date=to_date,
    )
    snapshot_points: list[AnalyticsBalanceHistoryPointResponse] = []
    if snapshots:
        snapshot_points = [
            AnalyticsBalanceHistoryPointResponse(
                timestamp=datetime.combine(
                    p.snapshot_date, time.min, tzinfo=timezone.utc
                ),
                balance=float(p.balance),
                equity=float(p.equity),
                source="daily_snapshot",
            )
            for p in snapshots
        ]

    if from_date is None or to_date is None:
        return AnalyticsBalanceHistoryResponse(points=snapshot_points)

    trade_points = _balance_history_points_from_trade_closes(
        db,
        account_id=account_id,
        account=account,
        from_date=from_date,
        to_date=to_date,
        include_manual=include_manual,
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
        db,
        account_id=account_id,
        closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
        include_manual=include_manual,
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
    """
    Break down performance by trade source (personal vs copied).

    Only meaningful for accounts synced via the headless MT5 service.
    For MetaAPI-sourced accounts, all trades will have trade_source=None
    and will appear under the 'unknown' bucket.
    """
    account = _get_account_or_404(db, account_id, user_id)
    start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
    trades = journal_repo.list_trades_filtered(
        db,
        account_id=account_id,
        closed_from_utc=start_utc,
        closed_to_utc_exclusive=end_utc,
        include_manual=include_manual,
    )

    grouped: dict = defaultdict(list)
    for t in trades:
        source_key = t.trade_source.value if t.trade_source else "unknown"
        grouped[source_key].append(t)

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
        summary=get_analytics_summary(
            db, account_id=account_id, user_id=user_id, from_date=from_date, to_date=to_date, include_manual=include_manual
        ),
        sessions=get_analytics_sessions(
            db, account_id=account_id, user_id=user_id, from_date=from_date, to_date=to_date, include_manual=include_manual
        ),
        instruments=get_analytics_instruments(
            db, account_id=account_id, user_id=user_id, from_date=from_date, to_date=to_date, include_manual=include_manual
        ),
        setups=get_analytics_setups(
            db, account_id=account_id, user_id=user_id, from_date=from_date, to_date=to_date, include_manual=include_manual
        ),
        trade_sources=get_analytics_trade_sources(
            db, account_id=account_id, user_id=user_id, from_date=from_date, to_date=to_date, include_manual=include_manual
        ),
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
    selected_accounts = []
    account_timezone = "UTC"
    starting_balance = Decimal("0")

    if account_id is not None:
        account = _get_account_or_404(db, account_id, user_id)
        selected_accounts = [account]
        account_timezone = account.timezone
        start_utc, end_utc = _resolve_date_window(from_date, to_date, account.timezone)
        trades = journal_repo.list_trades_filtered(
            db,
            account_id=account_id,
            closed_from_utc=start_utc,
            closed_to_utc_exclusive=end_utc,
            include_manual=include_manual,
        )
        starting_balance = _estimate_starting_balance(db, account=account)
    else:
        selected_accounts = _get_ready_accounts_for_user(db, user_id)
        account_ids = [account.id for account in selected_accounts]
        start_utc, end_utc = _resolve_multi_account_date_window(from_date, to_date)
        trades = journal_repo.list_trades_filtered_multi(
            db,
            account_ids=account_ids,
            closed_from_utc=start_utc,
            closed_to_utc_exclusive=end_utc,
            include_manual=include_manual,
        )
        if selected_accounts:
            starting_balance = sum(
                (_estimate_starting_balance(db, account=ready_account) for ready_account in selected_accounts),
                Decimal("0"),
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
    by_day: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for t in trades:
        cumulative += t.net_profit
        running_max = max(running_max, cumulative)
        drawdown = running_max - cumulative
        max_drawdown = max(max_drawdown, drawdown)
        if account_id is not None:
            local_day = to_account_local_date(t.closed_at, account_timezone)
        else:
            local_day = (
                t.closed_at.astimezone(timezone.utc).date()
                if t.closed_at.tzinfo is not None
                else t.closed_at.date()
            )
        by_day[local_day] += t.net_profit

    best_day = None
    worst_day = None
    if by_day:
        best_date, best_pnl = max(by_day.items(), key=lambda item: item[1])
        worst_date, worst_pnl = min(by_day.items(), key=lambda item: item[1])
        best_day = AnalyticsBestWorstDay(date=best_date, pnl=best_pnl)
        worst_day = AnalyticsBestWorstDay(date=worst_date, pnl=worst_pnl)

    net_pnl_percent = (
        float((total_net_pnl / starting_balance) * Decimal("100"))
        if starting_balance != 0
        else 0.0
    )
    summary = AnalyticsSummaryResponse(
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

    calendar_map: dict[date, dict[str, int | Decimal]] = {}
    for trade in trades:
        if account_id is not None:
            local_day = to_account_local_date(trade.closed_at, account_timezone)
        else:
            local_day = (
                trade.closed_at.astimezone(timezone.utc).date()
                if trade.closed_at.tzinfo is not None
                else trade.closed_at.date()
            )
        if local_day not in calendar_map:
            calendar_map[local_day] = {
                "trade_count": 0,
                "total_pnl": Decimal("0"),
                "win_count": 0,
                "loss_count": 0,
            }
        bucket = calendar_map[local_day]
        bucket["trade_count"] = int(bucket["trade_count"]) + 1
        bucket["total_pnl"] = Decimal(bucket["total_pnl"]) + trade.net_profit
        if trade.net_profit > 0:
            bucket["win_count"] = int(bucket["win_count"]) + 1
        elif trade.net_profit < 0:
            bucket["loss_count"] = int(bucket["loss_count"]) + 1

    journal_active_dates: set[date] = set()
    if account_id is not None and calendar_map:
        jd_from = min(calendar_map.keys())
        jd_to = max(calendar_map.keys())
        journal_active_dates = journal_repo.list_trading_dates_with_journal_activity(
            db,
            account_id=account_id,
            account_timezone=account_timezone,
            from_date=jd_from,
            to_date=jd_to,
        )

    calendar_days = []
    for trading_day in sorted(calendar_map.keys()):
        row = calendar_map[trading_day]
        total_pnl_for_day = float(Decimal(row["total_pnl"]))
        outcome = "breakeven"
        if total_pnl_for_day > 0:
            outcome = "win"
        elif total_pnl_for_day < 0:
            outcome = "loss"
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
        month=from_date.strftime("%Y-%m") if from_date else "all",
        days=calendar_days,
    )

    symbol_groups: dict[str, list] = defaultdict(list)
    for trade in trades:
        symbol_groups[trade.symbol].append(trade)
    instruments_items = []
    for symbol, symbol_trades in symbol_groups.items():
        trade_count = len(symbol_trades)
        symbol_wins = sum(1 for t in symbol_trades if t.net_profit > 0)
        symbol_total_pnl = sum((t.net_profit for t in symbol_trades), Decimal("0"))
        mfe_values = [float(t.mfe) for t in symbol_trades if t.mfe is not None]
        mae_values = [float(t.mae) for t in symbol_trades if t.mae is not None]
        instruments_items.append(
            AnalyticsInstrumentItemResponse(
                symbol=symbol,
                trade_count=trade_count,
                win_rate=(symbol_wins / trade_count) * 100 if trade_count else 0.0,
                total_pnl=float(symbol_total_pnl),
                avg_pnl=float(symbol_total_pnl / trade_count) if trade_count else 0.0,
                avg_mfe=(sum(mfe_values) / len(mfe_values)) if mfe_values else None,
                avg_mae=(sum(mae_values) / len(mae_values)) if mae_values else None,
            )
        )
    instruments_items.sort(key=lambda x: x.total_pnl, reverse=True)
    instruments = AnalyticsInstrumentsResponse(instruments=instruments_items)

    hourly_groups: dict[str, list] = defaultdict(list)
    weekday_groups: dict[str, list] = defaultdict(list)
    weekday_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    account_zone = ZoneInfo(account_timezone)
    for trade in trades:
        basis_time = trade.opened_at if time_basis == "open" else trade.closed_at
        closed_at_utc = (
            basis_time
            if basis_time.tzinfo is not None
            else basis_time.replace(tzinfo=timezone.utc)
        )
        local_closed_at = closed_at_utc.astimezone(account_zone)
        hour_bucket = f"{local_closed_at.hour:02d}"
        weekday_bucket = weekday_order[local_closed_at.weekday()]
        hourly_groups[hour_bucket].append(trade)
        weekday_groups[weekday_bucket].append(trade)

    def build_point(bucket: str, bucket_trades: list) -> AnalyticsTimePerformancePointResponse:
        bucket_count = len(bucket_trades)
        bucket_wins = sum(1 for item in bucket_trades if item.net_profit > 0)
        bucket_pnl = sum((item.net_profit for item in bucket_trades), Decimal("0"))
        return AnalyticsTimePerformancePointResponse(
            bucket=bucket,
            trade_count=bucket_count,
            total_pnl=float(bucket_pnl),
            win_rate=(bucket_wins / bucket_count) * 100 if bucket_count else 0.0,
            avg_pnl=float(bucket_pnl / bucket_count) if bucket_count else 0.0,
        )

    time_performance = AnalyticsTimePerformanceResponse(
        hourly=[build_point(f"{hour:02d}", hourly_groups.get(f"{hour:02d}", [])) for hour in range(24)],
        daily=[build_point(day, weekday_groups.get(day, [])) for day in weekday_order],
    )

    recent_sorted = sorted(trades, key=lambda item: (item.closed_at, item.id), reverse=True)
    recent_models = [
        _build_trade_response(trade, account_timezone=account.timezone)
        for trade in recent_sorted[:recent_limit]
    ]
    recent_trades = JournalTradeListResponse(items=recent_models, next_cursor=None)

    return AnalyticsDashboardResponse(
        summary=summary,
        calendar=calendar,
        instruments=instruments,
        time_performance=time_performance,
        recent_trades=recent_trades,
    )


# ---------------------------------------------------------------------------
# Startup seeding
# ---------------------------------------------------------------------------

def seed_system_journal_templates(db: Session) -> dict[str, int]:
    created, skipped = journal_repo.seed_system_templates(db)

    if created:
        db.commit()

    return {
        "created": created,
        "skipped_existing": skipped,
    }


def create_manual_trade(
    db: Session,
    *,
    account_id: uuid.UUID,
    payload: ManualTradeCreateRequest,
    current_user: User,
):
    # 1. Create trade in account repo
    trade = account_repo.create_manual_trade(db, account_id=account_id, payload=payload)
    
    # 2. Rebuild daily stats for the day so manual trade stats are updated
    local_trade_date = to_account_local_date(trade.closed_at, trade.account.timezone)
    account_repo.rebuild_daily_stats_for_date(
        db,
        account_id=account_id,
        trading_date=local_trade_date,
        account_timezone=trade.account.timezone,
    )
    
    # 3. Create/get journal structure so chat environment is ready
    get_or_create_trade_journal(db, trade_id=trade.id, current_user=current_user)
    
    db.commit()
    db.refresh(trade)
    
    return trade


def update_manual_trade(
    db: Session,
    *,
    trade_id: uuid.UUID,
    payload: ManualTradeUpdateRequest,
    current_user: User,
):
    # 1. Retrieve old trade/day before updating to rebuild daily stats later
    old_trade = account_repo.get_trade_by_id(db, trade_id)
    if not old_trade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found.")
    
    old_local_date = to_account_local_date(old_trade.closed_at, old_trade.account.timezone)
    account_id = old_trade.account_id
    account_timezone = old_trade.account.timezone
    
    # 2. Update trade via repository
    try:
        trade = account_repo.update_manual_trade(db, trade_id=trade_id, user_id=current_user.id, payload=payload)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        
    # 3. Rebuild daily stats for both old date and new date
    new_local_date = to_account_local_date(trade.closed_at, account_timezone)
    
    account_repo.rebuild_daily_stats_for_date(
        db,
        account_id=account_id,
        trading_date=old_local_date,
        account_timezone=account_timezone,
    )
    
    if new_local_date != old_local_date:
        account_repo.rebuild_daily_stats_for_date(
            db,
            account_id=account_id,
            trading_date=new_local_date,
            account_timezone=account_timezone,
        )
        
    # 4. If there is a system message inside the trade journal, update it
    trade_journal = journal_repo.get_trade_journal_by_trade_id(db, trade_id)
    if trade_journal:
        messages = journal_repo.list_messages_by_trade_journal(db, trade_journal.id)
        system_msgs = [m for m in messages if m.message_type == JournalMessageType.system]
        if system_msgs:
            sys_msg = system_msgs[0]
            sys_msg.system_data = {
                "symbol": trade.symbol,
                "direction": trade.direction.value,
                "net_profit": str(trade.net_profit),
                "open_price": str(trade.open_price),
                "close_price": str(trade.close_price),
                "volume": str(trade.volume),
                "duration_seconds": trade.duration_seconds,
                "session": trade.session.value,
                "opened_at": trade.opened_at.isoformat(),
                "closed_at": trade.closed_at.isoformat(),
            }
            
    db.commit()
    db.refresh(trade)
    return trade


def delete_manual_trade(
    db: Session,
    *,
    trade_id: uuid.UUID,
    current_user: User,
):
    old_trade = account_repo.get_trade_by_id(db, trade_id)
    if not old_trade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found.")
        
    old_local_date = to_account_local_date(old_trade.closed_at, old_trade.account.timezone)
    account_id = old_trade.account_id
    account_timezone = old_trade.account.timezone
    
    try:
        account_repo.delete_manual_trade(db, trade_id=trade_id, user_id=current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        
    account_repo.rebuild_daily_stats_for_date(
        db,
        account_id=account_id,
        trading_date=old_local_date,
        account_timezone=account_timezone,
    )
    db.commit()
