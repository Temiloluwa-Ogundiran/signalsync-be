# Import all models here so SQLAlchemy's Base.metadata is fully populated
# before Alembic autogenerate or engine.create_all() is called.

from app.domains.users.models import User
from app.domains.streams.models import Stream, StreamPrivacy, StreamMember, MemberStatus
from app.domains.posts.models import Post, PostMedia, PostMediaType, PostType, PostUpvote
from app.domains.auth.models import Token, TokenType
from app.domains.accounts.models import (
    AccountSnapshot,
    Trade,
    TradeDirection,
    TradeSession,
    TradingAccount,
    TradingAccountProvisioningStatus,
    TradingAccountStatus,
    TradingAccountType,
    TradingPlatform,
)
from app.domains.journal.models import (
    DailyJournal,
    JournalAttachment,
    JournalMessage,
    JournalMessageType,
    JournalTemplate,
    JournalTemplateType,
    TradeJournal,
    TagGroup,
    Tag,
    TradeTag,
)
from app.domains.ai.models import (
    AiAgent,
    AiChatMessage,
    AiChatSession,
    AiInsight,
    AiMessageRole,
    AiUsage,
    AiUserMemory,
)

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
    "DailyJournal",
    "TradeJournal",
    "JournalMessage",
    "JournalMessageType",
    "JournalAttachment",
    "JournalTemplate",
    "JournalTemplateType",
    "TagGroup",
    "Tag",
    "TradeTag",
    "AiAgent",
    "AiChatMessage",
    "AiChatSession",
    "AiInsight",
    "AiMessageRole",
    "AiUsage",
    "AiUserMemory",
]
