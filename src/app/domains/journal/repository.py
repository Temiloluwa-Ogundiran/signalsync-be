import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.domains.accounts.models import AccountSnapshot, Trade
from app.domains.journal.models import (
    DailyJournal,
    JournalAttachment,
    JournalMessage,
    JournalMessageType,
    JournalTemplate,
    JournalTemplateType,
    TagCategory,
    TagOption,
    TradeJournal,
    TradeTagSelection,
)


# ---------------------------------------------------------------------------
# DailyJournal
# ---------------------------------------------------------------------------

def get_daily_journal_by_id(
    db: Session, daily_journal_id: uuid.UUID
) -> Optional[DailyJournal]:
    stmt = select(DailyJournal).where(DailyJournal.id == daily_journal_id)
    return db.execute(stmt).scalar_one_or_none()


def get_daily_journal_by_account_and_date(
    db: Session,
    *,
    account_id: uuid.UUID,
    trading_date: date,
) -> Optional[DailyJournal]:
    stmt = select(DailyJournal).where(
        DailyJournal.account_id == account_id,
        DailyJournal.trading_date == trading_date,
    )
    return db.execute(stmt).scalar_one_or_none()


def create_daily_journal(
    db: Session,
    *,
    account_id: uuid.UUID,
    trading_date: date,
) -> DailyJournal:
    daily_journal = DailyJournal(account_id=account_id, trading_date=trading_date)
    db.add(daily_journal)
    db.flush()
    return daily_journal


