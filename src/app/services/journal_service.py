import re
import uuid
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.models.journal_message import JournalMessageType
from app.models.user import User
from app.core.config import settings
from app.core.supabase import get_supabase
from app.repositories import daily_journal_repo, journal_attachment_repo, journal_message_repo, trade_journal_repo, trade_repo, trading_account_repo
from app.schemas.journal_daily import DailyJournalResponse, DailyTradeChipResponse
from app.schemas.journal_message import JournalAttachmentResponse, JournalMessageResponse
from app.utils.storage import generate_signed_url, upload_media
from app.utils.timezone import to_account_local_date

_HASHTAG_PATTERN = r"#([A-Za-z][A-Za-z0-9_-]*)"
_ALLOWED_AUDIO_MIME_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/m4a",
    "audio/wav",
    "audio/x-wav",
    "audio/webm",
    "audio/ogg",
}


def extract_tags(content: str | None) -> list[str]:
    if not content:
        return []
    return [match.lower() for match in re.findall(_HASHTAG_PATTERN, content)]


def _upload_voice_note(file: UploadFile, *, user_prefix: str) -> tuple[str, str]:
    content_type = (file.content_type or "").lower()
    if content_type not in _ALLOWED_AUDIO_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported audio format for voice message.",
        )

    ext = "bin"
    if "/" in content_type:
        ext = content_type.split("/")[1].replace("x-", "")

    storage_path = f"{user_prefix}/{uuid.uuid4()}.{ext}"
    file_bytes = file.file.read()

    supabase = get_supabase()
    supabase.storage.from_(settings.JOURNAL_VOICE_BUCKET).upload(
        path=storage_path,
        file=file_bytes,
        file_options={"content-type": content_type, "upsert": "false"},
    )
    return storage_path, content_type


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
    attachments = journal_attachment_repo.list_by_message_id(db, message.id)
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
    trade = trade_repo.get_by_id(db, trade_id)
    if trade is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found.")

    account = trading_account_repo.get_by_id_for_user(db, trade.account_id, current_user.id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found.")

    return trade, account


def _get_daily_journal_owned_by_user(db: Session, *, daily_journal_id: uuid.UUID, current_user: User):
    daily_journal = daily_journal_repo.get_by_id(db, daily_journal_id)
    if daily_journal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Daily journal not found.")

    account = trading_account_repo.get_by_id_for_user(db, daily_journal.account_id, current_user.id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Daily journal not found.")

    return daily_journal, account


def get_or_create_trade_journal(db: Session, *, trade_id: uuid.UUID, current_user: User):
    trade, account = _get_trade_owned_by_user(db, trade_id=trade_id, current_user=current_user)

    trade_journal = trade_journal_repo.get_by_trade_id(db, trade_id)
    is_new = False

    if trade_journal is None:
        local_trade_date = to_account_local_date(trade.closed_at, account.timezone)
        daily_journal = daily_journal_repo.get_by_account_and_date(
            db,
            account_id=account.id,
            trading_date=local_trade_date,
        )
        if daily_journal is None:
            daily_journal = daily_journal_repo.create(
                db,
                account_id=account.id,
                trading_date=local_trade_date,
            )

        trade_journal = trade_journal_repo.create(
            db,
            trade_id=trade.id,
            daily_journal_id=daily_journal.id,
        )
        is_new = True

        journal_message_repo.create(
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

    messages = journal_message_repo.list_by_trade_journal(db, trade_journal.id)
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
    if message_type in {JournalMessageType.system, JournalMessageType.prompt, JournalMessageType.ai_response}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported message type.")

    trade_journal, _ = get_or_create_trade_journal(db, trade_id=trade_id, current_user=current_user)
    tags = extract_tags(content)

    audio_storage_path = None
    audio_mime_type = None
    image_storage_path = None
    image_mime_type = None
    image_original_filename = None

    if message_type == JournalMessageType.voice:
        if file is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Voice message file is required.")
        audio_storage_path, audio_mime_type = _upload_voice_note(file, user_prefix=str(current_user.id))
    elif message_type == JournalMessageType.image:
        if file is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Image file is required.")
        image_storage_path, media_type, image_mime_type = upload_media(
            file=file,
            bucket=settings.JOURNAL_IMAGES_BUCKET,
            prefix=str(current_user.id),
        )
        if str(media_type.value) != "image":
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only image files are allowed for image messages.")
        image_original_filename = file.filename or None
    elif message_type == JournalMessageType.text:
        if not content:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Text message content is required.")

    message = journal_message_repo.create(
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
        journal_attachment_repo.create(
            db,
            message_id=message.id,
            storage_path=image_storage_path,
            media_type="image",
            mime_type=image_mime_type,
            original_filename=image_original_filename,
            caption=content,
        )

    db.commit()
    db.refresh(message)
    return _serialize_message(db, message)


def get_or_create_daily_journal(
    db: Session,
    *,
    account_id: uuid.UUID,
    trading_date: date,
    current_user: User,
) -> DailyJournalResponse:
    account = trading_account_repo.get_by_id_for_user(db, account_id, current_user.id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found.")

    daily_journal = daily_journal_repo.get_by_account_and_date(
        db,
        account_id=account_id,
        trading_date=trading_date,
    )
    if daily_journal is None:
        daily_journal = daily_journal_repo.create(
            db,
            account_id=account_id,
            trading_date=trading_date,
        )
        db.commit()
        db.refresh(daily_journal)

    trades = trade_repo.list_by_account_local_date(
        db,
        account_id=account_id,
        trading_date=trading_date,
        account_timezone=account.timezone,
    )
    message_counts = trade_journal_repo.get_message_counts_for_trade_ids(
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
            )
        )

    messages = journal_message_repo.list_by_daily_journal(db, daily_journal.id)

    return DailyJournalResponse(
        id=daily_journal.id,
        trading_date=daily_journal.trading_date,
        account_timezone=account.timezone,
        trade_chips=trade_chips,
        messages=[_serialize_message(db, m) for m in messages],
    )


def create_daily_journal_message(
    db: Session,
    *,
    daily_journal_id: uuid.UUID,
    current_user: User,
    message_type: JournalMessageType,
    content: str | None,
    file: UploadFile | None = None,
):
    if message_type in {JournalMessageType.system, JournalMessageType.prompt, JournalMessageType.ai_response}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported message type.")

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
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Voice message file is required.")
        audio_storage_path, _ = _upload_voice_note(file, user_prefix=str(current_user.id))
    elif message_type == JournalMessageType.image:
        if file is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Image file is required.")
        image_storage_path, media_type, image_mime_type = upload_media(
            file=file,
            bucket=settings.JOURNAL_IMAGES_BUCKET,
            prefix=str(current_user.id),
        )
        if str(media_type.value) != "image":
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only image files are allowed for image messages.")
        image_original_filename = file.filename or None
    elif message_type == JournalMessageType.text and not content:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Text message content is required.")

    message = journal_message_repo.create(
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
        journal_attachment_repo.create(
            db,
            message_id=message.id,
            storage_path=image_storage_path,
            media_type="image",
            mime_type=image_mime_type,
            original_filename=image_original_filename,
            caption=content,
        )

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
    message = journal_message_repo.get_by_id(db, message_id)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")

    if message.author_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed.")

    if message.message_type in {JournalMessageType.system, JournalMessageType.prompt}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Message cannot be edited.")

    updated = journal_message_repo.update_content(db, message, content=content, tags=extract_tags(content))
    db.commit()
    db.refresh(updated)
    return _serialize_message(db, updated)


def delete_message(db: Session, *, message_id: uuid.UUID, current_user: User) -> None:
    message = journal_message_repo.get_by_id(db, message_id)
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found.")

    if message.author_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed.")

    if message.message_type in {JournalMessageType.system, JournalMessageType.prompt}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Message cannot be deleted.")

    journal_message_repo.delete(db, message)
    db.commit()


def list_daily_journal_feed(
    db: Session,
    *,
    account_id: uuid.UUID,
    current_user: User,
    limit: int,
    cursor_daily_journal_id: uuid.UUID | None,
):
    account = trading_account_repo.get_by_id_for_user(db, account_id, current_user.id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trading account not found.")

    return daily_journal_repo.list_feed(
        db,
        account_id=account_id,
        limit=limit,
        cursor_daily_journal_id=cursor_daily_journal_id,
    )
