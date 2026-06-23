from time import perf_counter

import pytest
from pydantic import ValidationError

from app.domains.copy_trading.parser import AiAction, deterministic_parse
from tests.fixtures.copy_signal_corpus import AI_FALLBACK_CASES, CLEAR_SIGNAL_CASES


@pytest.mark.parametrize("case", CLEAR_SIGNAL_CASES, ids=lambda case: case.text)
def test_clear_signal_formats_use_deterministic_parser(case) -> None:
    parsed = deterministic_parse(case.text)

    assert parsed is not None
    assert parsed.action == case.action
    assert parsed.symbol == case.symbol
    assert parsed.direction == case.direction
    assert parsed.entry == case.entry
    assert parsed.entry_high == case.entry_high
    assert parsed.stop_loss == case.stop_loss
    assert parsed.take_profits == case.take_profits
    assert parsed.close_fraction == case.close_fraction


@pytest.mark.parametrize("text", AI_FALLBACK_CASES)
def test_ambiguous_or_non_signal_text_does_not_use_fast_path(text: str) -> None:
    assert deterministic_parse(text) is None


def test_fast_path_preserves_decimal_precision() -> None:
    parsed = deterministic_parse(
        "BUY EURUSD @ 1.07123 SL 1.06987 TP1 1.07456 TP2 1.08001"
    )

    assert str(parsed.entry) == "1.07123"
    assert str(parsed.stop_loss) == "1.06987"
    assert [str(value) for value in parsed.take_profits] == ["1.07456", "1.08001"]


def test_fast_path_p95_is_below_ten_milliseconds() -> None:
    samples = []
    for _ in range(100):
        started = perf_counter()
        deterministic_parse("BUY XAUUSD ENTRY 2310-2315 SL 2295 TP1 2330 TP2 2350")
        samples.append((perf_counter() - started) * 1000)

    samples.sort()
    assert samples[94] < 10


@pytest.mark.parametrize(
    ("text", "action", "symbol", "direction", "entry", "order_type"),
    [
        ("LONG XAUUSD", "open_market", "XAUUSD", "buy", None, None),
        ("XAUUSD SHORT", "open_market", "XAUUSD", "sell", None, None),
        ("BUY XAUUSD LIMIT 2300", "place_pending", "XAUUSD", "buy", "2300", "limit"),
        ("XAUUSD BUY LIMIT 2300", "place_pending", "XAUUSD", "buy", "2300", "limit"),
        ("SELL EURUSD STOP 1.0800", "place_pending", "EURUSD", "sell", "1.0800", "stop"),
        ("\U0001f525 buy nas100 \U0001f525", "open_market", "NAS100", "buy", None, None),
    ],
)
def test_common_direction_and_pending_order_variants(
    text, action, symbol, direction, entry, order_type
) -> None:
    parsed = deterministic_parse(text)

    assert parsed is not None
    assert parsed.action.value == action
    assert parsed.symbol == symbol
    assert parsed.direction == direction
    parsed_entry = str(parsed.entry) if parsed.entry is not None else None
    assert parsed_entry == entry
    assert parsed.order_type == order_type


@pytest.mark.parametrize(
    "text",
    [
        "BUY XAUUSD 2310",
        "SELL EURUSD 1.0800-1.0810",
        "BUY GOLD ABOVE 2310",
    ],
)
def test_unlabelled_entry_prices_fall_back_to_ai(text: str) -> None:
    assert deterministic_parse(text) is None


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
@pytest.mark.parametrize("symbol", ["EURUSD", "GBPJPY", "XAUUSD", "BTCUSD", "US30"])
@pytest.mark.parametrize("separator", [" ", "\n", " : "])
def test_explicit_market_signal_format_matrix(direction, symbol, separator) -> None:
    parsed = deterministic_parse(f"{direction}{separator}{symbol}")

    assert parsed is not None
    assert parsed.action.value == "open_market"
    assert parsed.symbol == symbol
    assert parsed.direction == direction.lower()


def test_ai_schema_rejects_unsupported_direction() -> None:
    with pytest.raises(ValidationError):
        AiAction(
            action="open_market",
            symbol="XAUUSD",
            direction="sideways",
            confidence=1,
        )
