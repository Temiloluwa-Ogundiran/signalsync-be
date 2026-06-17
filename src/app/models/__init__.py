# Import all models here so SQLAlchemy's Base.metadata is fully populated
# before Alembic autogenerate or engine.create_all() is called.

from app.domains.users.models import User
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
    Setup,
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
from app.domains.notifications.models import Notification, NotificationType

__all__ = [
    "User",
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
    "Setup",
    "AiAgent",
    "AiChatMessage",
    "AiChatSession",
    "AiInsight",
    "AiMessageRole",
    "AiUsage",
    "AiUserMemory",
    "Notification",
    "NotificationType",
]
