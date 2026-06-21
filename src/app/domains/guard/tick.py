"""The per-tick input to the Guard engine.

A Tick is the normalized snapshot the watcher produces from an mt5-core poll. The
engine is pure: given the previous EngineMemory + a Tick, it produces the next
memory and an AccountState. Nothing in here reaches the network.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from .money import money


@dataclass(frozen=True)
class Position:
    """An open position, P&L already net of swap + commission (real equity)."""

    ticket: str
    symbol: str
    volume: Decimal
    open_price: Decimal
    profit: Decimal  # floating P&L incl. swap + commission

    @staticmethod
    def of(ticket, symbol, volume, open_price, profit) -> "Position":
        return Position(
            ticket=str(ticket),
            symbol=str(symbol),
            volume=money(volume),
            open_price=money(open_price),
            profit=money(profit),
        )


@dataclass(frozen=True)
class Tick:
    """One observation of an account.

    ``balance`` is realized; ``equity`` is balance + floating P&L. Both must
    already include swap & commission — they are real equity. ``traded_today`` and
    ``day_realized_pnl`` let the engine track trading-days and closed-day results
    without holding full deal history.
    """

    ts: datetime                 # timezone-aware
    balance: Decimal
    equity: Decimal
    positions: List[Position] = field(default_factory=list)
    traded_today: bool = False   # any deal opened/closed since the firm's reset
    day_realized_pnl: Decimal = Decimal(0)  # realized P&L since reset

    @staticmethod
    def of(ts: datetime, balance, equity, positions: Optional[List[Position]] = None,
           traded_today: bool = False, day_realized_pnl=0) -> "Tick":
        if ts.tzinfo is None:
            raise ValueError("Tick.ts must be timezone-aware")
        return Tick(
            ts=ts,
            balance=money(balance),
            equity=money(equity),
            positions=list(positions or []),
            traded_today=traded_today,
            day_realized_pnl=money(day_realized_pnl),
        )

    @property
    def open_pnl(self) -> Decimal:
        return sum((p.profit for p in self.positions), Decimal(0))

    @property
    def has_open_positions(self) -> bool:
        return len(self.positions) > 0
