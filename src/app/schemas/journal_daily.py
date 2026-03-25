import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel

from app.schemas.journal_message import JournalMessageResponse


class DailyTradeChipResponse(BaseModel):
    trade_id: uuid.UUID
    symbol: str
    direction: str
    net_profit: Decimal
    outcome: str
    journal_message_count: int


class DailyJournalResponse(BaseModel):
    id: uuid.UUID
    trading_date: date
    trade_chips: list[DailyTradeChipResponse]
    messages: list[JournalMessageResponse]


class DailyJournalFeedItemResponse(BaseModel):
    id: uuid.UUID
    trading_date: date


class DailyJournalFeedResponse(BaseModel):
    items: list[DailyJournalFeedItemResponse]
    next_cursor: Optional[uuid.UUID] = None
