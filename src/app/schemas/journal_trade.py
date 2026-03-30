import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel

from app.models.trade import TradeDirection, TradeSession


class JournalTradeResponse(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    broker_trade_id: str
    symbol: str
    direction: TradeDirection
    open_price: Decimal
    close_price: Decimal
    volume: Decimal
    profit: Decimal
    commission: Decimal
    swap: Decimal
    net_profit: Decimal
    duration_seconds: int
    session: TradeSession
    opened_at: datetime
    closed_at: datetime
    balance_before_trade: Decimal | None = None
    net_roi_percent: Decimal | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class JournalTradeListResponse(BaseModel):
    items: list[JournalTradeResponse]
    next_cursor: Optional[uuid.UUID] = None
