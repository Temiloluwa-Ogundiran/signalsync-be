from dataclasses import dataclass, field
from decimal import Decimal

from app.domains.copy_trading.engine import SignalAction


@dataclass(frozen=True)
class SignalCase:
    text: str
    action: SignalAction
    symbol: str | None = None
    direction: str | None = None
    entry: Decimal | None = None
    entry_high: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profits: list[Decimal] = field(default_factory=list)
    close_fraction: Decimal | None = None


CLEAR_SIGNAL_CASES = [
    SignalCase("BUY XAUUSD", SignalAction.open_market, "XAUUSD", "buy"),
    SignalCase("sell EURUSD now", SignalAction.open_market, "EURUSD", "sell"),
    SignalCase("XAUUSD BUY", SignalAction.open_market, "XAUUSD", "buy"),
    SignalCase("BUY GOLD NOW", SignalAction.open_market, "XAUUSD", "buy"),
    SignalCase(
        "BUY XAUUSD\nSL 2310\nTP 2340",
        SignalAction.open_market,
        "XAUUSD",
        "buy",
        stop_loss=Decimal("2310"),
        take_profits=[Decimal("2340")],
    ),
    SignalCase(
        "SELL EURUSD @ 1.09150 SL: 1.09500 TP1: 1.08700 TP2: 1.08300",
        SignalAction.open_market,
        "EURUSD",
        "sell",
        entry=Decimal("1.09150"),
        stop_loss=Decimal("1.09500"),
        take_profits=[Decimal("1.08700"), Decimal("1.08300")],
    ),
    SignalCase(
        "BUY XAUUSD ENTRY 2310-2315 SL 2295 TP1 2330 TP2 2350",
        SignalAction.open_market,
        "XAUUSD",
        "buy",
        entry=Decimal("2310"),
        entry_high=Decimal("2315"),
        stop_loss=Decimal("2295"),
        take_profits=[Decimal("2330"), Decimal("2350")],
    ),
    SignalCase(
        "BUY LIMIT XAUUSD 2300 SL 2280 TP 2340",
        SignalAction.place_pending,
        "XAUUSD",
        "buy",
        entry=Decimal("2300"),
        stop_loss=Decimal("2280"),
        take_profits=[Decimal("2340")],
    ),
    SignalCase(
        "EURUSD SELL STOP @ 1.0800 SL 1.0850 TP 1.0700",
        SignalAction.place_pending,
        "EURUSD",
        "sell",
        entry=Decimal("1.0800"),
        stop_loss=Decimal("1.0850"),
        take_profits=[Decimal("1.0700")],
    ),
    SignalCase("SL 2310", SignalAction.modify_sl_tp, stop_loss=Decimal("2310")),
    SignalCase("set stop loss to 1.0850", SignalAction.modify_sl_tp, stop_loss=Decimal("1.0850")),
    SignalCase(
        "XAUUSD SL 2320 TP 2360",
        SignalAction.modify_sl_tp,
        "XAUUSD",
        stop_loss=Decimal("2320"),
        take_profits=[Decimal("2360")],
    ),
    SignalCase("move sl to breakeven", SignalAction.break_even),
    SignalCase("BE now", SignalAction.break_even),
    SignalCase("close half XAUUSD", SignalAction.partial_close, "XAUUSD", close_fraction=Decimal("0.5")),
    SignalCase("close 25% EURUSD", SignalAction.partial_close, "EURUSD", close_fraction=Decimal("0.25")),
    SignalCase("close XAUUSD", SignalAction.full_close, "XAUUSD"),
    SignalCase("exit all EURUSD", SignalAction.full_close, "EURUSD"),
    SignalCase("cancel pending XAUUSD", SignalAction.cancel_pending, "XAUUSD"),
    SignalCase("delete EURUSD limit", SignalAction.cancel_pending, "EURUSD"),
    SignalCase("TP3 2380", SignalAction.additional_tp, take_profits=[Decimal("2380")]),
    SignalCase("add take profit 1.0750 EURUSD", SignalAction.additional_tp, "EURUSD", take_profits=[Decimal("1.0750")]),
    SignalCase("TP hit on XAUUSD", SignalAction.status_only, "XAUUSD"),
    SignalCase("EURUSD stopped out", SignalAction.status_only, "EURUSD"),
]


AI_FALLBACK_CASES = [
    "Gold is looking bullish today",
    "Maybe buy if it rejects the zone",
    "Secure something here",
    "The runner can breathe now",
    "Good morning traders",
    "Results from yesterday were excellent",
    "Watch 2310 closely",
    "I might send a signal later",
    "🔥🔥🔥",
    "",
]
