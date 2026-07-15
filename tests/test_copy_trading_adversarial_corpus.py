from decimal import Decimal
import random
import string

import pytest

from app.domains.copy_trading.engine import (
    ParsedSignal,
    RouteExecutionPolicy,
    SignalAction,
    validate_signal,
)
from app.domains.copy_trading.parser import deterministic_parse


VALID_SIGNAL_VARIANTS = [
    (f"{direction}{separator}{symbol}\nSL {sl}\nTP {tp}", direction.lower(), symbol)
    for direction, sl, tp in (
        ("BUY", "1.0800", "1.1000"),
        ("SELL", "1.1000", "1.0800"),
    )
    for symbol in ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "BTCUSD")
    for separator in (" ", " : ", "\n")
]


@pytest.mark.parametrize(
    ("text", "direction", "symbol"),
    VALID_SIGNAL_VARIANTS,
)
def test_adversarial_valid_market_signal_matrix(text, direction, symbol) -> None:
    parsed = deterministic_parse(text)

    assert parsed is not None
    assert parsed.action == SignalAction.open_market
    assert parsed.direction == direction
    assert parsed.symbol == symbol


@pytest.mark.parametrize(
    ("text", "symbol"),
    [
        ("BUY EUR/USD SL 1.0800 TP 1.1000", "EURUSD"),
        ("SELL GBP/JPY SL 205.00 TP 201.00", "GBPJPY"),
        ("BUY XAU/USD SL 2300 TP 2400", "XAUUSD"),
        ("SELL BTC/USD SL 70000 TP 65000", "BTCUSD"),
    ],
)
def test_common_slash_separated_symbols_are_normalized(text, symbol) -> None:
    parsed = deterministic_parse(text)

    assert parsed is not None
    assert parsed.symbol == symbol


AMBIGUOUS_OR_NEGATED_SIGNALS = [
    "BUY SELL EURUSD SL 1.0800 TP 1.1000",
    "LONG SHORT XAUUSD SL 2300 TP 2400",
    "BUY EURUSD GBPUSD SL 1.0800 TP 1.1000",
    "SELL XAUUSD BTCUSD SL 2300 TP 2200",
    "DO NOT BUY EURUSD SL 1.0800 TP 1.1000",
    "DON'T SELL EURUSD SL 1.1000 TP 1.0800",
    "CANCEL THE BUY EURUSD SL 1.0800 TP 1.1000",
    "IGNORE BUY EURUSD SL 1.0800 TP 1.1000",
    "AVOID SELL XAUUSD SL 2400 TP 2300",
    "WE BOUGHT EURUSD SL 1.0800 TP 1.1000",
    "WE SOLD EURUSD SL 1.1000 TP 1.0800",
    "YESTERDAY BUY EURUSD SL 1.0800 TP 1.1000",
    "RESULT: BUY EURUSD SL 1.0800 TP 1.1000 WON",
    "EXAMPLE: SELL EURUSD SL 1.1000 TP 1.0800",
    "BACKTEST BUY XAUUSD SL 2300 TP 2400",
    "BUY EURUSD OR SELL GBPUSD SL 1.0800 TP 1.1000",
]


@pytest.mark.parametrize("text", AMBIGUOUS_OR_NEGATED_SIGNALS)
def test_ambiguous_or_negated_text_never_becomes_a_trade(text) -> None:
    parsed = deterministic_parse(text)

    assert parsed is not None
    assert parsed.action == SignalAction.status_only
    assert parsed.confidence == 0


@pytest.mark.parametrize(
    "text",
    [
        "BUY EURUSD SL -1 TP 1.1000",
        "BUY EURUSD SL 0 TP 1.1000",
        "BUY EURUSD SL 1.0800 TP -1",
        "BUY EURUSD SL 1.0800 TP 0",
        "BUY LIMIT EURUSD -1 SL 1.0800 TP 1.1000",
        "BUY LIMIT EURUSD 0 SL 1.0800 TP 1.1000",
    ],
)
def test_unsafe_prices_are_rejected_before_execution(text) -> None:
    parsed = deterministic_parse(text)
    assert parsed is not None

    signal = ParsedSignal(
        action=parsed.action,
        symbol=parsed.symbol,
        direction=parsed.direction,
        entry=parsed.entry,
        entry_high=parsed.entry_high,
        stop_loss=parsed.stop_loss,
        take_profits=parsed.take_profits,
        confidence=parsed.confidence,
    )

    result = validate_signal(signal, RouteExecutionPolicy())
    assert result.accepted is False
    assert result.reason == "Trade prices must be positive finite numbers."


def test_reversed_entry_range_has_specific_validation_error() -> None:
    parsed = deterministic_parse(
        "BUY EURUSD ENTRY 1.1000-1.0900 SL 1.0800 TP 1.1200"
    )
    assert parsed is not None

    result = validate_signal(
        ParsedSignal(
            action=parsed.action,
            symbol=parsed.symbol,
            direction=parsed.direction,
            entry=parsed.entry,
            entry_high=parsed.entry_high,
            stop_loss=parsed.stop_loss,
            take_profits=parsed.take_profits,
            confidence=1,
        ),
        RouteExecutionPolicy(),
    )

    assert result.accepted is False
    assert result.reason == "Entry range minimum cannot exceed its maximum."


@pytest.mark.parametrize(
    ("fraction", "reason"),
    [
        (None, "Partial close needs a percentage between 0% and 100%."),
        (Decimal("NaN"), "Partial close needs a percentage between 0% and 100%."),
        (Decimal("Infinity"), "Partial close needs a percentage between 0% and 100%."),
        (Decimal("-Infinity"), "Partial close needs a percentage between 0% and 100%."),
    ],
)
def test_partial_close_requires_a_finite_fraction(fraction, reason) -> None:
    result = validate_signal(
        ParsedSignal(
            action=SignalAction.partial_close,
            symbol="EURUSD",
            close_fraction=fraction,
            confidence=1,
        ),
        RouteExecutionPolicy(),
    )

    assert result.accepted is False
    assert result.reason == reason


NOISE_TEMPLATES = [
    "Good morning traders {noise}",
    "Results from yesterday {noise}",
    "No signal yet {noise}",
    "Education only {noise}",
    "Risk management reminder {noise}",
    "https://example.com/{noise}",
    "```json {{\"message\": \"{noise}\"}} ```",
    "<b>Channel update</b> {noise}",
]


@pytest.mark.parametrize(
    "text",
    [
        template.format(noise=f"note-{index}-#%&")
        for index in range(10)
        for template in NOISE_TEMPLATES
    ],
)
def test_eighty_noisy_non_signals_never_raise_or_fast_parse(text) -> None:
    assert deterministic_parse(text) is None


def test_seeded_fuzz_corpus_of_one_thousand_messages_never_raises() -> None:
    randomizer = random.Random(20260715)
    alphabet = string.ascii_letters + string.digits + string.punctuation + " \n\t"

    for _ in range(1000):
        text = "".join(
            randomizer.choice(alphabet)
            for _ in range(randomizer.randint(0, 300))
        )
        deterministic_parse(text)
