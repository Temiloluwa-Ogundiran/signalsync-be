from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime, date
from typing import Optional
from io import BytesIO

@dataclass
class ParsedTrade:
    """A single normalized trade extracted from an import file."""
    broker_trade_id: str        # Position ID (dedup key)
    symbol: str
    direction: str              # "buy" | "sell"
    volume: Decimal
    open_price: Decimal
    close_price: Decimal
    opened_at: datetime         # Timezone-naive (broker server time)
    closed_at: datetime
    profit: Decimal             # Gross profit
    commission: Decimal
    swap: Decimal
    sl: Optional[Decimal] = None
    tp: Optional[Decimal] = None
    position_id: Optional[str] = None

@dataclass
class ParsedAccountMeta:
    """Account metadata extracted from the file header."""
    account_number: Optional[str] = None
    currency: Optional[str] = None
    broker_server: Optional[str] = None
    account_type: Optional[str] = None    # "demo" | "live"
    broker_name: Optional[str] = None
    starting_balance: Optional[Decimal] = None
    current_balance: Optional[Decimal] = None

@dataclass
class ParseError:
    row_number: int
    column: Optional[str]
    message: str
    severity: str = "error"     # "error" | "warning"

@dataclass
class ParseResult:
    trades: list[ParsedTrade] = field(default_factory=list)
    account_meta: ParsedAccountMeta = field(default_factory=ParsedAccountMeta)
    daily_balances: dict[date, Decimal] = field(default_factory=dict)
    errors: list[ParseError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_row_count: int = 0
    parsed_trade_count: int = 0

class PlatformParser(ABC):
    """Base class for platform-specific file parsers."""
    platform_id: str                # e.g., "mt5"
    platform_display_name: str      # e.g., "MetaTrader 5"
    supported_extensions: list[str] # e.g., [".xlsx", ".csv"]
    
    @abstractmethod
    def parse(self, file_content: BytesIO, source_timezone: str) -> ParseResult:
        """Parse uploaded file into normalized trades."""
        ...
