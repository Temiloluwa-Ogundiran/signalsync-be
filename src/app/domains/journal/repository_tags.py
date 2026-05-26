import uuid
from typing import Optional, List, Tuple
from sqlalchemy import select, delete, and_, or_
from sqlalchemy.orm import Session, joinedload
from app.domains.journal.models import TagCategory, TagOption, TradeTagSelection


def seed_system_tags(db: Session) -> Tuple[int, int]:
    """
    Seeds system-wide default categories and options.
    Returns (categories_created, options_created).
    """
    categories_created = 0
    options_created = 0

    defaults = {
        "Strategy": [
            ("Breakout", "#3b82f6"),       # Blue
            ("Trend Following", "#10b981"), # Green
            ("Mean Reversion", "#8b5cf6"),  # Purple
            ("Scalping", "#f59e0b"),        # Orange
            ("Momentum", "#ec4899"),        # Pink
        ],
        "Mistakes": [
            ("FOMO", "#ef4444"),            # Red
            ("Overleveraging", "#b91c1c"),  # Dark Red
            ("Early Exit", "#f59e0b"),      # Orange
            ("Chasing Market", "#ec4899"),  # Pink
            ("No SL", "#7f1d1d"),           # Maroon
        ]
    }

    for cat_title, opts in defaults.items():
        # Check if system category exists
        stmt = select(TagCategory).where(
            and_(TagCategory.title == cat_title, TagCategory.is_system == True)
        )
        category = db.execute(stmt).scalar_one_or_none()

        if not category:
            category = TagCategory(
                title=cat_title,
                is_system=True,
                user_id=None
            )
            db.add(category)
            db.flush()
            categories_created += 1

        for opt_val, opt_color in opts:
            # Check if option exists in this category
            opt_stmt = select(TagOption).where(
                and_(
                    TagOption.category_id == category.id,
                    TagOption.value == opt_val,
                    TagOption.user_id == None
                )
            )
            option = db.execute(opt_stmt).scalar_one_or_none()

            if not option:
                option = TagOption(
                    category_id=category.id,
                    value=opt_val,
                    color=opt_color,
                    user_id=None
                )
                db.add(option)
                db.flush()
                options_created += 1

    return categories_created, options_created


def list_categories_with_options(db: Session, user_id: uuid.UUID) -> List[TagCategory]:
    """
    Lists all categories visible to a user (system categories + custom categories owned by the user).
    Includes only options within those categories that are visible (system options + user custom options).
    """
    # Fetch visible categories
    stmt = (
        select(TagCategory)
        .where(
            or_(
                TagCategory.is_system == True,
                TagCategory.user_id == user_id
            )
        )
        .order_by(TagCategory.is_system.desc(), TagCategory.created_at.asc())
    )
    categories = db.execute(stmt).scalars().all()

    # Post-process or subquery load only visible options.
    # To avoid N+1 queries, we can fetch all visible options and distribute them.
    opt_stmt = (
        select(TagOption)
        .where(
            or_(
                TagOption.user_id == None,
                TagOption.user_id == user_id
            )
        )
        .order_by(TagOption.created_at.asc())
    )
    all_options = db.execute(opt_stmt).scalars().all()

    # Group options by category_id
    options_by_cat = {}
    for opt in all_options:
        options_by_cat.setdefault(opt.category_id, []).append(opt)

    # Attach only visible options to the category instances
    for cat in categories:
        cat.options = options_by_cat.get(cat.id, [])

    return list(categories)


def get_category_by_id(db: Session, category_id: uuid.UUID) -> Optional[TagCategory]:
    stmt = select(TagCategory).where(TagCategory.id == category_id)
    return db.execute(stmt).scalar_one_or_none()


def create_category(db: Session, user_id: uuid.UUID, title: str) -> TagCategory:
    category = TagCategory(
        user_id=user_id,
        title=title,
        is_system=False
    )
    db.add(category)
    db.flush()
    return category


def delete_category(db: Session, user_id: uuid.UUID, category_id: uuid.UUID) -> bool:
    stmt = select(TagCategory).where(
        and_(TagCategory.id == category_id, TagCategory.user_id == user_id)
    )
    category = db.execute(stmt).scalar_one_or_none()
    if not category:
        return False
    db.delete(category)
    db.flush()
    return True


def get_option_by_id(db: Session, option_id: uuid.UUID) -> Optional[TagOption]:
    stmt = select(TagOption).where(TagOption.id == option_id)
    return db.execute(stmt).scalar_one_or_none()


def create_option(
    db: Session,
    user_id: uuid.UUID,
    category_id: uuid.UUID,
    value: str,
    color: Optional[str] = None
) -> TagOption:
    option = TagOption(
        category_id=category_id,
        user_id=user_id,
        value=value,
        color=color
    )
    db.add(option)
    db.flush()
    return option


def delete_option(db: Session, user_id: uuid.UUID, option_id: uuid.UUID) -> bool:
    stmt = select(TagOption).where(
        and_(TagOption.id == option_id, TagOption.user_id == user_id)
    )
    option = db.execute(stmt).scalar_one_or_none()
    if not option:
        return False
    db.delete(option)
    db.flush()
    return True


def get_trade_tag_options(db: Session, trade_id: uuid.UUID) -> List[TagOption]:
    """
    Get all selected tag options for a specific trade.
    """
    stmt = (
        select(TagOption)
        .join(TradeTagSelection, TradeTagSelection.option_id == TagOption.id)
        .where(TradeTagSelection.trade_id == trade_id)
        .order_by(TagOption.value.asc())
    )
    return list(db.execute(stmt).scalars().all())


def update_trade_tags(db: Session, trade_id: uuid.UUID, option_ids: List[uuid.UUID]) -> None:
    """
    Replaces all active tag selections for a trade.
    """
    # Delete old selections
    del_stmt = delete(TradeTagSelection).where(TradeTagSelection.trade_id == trade_id)
    db.execute(del_stmt)

    # Insert new selections
    for opt_id in option_ids:
        sel = TradeTagSelection(trade_id=trade_id, option_id=opt_id)
        db.add(sel)
    db.flush()