def list_daily_journal_feed(
    db: Session,
    *,
    account_id: uuid.UUID,
    limit: int,
    cursor_daily_journal_id: Optional[uuid.UUID],
) -> list[DailyJournal]:
    stmt = (
        select(DailyJournal)
        .where(DailyJournal.account_id == account_id)
        .order_by(DailyJournal.trading_date.desc(), DailyJournal.id.desc())
        .limit(limit)
    )

    if cursor_daily_journal_id is not None:
        cursor_stmt = select(DailyJournal.trading_date, DailyJournal.id).where(
            DailyJournal.id == cursor_daily_journal_id,
            DailyJournal.account_id == account_id,
        )
        cursor_row = db.execute(cursor_stmt).one_or_none()
        if cursor_row is not None:
            cursor_trading_date, cursor_id = cursor_row
            stmt = stmt.where(
                (DailyJournal.trading_date < cursor_trading_date)
                | ((DailyJournal.trading_date == cursor_trading_date) & (DailyJournal.id < cursor_id))
            )

    return list(db.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# TradeJournal
# ---------------------------------------------------------------------------

def get_trade_journal_by_id(
    db: Session, trade_journal_id: uuid.UUID
) -> Optional[TradeJournal]:
    stmt = select(TradeJournal).where(TradeJournal.id == trade_journal_id)
    return db.execute(stmt).scalar_one_or_none()


def get_trade_journal_by_trade_id(
    db: Session, trade_id: uuid.UUID
) -> Optional[TradeJournal]:
    stmt = select(TradeJournal).where(TradeJournal.trade_id == trade_id)
    return db.execute(stmt).scalar_one_or_none()


def create_trade_journal(
    db: Session,
    *,
    trade_id: uuid.UUID,
    daily_journal_id: Optional[uuid.UUID],
) -> TradeJournal:
    trade_journal = TradeJournal(trade_id=trade_id, daily_journal_id=daily_journal_id)
    db.add(trade_journal)
    db.flush()
    return trade_journal


def map_trade_reviewed_at_by_trade_ids(
    db: Session,
    *,
    trade_ids: list[uuid.UUID],
) -> dict[uuid.UUID, Optional[datetime]]:
    if not trade_ids:
        return {}
    stmt = select(TradeJournal.trade_id, TradeJournal.reviewed_at).where(
        TradeJournal.trade_id.in_(trade_ids)
    )
    rows = db.execute(stmt).all()
    return {row[0]: row[1] for row in rows}


def map_trade_journal_ratings_by_trade_ids(
    db: Session,
    *,
    trade_ids: list[uuid.UUID],
) -> dict[uuid.UUID, Optional[int]]:
    if not trade_ids:
        return {}
    stmt = select(TradeJournal.trade_id, TradeJournal.rating).where(
        TradeJournal.trade_id.in_(trade_ids)
    )
    rows = db.execute(stmt).all()
    return {row[0]: row[1] for row in rows}


def map_trade_journal_assessments_by_trade_ids(
    db: Session,
    *,
    trade_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict]:
    if not trade_ids:
        return {}
    stmt = select(
        TradeJournal.trade_id,
        TradeJournal.execution_quality,
        TradeJournal.setup_quality,
        TradeJournal.discipline_score,
    ).where(TradeJournal.trade_id.in_(trade_ids))
    rows = db.execute(stmt).all()
    return {
        row[0]: {
            "execution_quality": row[1],
            "setup_quality": row[2],
            "discipline_score": row[3],
        }
        for row in rows
    }


def adjacent_traded_local_dates(
    db: Session,
    *,
    account_id: uuid.UUID,
    account_timezone: str,
    trading_date: date,
) -> tuple[Optional[date], Optional[date]]:
    """Previous / next local calendar dates (vs account tz) that have at least one closed trade."""
    local_date_expr = func.date(func.timezone(account_timezone, Trade.closed_at))
    prev_stmt = select(func.max(local_date_expr)).where(
        Trade.account_id == account_id,
        local_date_expr < trading_date,
    )
    next_stmt = select(func.min(local_date_expr)).where(
        Trade.account_id == account_id,
        local_date_expr > trading_date,
    )
    prev_raw = db.execute(prev_stmt).scalar_one_or_none()
    next_raw = db.execute(next_stmt).scalar_one_or_none()
    prev_date = prev_raw if isinstance(prev_raw, date) else None
    next_date = next_raw if isinstance(next_raw, date) else None
    return prev_date, next_date


def get_trade_journal_message_counts(
    db: Session,
    *,
    trade_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    if not trade_ids:
        return {}

    stmt = (
        select(TradeJournal.trade_id, func.count(JournalMessage.id))
        .outerjoin(JournalMessage, JournalMessage.trade_journal_id == TradeJournal.id)
        .where(TradeJournal.trade_id.in_(trade_ids))
        .group_by(TradeJournal.trade_id)
    )

    return {trade_id: int(count) for trade_id, count in db.execute(stmt).all()}


# ---------------------------------------------------------------------------
# JournalMessage
# ---------------------------------------------------------------------------

def create_message(
    db: Session,
    *,
    daily_journal_id: Optional[uuid.UUID],
    trade_journal_id: Optional[uuid.UUID],
    author_id: Optional[uuid.UUID],
    message_type: JournalMessageType,
    content: Optional[str],
    tags: list[str],
    system_data: Optional[dict] = None,
    audio_storage_path: Optional[str] = None,
    audio_duration_seconds: Optional[int] = None,
) -> JournalMessage:
    message = JournalMessage(
        daily_journal_id=daily_journal_id,
        trade_journal_id=trade_journal_id,
        author_id=author_id,
        message_type=message_type,
        content=content,
        tags=tags,
        system_data=system_data,
        audio_storage_path=audio_storage_path,
        audio_duration_seconds=audio_duration_seconds,
    )
    db.add(message)
    db.flush()
    return message


def get_message_by_id(
    db: Session, message_id: uuid.UUID
) -> Optional[JournalMessage]:
    stmt = select(JournalMessage).where(JournalMessage.id == message_id)
    return db.execute(stmt).scalar_one_or_none()


def list_messages_by_trade_journal(
    db: Session, trade_journal_id: uuid.UUID
) -> list[JournalMessage]:
    stmt = (
        select(JournalMessage)
        .where(JournalMessage.trade_journal_id == trade_journal_id)
        .order_by(JournalMessage.created_at.asc(), JournalMessage.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def list_messages_by_daily_journal(
    db: Session, daily_journal_id: uuid.UUID
) -> list[JournalMessage]:
    stmt = (
        select(JournalMessage)
        .where(JournalMessage.daily_journal_id == daily_journal_id)
        .order_by(JournalMessage.created_at.asc(), JournalMessage.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def update_message_content(
    db: Session,
    message: JournalMessage,
    content: Optional[str],
    tags: list[str],
) -> JournalMessage:
    message.content = content
    message.tags = tags
    message.is_edited = True
    message.edited_at = datetime.now(timezone.utc)
    db.flush()
    return message


def delete_message(db: Session, message: JournalMessage) -> None:
    db.delete(message)
    db.flush()


# ---------------------------------------------------------------------------
# JournalAttachment
# ---------------------------------------------------------------------------

def create_attachment(
    db: Session,
    *,
    message_id: uuid.UUID,
    storage_path: str,
    media_type: str,
    mime_type: str,
    original_filename: Optional[str],
    caption: Optional[str],
) -> JournalAttachment:
    attachment = JournalAttachment(
        message_id=message_id,
        storage_path=storage_path,
        media_type=media_type,
        mime_type=mime_type,
        original_filename=original_filename,
        caption=caption,
    )
    db.add(attachment)
    db.flush()
    return attachment


def list_attachments_by_message(
    db: Session, message_id: uuid.UUID
) -> list[JournalAttachment]:
    stmt = (
        select(JournalAttachment)
        .where(JournalAttachment.message_id == message_id)
        .order_by(JournalAttachment.created_at.asc(), JournalAttachment.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# JournalTemplate
# ---------------------------------------------------------------------------

DAILY_REFLECTION_TEMPLATE_ID = uuid.UUID("ee5ad00f-0de2-4ce9-a8f3-f89a0a8f1f3a")
POST_TRADE_REVIEW_TEMPLATE_ID = uuid.UUID("228eb5ba-30a0-4b36-95b2-7e82ff931f91")


def seed_system_templates(db: Session) -> tuple[int, int]:
    now = datetime.now(timezone.utc)

    templates = [
        {
            "id": DAILY_REFLECTION_TEMPLATE_ID,
            "name": "Daily reflection",
            "template_type": JournalTemplateType.daily,
            "is_system": True,
            "owner_id": None,
            "questions": [
                {"id": 1, "order": 1, "text": "How did you feel going into today's session?"},
                {"id": 2, "order": 2, "text": "Did you stick to your trading plan today?"},
                {"id": 3, "order": 3, "text": "What was your best decision today?"},
                {"id": 4, "order": 4, "text": "What would you do differently tomorrow?"},
                {"id": 5, "order": 5, "text": "Rate your discipline today from 1-10."},
            ],
            "created_at": now,
        },
        {
            "id": POST_TRADE_REVIEW_TEMPLATE_ID,
            "name": "Post-trade review",
            "template_type": JournalTemplateType.trade,
            "is_system": True,
            "owner_id": None,
            "questions": [
                {"id": 1, "order": 1, "text": "What was your entry reason for this trade?"},
                {"id": 2, "order": 2, "text": "Did you follow your entry rules exactly?"},
                {"id": 3, "order": 3, "text": "How did you manage the trade (SL, TP, partials)?"},
                {"id": 4, "order": 4, "text": "What would you have done differently?"},
                {
                    "id": 5,
                    "order": 5,
                    "text": "Rate this trade: A (followed plan perfectly) / B (minor deviation) / C (broke rules)",
                },
            ],
            "created_at": now,
        },
    ]

    target_ids = [tpl["id"] for tpl in templates]
    existing_ids = set(
        db.scalars(
            select(JournalTemplate.id).where(JournalTemplate.id.in_(target_ids))
        ).all()
    )

    created_count = 0
    for template in templates:
        if template["id"] in existing_ids:
            continue
        db.add(JournalTemplate(**template))
        created_count += 1

    return created_count, len(templates) - created_count


def create_template(
    db: Session,
    *,
    owner_id: uuid.UUID,
    name: str,
    template_type: JournalTemplateType,
    questions: list,
) -> JournalTemplate:
    template = JournalTemplate(
        owner_id=owner_id,
        name=name,
        template_type=template_type,
        is_system=False,
        questions=questions,
    )
    db.add(template)
    db.flush()
    return template


def get_template_visible_to_user(
    db: Session,
    *,
    template_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Optional[JournalTemplate]:
    stmt = select(JournalTemplate).where(
        JournalTemplate.id == template_id,
        or_(JournalTemplate.is_system.is_(True), JournalTemplate.owner_id == user_id),
    )
    return db.execute(stmt).scalar_one_or_none()


def list_templates_visible_to_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    template_type: Optional[JournalTemplateType] = None,
) -> list[JournalTemplate]:
    stmt = select(JournalTemplate).where(
        or_(JournalTemplate.is_system.is_(True), JournalTemplate.owner_id == user_id)
    )

    if template_type is not None:
        stmt = stmt.where(JournalTemplate.template_type == template_type)

    stmt = stmt.order_by(JournalTemplate.is_system.desc(), JournalTemplate.created_at.asc())
    return list(db.execute(stmt).scalars().all())


def delete_user_template(
    db: Session, *, template: JournalTemplate, user_id: uuid.UUID
) -> bool:
    if template.is_system or template.owner_id != user_id:
        return False
    db.delete(template)
    db.flush()
    return True


# ---------------------------------------------------------------------------
# Analytics queries
# ---------------------------------------------------------------------------

def list_trades_filtered(
    db: Session,
    *,
    account_id: uuid.UUID,
    closed_from_utc,
    closed_to_utc_exclusive,
    include_manual: bool = True,
) -> list[Trade]:
    stmt = select(Trade).where(
        Trade.account_id == account_id,
        Trade.is_missed.is_(False),
    )

    if not include_manual:
        stmt = stmt.where(Trade.is_manual.is_(False))
    if closed_from_utc is not None:
        stmt = stmt.where(Trade.closed_at >= closed_from_utc)
    if closed_to_utc_exclusive is not None:
        stmt = stmt.where(Trade.closed_at < closed_to_utc_exclusive)

    stmt = stmt.order_by(Trade.closed_at.asc(), Trade.id.asc())
    return list(db.execute(stmt).scalars().all())


def list_trades_filtered_multi(
    db: Session,
    *,
    account_ids: list[uuid.UUID],
    closed_from_utc,
    closed_to_utc_exclusive,
    include_manual: bool = True,
) -> list[Trade]:
    if not account_ids:
        return []

    stmt = select(Trade).where(
        Trade.account_id.in_(account_ids),
        Trade.is_missed.is_(False),
    )

    if not include_manual:
        stmt = stmt.where(Trade.is_manual.is_(False))
    if closed_from_utc is not None:
        stmt = stmt.where(Trade.closed_at >= closed_from_utc)
    if closed_to_utc_exclusive is not None:
        stmt = stmt.where(Trade.closed_at < closed_to_utc_exclusive)

    stmt = stmt.order_by(Trade.closed_at.asc(), Trade.id.asc())
    return list(db.execute(stmt).scalars().all())


def list_daily_pnl(
    db: Session,
    *,
    account_id: uuid.UUID,
    account_timezone: str,
    closed_from_utc,
    closed_to_utc_exclusive,
    include_manual: bool = True,
) -> list[tuple]:
    from sqlalchemy import func as sa_func
    local_date_expr = sa_func.date(sa_func.timezone(account_timezone, Trade.closed_at))

    stmt = (
        select(
            local_date_expr.label("trading_date"),
            sa_func.count(Trade.id).label("trade_count"),
            sa_func.sum(Trade.net_profit).label("total_pnl"),
            sa_func.count(Trade.id).filter(Trade.net_profit > 0).label("win_count"),
            sa_func.count(Trade.id).filter(Trade.net_profit < 0).label("loss_count"),
        )
        .where(
            Trade.account_id == account_id,
            Trade.is_missed.is_(False),
        )
    )

    if not include_manual:
        stmt = stmt.where(Trade.is_manual.is_(False))

    stmt = stmt.group_by(local_date_expr).order_by(local_date_expr)

    if closed_from_utc is not None:
        stmt = stmt.where(Trade.closed_at >= closed_from_utc)
    if closed_to_utc_exclusive is not None:
        stmt = stmt.where(Trade.closed_at < closed_to_utc_exclusive)

    return list(db.execute(stmt).all())


def list_trading_dates_with_journal_activity(
    db: Session,
    *,
    account_id: uuid.UUID,
    account_timezone: str,
    from_date: date,
    to_date: date,
) -> set[date]:
    """Local trading dates with journal engagement for calendar UI.

    Includes: non-system day/trade messages, explicitly reviewed daily journals,
    or trade journals marked reviewed (local close date).
    """
    local_trade_date = func.date(func.timezone(account_timezone, Trade.closed_at))

    stmt_daily = (
        select(DailyJournal.trading_date)
        .join(JournalMessage, JournalMessage.daily_journal_id == DailyJournal.id)
        .where(
            DailyJournal.account_id == account_id,
            JournalMessage.message_type != JournalMessageType.system,
            DailyJournal.trading_date >= from_date,
            DailyJournal.trading_date <= to_date,
        )
        .distinct()
    )

    stmt_trade = (
        select(local_trade_date.label("trading_date"))
        .select_from(Trade)
        .join(TradeJournal, TradeJournal.trade_id == Trade.id)
        .join(JournalMessage, JournalMessage.trade_journal_id == TradeJournal.id)
        .where(
            Trade.account_id == account_id,
            JournalMessage.message_type != JournalMessageType.system,
            local_trade_date >= from_date,
            local_trade_date <= to_date,
        )
        .distinct()
    )

    stmt_daily_reviewed = (
        select(DailyJournal.trading_date)
        .where(
            DailyJournal.account_id == account_id,
            DailyJournal.reviewed_at.isnot(None),
            DailyJournal.trading_date >= from_date,
            DailyJournal.trading_date <= to_date,
        )
        .distinct()
    )

    stmt_trade_reviewed = (
        select(local_trade_date.label("trading_date"))
        .select_from(Trade)
        .join(TradeJournal, TradeJournal.trade_id == Trade.id)
        .where(
            Trade.account_id == account_id,
            TradeJournal.reviewed_at.isnot(None),
            local_trade_date >= from_date,
            local_trade_date <= to_date,
        )
        .distinct()
    )

    dates: set[date] = set()
    for (d,) in db.execute(stmt_daily).all():
        if d is not None:
            dates.add(d)
    for (d,) in db.execute(stmt_trade).all():
        if d is not None:
            dates.add(d)
    for (d,) in db.execute(stmt_daily_reviewed).all():
        if d is not None:
            dates.add(d)
    for (d,) in db.execute(stmt_trade_reviewed).all():
        if d is not None:
            dates.add(d)
    return dates


def list_account_snapshots(
    db: Session,
    *,
    account_id: uuid.UUID,
    from_date,
    to_date,
) -> list[AccountSnapshot]:
    stmt = select(AccountSnapshot).where(AccountSnapshot.account_id == account_id)

    if from_date is not None:
        stmt = stmt.where(AccountSnapshot.snapshot_date >= from_date)
    if to_date is not None:
        stmt = stmt.where(AccountSnapshot.snapshot_date <= to_date)

    stmt = stmt.order_by(AccountSnapshot.snapshot_date.asc(), AccountSnapshot.id.asc())
    return list(db.execute(stmt).scalars().all())


def get_latest_account_snapshot_on_or_before(
    db: Session,
    *,
    account_id: uuid.UUID,
    snapshot_date: date,
) -> AccountSnapshot | None:
    stmt = (
        select(AccountSnapshot)
        .where(
            AccountSnapshot.account_id == account_id,
            AccountSnapshot.snapshot_date <= snapshot_date,
        )
        .order_by(AccountSnapshot.snapshot_date.desc(), AccountSnapshot.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def list_trade_setups(
    db: Session,
    *,
    account_id: uuid.UUID,
    closed_from_utc,
    closed_to_utc_exclusive,
    include_manual: bool = True,
) -> list[tuple]:
    from sqlalchemy import text
    sql = """
    WITH tagged_trades AS (
        SELECT
            lower(unnest(jm.tags)) AS tag,
            t.id AS trade_id,
            t.net_profit AS net_profit
        FROM trades t
        JOIN trade_journals tj ON tj.trade_id = t.id
        JOIN journal_messages jm ON jm.trade_journal_id = tj.id
        WHERE t.account_id = :account_id
          AND t.is_missed = FALSE
          AND (:include_manual OR t.is_manual = FALSE)
          AND cardinality(jm.tags) > 0
          AND (:closed_from_utc IS NULL OR t.closed_at >= :closed_from_utc)
          AND (:closed_to_utc_exclusive IS NULL OR t.closed_at < :closed_to_utc_exclusive)
        UNION
        SELECT
            lower(to.value) AS tag,
            t.id AS trade_id,
            t.net_profit AS net_profit
        FROM trades t
        JOIN trade_tag_selections tts ON tts.trade_id = t.id
        JOIN tag_options to ON to.id = tts.option_id
        JOIN tag_categories tc ON tc.id = to.category_id
        WHERE t.account_id = :account_id
          AND tc.title = 'Strategy'
          AND t.is_missed = FALSE
          AND (:include_manual OR t.is_manual = FALSE)
          AND (:closed_from_utc IS NULL OR t.closed_at >= :closed_from_utc)
          AND (:closed_to_utc_exclusive IS NULL OR t.closed_at < :closed_to_utc_exclusive)
    )
    SELECT
        tag,
        COUNT(DISTINCT trade_id) AS trade_count,
        COUNT(DISTINCT trade_id) FILTER (WHERE net_profit > 0) AS win_count,
        COALESCE(SUM(net_profit), 0) AS total_pnl
    FROM tagged_trades
    GROUP BY tag
    ORDER BY total_pnl DESC, tag ASC
    """

    return list(
        db.execute(
            text(sql),
            {
                "account_id": str(account_id),
                "closed_from_utc": closed_from_utc,
                "closed_to_utc_exclusive": closed_to_utc_exclusive,
                "include_manual": include_manual,
            },
        ).all()
    )


# ---------------------------------------------------------------------------
# Tag repository (merged from repository_tags.py)
# ---------------------------------------------------------------------------

def seed_system_tags(db: Session) -> tuple[int, int]:
    """Seeds system-wide default categories and options. Returns (categories_created, options_created)."""
    categories_created = 0
    options_created = 0

    defaults = {
        "Strategy": [
            ("Breakout", "#3b82f6"),
            ("Trend Following", "#10b981"),
            ("Mean Reversion", "#8b5cf6"),
            ("Scalping", "#f59e0b"),
            ("Momentum", "#ec4899"),
        ],
        "Mistakes": [
            ("FOMO", "#ef4444"),
            ("Overleveraging", "#b91c1c"),
            ("Early Exit", "#f59e0b"),
            ("Chasing Market", "#ec4899"),
            ("No SL", "#7f1d1d"),
        ],
    }

    for cat_title, opts in defaults.items():
        stmt = select(TagCategory).where(
            and_(TagCategory.title == cat_title, TagCategory.is_system.is_(True))
        )
        category = db.execute(stmt).scalar_one_or_none()
        if not category:
            category = TagCategory(title=cat_title, is_system=True, user_id=None)
            db.add(category)
            db.flush()
            categories_created += 1

        for opt_val, opt_color in opts:
            opt_stmt = select(TagOption).where(
                and_(
                    TagOption.category_id == category.id,
                    TagOption.value == opt_val,
                    TagOption.user_id.is_(None),
                )
            )
            if not db.execute(opt_stmt).scalar_one_or_none():
                db.add(TagOption(category_id=category.id, value=opt_val, color=opt_color, user_id=None))
                db.flush()
                options_created += 1

    return categories_created, options_created


def list_categories_with_options(db: Session, user_id: uuid.UUID) -> list[TagCategory]:
    stmt = (
        select(TagCategory)
        .where(or_(TagCategory.is_system.is_(True), TagCategory.user_id == user_id))
        .order_by(TagCategory.is_system.desc(), TagCategory.created_at.asc())
    )
    categories = list(db.execute(stmt).scalars())

    opt_stmt = (
        select(TagOption)
        .where(or_(TagOption.user_id.is_(None), TagOption.user_id == user_id))
        .order_by(TagOption.created_at.asc())
    )
    options_by_cat: dict[uuid.UUID, list[TagOption]] = {}
    for opt in db.execute(opt_stmt).scalars():
        options_by_cat.setdefault(opt.category_id, []).append(opt)

    for cat in categories:
        cat.options = options_by_cat.get(cat.id, [])
    return categories


def get_category_by_id(db: Session, category_id: uuid.UUID) -> TagCategory | None:
    return db.execute(select(TagCategory).where(TagCategory.id == category_id)).scalar_one_or_none()


def create_category(db: Session, user_id: uuid.UUID, title: str) -> TagCategory:
    category = TagCategory(user_id=user_id, title=title, is_system=False)
    db.add(category)
    db.flush()
    return category


def delete_category(db: Session, user_id: uuid.UUID, category_id: uuid.UUID) -> bool:
    stmt = select(TagCategory).where(and_(TagCategory.id == category_id, TagCategory.user_id == user_id))
    category = db.execute(stmt).scalar_one_or_none()
    if not category:
        return False
    db.delete(category)
    db.flush()
    return True


def get_option_by_id(db: Session, option_id: uuid.UUID) -> TagOption | None:
    return db.execute(select(TagOption).where(TagOption.id == option_id)).scalar_one_or_none()


def create_option(
    db: Session,
    user_id: uuid.UUID,
    category_id: uuid.UUID,
    value: str,
    color: str | None = None,
) -> TagOption:
    option = TagOption(category_id=category_id, user_id=user_id, value=value, color=color)
    db.add(option)
    db.flush()
    return option


def delete_option(db: Session, user_id: uuid.UUID, option_id: uuid.UUID) -> bool:
    stmt = select(TagOption).where(and_(TagOption.id == option_id, TagOption.user_id == user_id))
    option = db.execute(stmt).scalar_one_or_none()
    if not option:
        return False
    db.delete(option)
    db.flush()
    return True


def get_trade_tag_options(db: Session, trade_id: uuid.UUID) -> list[TagOption]:
    stmt = (
        select(TagOption)
        .join(TradeTagSelection, TradeTagSelection.option_id == TagOption.id)
        .where(TradeTagSelection.trade_id == trade_id)
        .order_by(TagOption.value.asc())
    )
    return list(db.execute(stmt).scalars())


def update_trade_tags(db: Session, trade_id: uuid.UUID, option_ids: list[uuid.UUID]) -> None:
    db.execute(delete(TradeTagSelection).where(TradeTagSelection.trade_id == trade_id))
    for opt_id in option_ids:
        db.add(TradeTagSelection(trade_id=trade_id, option_id=opt_id))
    db.flush()
