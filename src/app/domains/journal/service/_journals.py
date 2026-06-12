import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domains.accounts import repository as account_repo
from app.domains.journal import repository as journal_repo
from app.domains.journal.models import JournalMessageType
from app.domains.journal.schemas import (
    AdjacentTradedDatesResponse,
    DailyJournalResponse,
    DailyTradeChipResponse,
    JournalMessageResponse,
    JournalReviewedAtResponse,
    JournalTradeListResponse,
    JournalTradeResponse,
)
from app.domains.users.models import User
from app.shared.utils.storage import upload_journal_voice_note, upload_media
from app.shared.utils.timezone import local_date_to_utc_range, to_account_local_date

from ._helpers import (
    _build_trade_response,
    _estimate_starting_balance,
    _get_daily_journal_owned_by_user,
    _get_trade_owned_by_user,
    _resolve_day_balances,
    _serialize_message,
    extract_tags,
)


def get_or_create_trade_journal(db: Session, *, trade_id: uuid.UUID, current_user: User):
    trade, account = _get_trade_owned_by_user(db, trade_id=trade_id, current_user=current_user)

    trade_journal = journal_repo.get_trade_journal_by_trade_id(db, trade_id)
    is_new = False

    if trade_journal is None:
        local_trade_date = to_account_local_date(trade.closed_at, account.timezone)
        daily_journal = journal_repo.get_daily_journal_by_account_and_date(
            db, account_id=account.id, trading_date=local_trade_date,
        )
        if daily_journal is None:
            daily_journal = journal_repo.create_daily_journal(
                db, account_id=account.id, trading_date=local_trade_date,
            )

        trade_journal = journal_repo.create_trade_journal(
            db, trade_id=trade.id, daily_journal_id=daily_journal.id,
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
    image_storage_path = None
    image_mime_type = None
    image_original_filename = None

    if message_type == JournalMessageType.voice:
        if file is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Voice message file is required.",
            )
        audio_storage_path, _ = upload_journal_voice_note(file, user_prefix=str(current_user.id))
    elif message_type == JournalMessageType.image:
        if file is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Image file is required.",
            )
        image_storage_path, media_type, image_mime_type = upload_media(
            file=file, bucket=settings.JOURNAL_IMAGES_BUCKET, prefix=str(current_user.id),
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


def get_or_create_daily_journal(
    db: Session,
    *,
    account_id: uuid.UUID,
    trading_date,
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
        db, account_id=account_id, trading_date=trading_date,
    )
    if daily_journal is None:
        daily_journal = journal_repo.create_daily_journal(
            db, account_id=account_id, trading_date=trading_date,
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
    trade_ids = [trade.id for trade in trades]
    message_counts = journal_repo.get_trade_journal_message_counts(db, trade_ids=trade_ids)
    trade_reviewed_map = journal_repo.map_trade_reviewed_at_by_trade_ids(db, trade_ids=trade_ids)
    trade_rating_map = journal_repo.map_trade_journal_ratings_by_trade_ids(db, trade_ids=trade_ids)
    trade_assessments_map = journal_repo.map_trade_journal_assessments_by_trade_ids(db, trade_ids=trade_ids)

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
        db, account_id=account_id, trading_date=trading_date,
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
            db, account_id=account_id, closed_before_utc=closed_from_utc,
        )
        running_balance = est_equity + realized_before_local_day
        if day_start_balance is None:
            day_start_balance = running_balance

    trade_models: list[JournalTradeResponse] = []
    for trade in trades:
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
    trading_date,
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
    db: Session, *, daily_journal_id: uuid.UUID, current_user: User,
) -> JournalReviewedAtResponse:
    daily_journal, _ = _get_daily_journal_owned_by_user(
        db, daily_journal_id=daily_journal_id, current_user=current_user,
    )
    daily_journal.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(daily_journal)
    return JournalReviewedAtResponse(reviewed_at=daily_journal.reviewed_at)


def mark_trade_journal_reviewed(
    db: Session, *, trade_id: uuid.UUID, current_user: User,
) -> JournalReviewedAtResponse:
    trade_journal, _ = get_or_create_trade_journal(
        db, trade_id=trade_id, current_user=current_user
    )
    trade_journal.reviewed_at = datetime.now(timezone.utc)
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
        db, daily_journal_id=daily_journal_id, current_user=current_user,
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
        audio_storage_path, _ = upload_journal_voice_note(file, user_prefix=str(current_user.id))
    elif message_type == JournalMessageType.image:
        if file is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Image file is required.",
            )
        image_storage_path, media_type, image_mime_type = upload_media(
            file=file, bucket=settings.JOURNAL_IMAGES_BUCKET, prefix=str(current_user.id),
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
