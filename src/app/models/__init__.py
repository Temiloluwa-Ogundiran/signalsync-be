# Import all models here so SQLAlchemy's Base.metadata is fully populated
# before Alembic autogenerate or engine.create_all() is called.

from app.models.user import User
from app.models.stream import Stream, StreamPrivacy
from app.models.stream_member import StreamMember, MemberStatus
from app.models.post import Post, PostMedia, PostMediaType, PostType, PostUpvote
from app.models.token import Token, TokenType
from app.models.trading_account import (
    TradingAccount,
    TradingAccountProvisioningStatus,
    TradingAccountStatus,
    TradingAccountType,
    TradingPlatform,
)
from app.models.trade import Trade, TradeDirection, TradeSession
from app.models.account_snapshot import AccountSnapshot
from app.models.daily_stats import DailyStats
from app.models.daily_journal import DailyJournal
from app.models.trade_journal import TradeJournal
from app.models.journal_message import JournalMessage, JournalMessageType
from app.models.journal_attachment import JournalAttachment
from app.models.journal_template import JournalTemplate, JournalTemplateType

__all__ = [
    "User",
    "Stream",
    "StreamPrivacy",
    "StreamMember",
    "MemberStatus",
    "Post",
    "PostMedia",
    "PostMediaType",
    "PostType",
    "PostUpvote",
    "Token",
    "TokenType",
    "TradingAccount",
    "TradingAccountProvisioningStatus",
    "TradingAccountStatus",
    "TradingAccountType",
    "TradingPlatform",
    "Trade",
    "TradeDirection",
    "TradeSession",
    "AccountSnapshot",
    "DailyStats",
    "DailyJournal",
    "TradeJournal",
    "JournalMessage",
    "JournalMessageType",
    "JournalAttachment",
    "JournalTemplate",
    "JournalTemplateType",
]
