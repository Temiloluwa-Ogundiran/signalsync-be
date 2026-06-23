from itertools import permutations

from app.domains.copy_trading.generations import merge_generation_context
from app.domains.copy_trading.parser import deterministic_parse


def _merge_messages(messages: tuple[str, ...]) -> dict:
    context = {}
    for message in messages:
        parsed = deterministic_parse(message)
        assert parsed is not None
        context = merge_generation_context(
            context,
            parsed.model_dump(mode="json"),
            opening_submitted=False,
        )
    return context


def test_open_sl_and_tp_can_arrive_in_any_order() -> None:
    messages = ("BUY XAUUSD", "SL 2310", "TP 2350")

    for ordering in permutations(messages):
        context = _merge_messages(ordering)
        assert context["action"] == "open_market", ordering
        assert context["symbol"] == "XAUUSD", ordering
        assert context["direction"] == "buy", ordering
        assert str(context["stop_loss"]) == "2310", ordering
        assert [str(value) for value in context["take_profits"]] == ["2350"], ordering


def test_multiple_take_profit_fragments_accumulate_before_opening() -> None:
    context = _merge_messages(("TP1 2330", "TP2 2350", "BUY XAUUSD", "SL 2290"))

    assert context["action"] == "open_market"
    assert [str(value) for value in context["take_profits"]] == ["2330", "2350"]


def test_duplicate_take_profit_fragment_is_idempotent() -> None:
    context = _merge_messages(("BUY XAUUSD", "TP1 2330", "TP1 2330"))

    assert [str(value) for value in context["take_profits"]] == ["2330"]
