from dataclasses import replace
from datetime import datetime, timedelta, timezone
import uuid

from app.domains.copy_trading.assembly import (
    ConversationCandidate,
    choose_conversation,
    route_deadline,
)
from app.domains.copy_trading.generations import (
    corrective_action_for_submitted_edit,
    merge_generation_context,
)


NOW = datetime(2026, 6, 22, tzinfo=timezone.utc)


def candidate(
    *,
    symbol: str | None,
    direction: str | None,
    root: int,
    last: int | None = None,
    opening_submitted: bool = False,
    has_active_trade: bool = True,
) -> ConversationCandidate:
    return ConversationCandidate(
        id=uuid.uuid4(),
        reply_root_message_id=root,
        last_message_id=last or root,
        symbol=symbol,
        direction=direction,
        updated_at=NOW,
        opening_submitted=opening_submitted,
        has_active_trade=has_active_trade,
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


def test_later_sl_enriches_incomplete_open_instead_of_replacing_action() -> None:
    merged = merge_generation_context(
        {
            "action": "open_market",
            "direction": "buy",
            "symbol": "XAUUSD",
        },
        {
            "action": "modify_sl_tp",
            "stop_loss": "2315",
        },
        opening_submitted=False,
    )

    assert merged["action"] == "open_market"
    assert merged["stop_loss"] == "2315"


def test_later_sl_remains_management_action_after_open_submission() -> None:
    merged = merge_generation_context(
        {
            "action": "open_market",
            "direction": "buy",
            "symbol": "XAUUSD",
        },
        {
            "action": "modify_sl_tp",
            "stop_loss": "2315",
        },
        opening_submitted=True,
    )

    assert merged["action"] == "modify_sl_tp"
    assert merged["symbol"] == "XAUUSD"


def test_new_open_does_not_reuse_a_submitted_conversation() -> None:
    submitted = candidate(
        symbol="XAUUSD",
        direction="buy",
        root=10,
        opening_submitted=True,
    )

    result = choose_conversation(
        reply_to_message_id=None,
        symbol="XAUUSD",
        direction="buy",
        action="open_market",
        candidates=[submitted],
    )

    assert result.selected is None
    assert result.ambiguous is False


def test_management_update_can_reuse_a_submitted_conversation() -> None:
    submitted = candidate(
        symbol="XAUUSD",
        direction="buy",
        root=10,
        opening_submitted=True,
    )

    result = choose_conversation(
        reply_to_message_id=None,
        symbol="XAUUSD",
        direction=None,
        action="modify_sl_tp",
        candidates=[submitted],
    )

    assert result.selected == submitted


def test_management_ignores_failed_attempt_when_one_live_trade_matches() -> None:
    failed = candidate(
        symbol="EURUSD",
        direction="sell",
        root=10,
        opening_submitted=True,
        has_active_trade=False,
    )
    live = candidate(
        symbol="EURUSD",
        direction="sell",
        root=20,
        opening_submitted=True,
        has_active_trade=True,
    )

    result = choose_conversation(
        reply_to_message_id=None,
        symbol="EURUSD",
        direction=None,
        action="modify_sl_tp",
        candidates=[failed, live],
    )

    assert result.selected == live
    assert result.ambiguous is False


def test_management_update_still_enriches_an_incomplete_opening() -> None:
    incomplete = candidate(
        symbol="EURUSD",
        direction="buy",
        root=10,
        opening_submitted=False,
        has_active_trade=False,
    )

    result = choose_conversation(
        reply_to_message_id=None,
        symbol="EURUSD",
        direction=None,
        action="modify_sl_tp",
        candidates=[incomplete],
    )

    assert result.selected == incomplete


def test_sl_tp_fragment_prefers_incomplete_opening_over_live_trade() -> None:
    incomplete = candidate(
        symbol="EURUSD",
        direction="sell",
        root=30,
        opening_submitted=False,
        has_active_trade=False,
    )
    live = candidate(
        symbol="EURUSD",
        direction="sell",
        root=20,
        opening_submitted=True,
        has_active_trade=True,
    )

    result = choose_conversation(
        reply_to_message_id=None,
        message_id=31,
        symbol=None,
        direction=None,
        action="modify_sl_tp",
        candidates=[live, incomplete],
    )

    assert result.selected == incomplete
    assert result.ambiguous is False


def test_break_even_does_not_attach_to_incomplete_opening() -> None:
    incomplete = candidate(
        symbol="EURUSD",
        direction="sell",
        root=30,
        opening_submitted=False,
        has_active_trade=False,
    )
    live = candidate(
        symbol="EURUSD",
        direction="sell",
        root=20,
        opening_submitted=True,
        has_active_trade=True,
    )

    result = choose_conversation(
        reply_to_message_id=None,
        symbol="EURUSD",
        direction="sell",
        action="break_even",
        candidates=[incomplete, live],
    )

    assert result.selected == live


def test_opening_message_claims_only_unresolved_symbol_less_conversation() -> None:
    unresolved = candidate(symbol=None, direction=None, root=10)

    result = choose_conversation(
        reply_to_message_id=None,
        symbol="XAUUSD",
        direction="buy",
        candidates=[unresolved],
        action="open_market",
    )

    assert result.selected == unresolved
    assert result.ambiguous is False


def test_opening_message_does_not_guess_between_symbol_less_conversations() -> None:
    unresolved = [
        candidate(symbol=None, direction=None, root=10),
        candidate(symbol=None, direction=None, root=11),
    ]

    result = choose_conversation(
        reply_to_message_id=None,
        symbol="XAUUSD",
        direction="buy",
        candidates=unresolved,
        action="open_market",
    )

    assert result.selected is None
    assert result.ambiguous is True


def test_edit_reuses_its_submitted_conversation() -> None:
    submitted = candidate(
        symbol="XAUUSD",
        direction="buy",
        root=10,
        opening_submitted=True,
    )

    result = choose_conversation(
        reply_to_message_id=None,
        message_id=10,
        is_edit=True,
        symbol="XAUUSD",
        direction="buy",
        action="open_market",
        candidates=[submitted],
    )

    assert result.selected == submitted


def test_submitted_open_edit_becomes_sl_tp_correction() -> None:
    update = corrective_action_for_submitted_edit(
        {
            "action": "open_market",
            "symbol": "XAUUSD",
            "direction": "buy",
            "stop_loss": "2310",
            "take_profits": ["2340"],
        },
        {
            "action": "open_market",
            "symbol": "XAUUSD",
            "direction": "buy",
            "stop_loss": "2315",
            "take_profits": ["2340"],
        },
    )

    assert update["action"] == "modify_sl_tp"
    assert update["stop_loss"] == "2315"


def test_submitted_open_edit_without_management_change_is_status_only() -> None:
    context = {
        "action": "open_market",
        "symbol": "XAUUSD",
        "direction": "buy",
        "stop_loss": "2315",
        "take_profits": ["2340"],
    }

    update = corrective_action_for_submitted_edit(context, dict(context))

    assert update["action"] == "status_only"
