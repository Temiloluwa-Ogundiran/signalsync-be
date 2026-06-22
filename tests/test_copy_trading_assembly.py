from dataclasses import replace
from datetime import datetime, timedelta, timezone
import uuid

from app.domains.copy_trading.assembly import (
    ConversationCandidate,
    choose_conversation,
    route_deadline,
)


NOW = datetime(2026, 6, 22, tzinfo=timezone.utc)


def candidate(
    *,
    symbol: str | None,
    direction: str | None,
    root: int,
    last: int | None = None,
) -> ConversationCandidate:
    return ConversationCandidate(
        id=uuid.uuid4(),
        reply_root_message_id=root,
        last_message_id=last or root,
        symbol=symbol,
        direction=direction,
        updated_at=NOW,
    )


def test_reply_chain_wins_over_symbol_guessing() -> None:
    eur = candidate(symbol="EURUSD", direction="buy", root=10, last=11)
    gold = candidate(symbol="XAUUSD", direction="sell", root=20)

    result = choose_conversation(
        reply_to_message_id=11,
        symbol=None,
        direction=None,
        candidates=[gold, eur],
    )

    assert result.selected == eur
    assert result.ambiguous is False


def test_different_symbols_do_not_share_conversations() -> None:
    eur = candidate(symbol="EURUSD", direction="buy", root=10)

    result = choose_conversation(
        reply_to_message_id=None,
        symbol="XAUUSD",
        direction="sell",
        candidates=[eur],
    )

    assert result.selected is None
    assert result.ambiguous is False


def test_management_update_with_multiple_candidates_is_ambiguous() -> None:
    eur = candidate(symbol="EURUSD", direction="buy", root=10)
    gold = candidate(symbol="XAUUSD", direction="sell", root=20)

    result = choose_conversation(
        reply_to_message_id=None,
        symbol=None,
        direction=None,
        candidates=[eur, gold],
    )

    assert result.selected is None
    assert result.ambiguous is True


def test_direction_must_match_when_the_message_supplies_it() -> None:
    buy = candidate(symbol="EURUSD", direction="buy", root=10)

    result = choose_conversation(
        reply_to_message_id=None,
        symbol="EURUSD",
        direction="sell",
        candidates=[buy],
    )

    assert result.selected is None


def test_each_route_keeps_its_own_assembly_deadline() -> None:
    short = route_deadline(NOW, 30)
    long = route_deadline(NOW, 90)

    assert short == NOW + timedelta(seconds=30)
    assert long - short == timedelta(seconds=60)
