from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from decimal import Decimal

import pytest

from app.core.config import settings
from app.domains.copy_trading.engine import (
    ParsedSignal,
    RouteExecutionPolicy,
    SignalAction,
    validate_signal,
)
from app.domains.copy_trading.models import CopyActivityLevel
from app.domains.copy_trading.workers import (
    AiAction,
    _activity,
    _is_user_visible_status,
    _parse_message,
)


@patch("langchain_openai.ChatOpenAI")
def test_parser_uses_configured_bounded_timeout_and_retry(model_class):
    parser = model_class.return_value.with_structured_output.return_value
    parser.invoke.return_value = AiAction(
        action="open_market",
        symbol="XAUUSD",
        direction="buy",
        confidence=0.95,
    )

    parsed = _parse_message("Maybe buy gold if it rejects the zone", {})

    assert parsed.symbol == "XAUUSD"
    assert model_class.call_args.kwargs["timeout"] == settings.COPY_TRADING_AI_TIMEOUT_SECONDS
    assert model_class.call_args.kwargs["max_retries"] == settings.COPY_TRADING_AI_MAX_RETRIES
    assert settings.COPY_TRADING_AI_TIMEOUT_SECONDS > 0.7


@patch("langchain_openai.ChatOpenAI")
def test_parser_skips_remote_ai_for_explicit_signal(model_class):
    parsed = _parse_message("BUY XAUUSD SL 2310 TP 2350", {})

    assert parsed.action == SignalAction.open_market
    assert parsed.stop_loss == Decimal("2310")
    assert parsed.take_profits == [Decimal("2350")]
    model_class.assert_not_called()


@patch("langchain_openai.ChatOpenAI")
def test_parser_preserves_sl_and_tp_with_at_separator(model_class):
    parsed = _parse_message("SELL XAUUSD now\nSL at 4024\nTP at 4044", {})

    assert parsed.action == SignalAction.open_market
    assert parsed.symbol == "XAUUSD"
    assert parsed.direction == "sell"
    assert parsed.entry is None
    assert parsed.stop_loss == Decimal("4024")
    assert parsed.take_profits == [Decimal("4044")]
    model_class.assert_not_called()


@patch("langchain_openai.ChatOpenAI")
def test_parser_skips_remote_ai_for_ordinary_channel_commentary(model_class):
    parsed = _parse_message("EURUSD looks interesting, but this is not a signal.", {})

    assert parsed.action == SignalAction.status_only
    assert parsed.confidence == 0
    assert _is_user_visible_status(parsed) is False
    model_class.assert_not_called()


def test_only_explicit_symbol_status_updates_are_user_visible() -> None:
    assert _is_user_visible_status(
        AiAction(action="status_only", symbol="EURUSD", confidence=1)
    )
    assert not _is_user_visible_status(
        AiAction(action="status_only", symbol=None, confidence=0.2)
    )


@patch("app.domains.copy_trading.workers.SessionCipher")
@patch("app.domains.copy_trading.workers.CopyActivityEvent")
def test_signal_activity_encrypts_and_retains_the_source_message(
    activity_event_class, cipher_class
):
    cipher_class.return_value.encrypt.return_value = "encrypted-message"
    db = MagicMock()
    route = SimpleNamespace(
        id="route-id",
        user_id="user-id",
        source_id="source-id",
        target_connection_id="connection-id",
    )

    _activity(
        db,
        route=route,
        correlation_id="correlation-id",
        action="signal.failed",
        title="Signal analysis failed",
        level=CopyActivityLevel.error,
        details={"reason": "Request timed out"},
        raw_message="BUY XAUUSD",
    )

    assert (
        activity_event_class.call_args.kwargs["encrypted_raw_message"]
        == "encrypted-message"
    )
    db.add.assert_called_once_with(activity_event_class.return_value)
    cipher_class.return_value.encrypt.assert_called_once_with("BUY XAUUSD")


def test_low_confidence_complete_signal_is_advisory() -> None:
    signal = ParsedSignal(
        action=SignalAction.open_market,
        symbol="XAUUSD",
        direction="buy",
        stop_loss=Decimal("2315"),
        take_profits=[Decimal("2340")],
        confidence=0.42,
    )

    result = validate_signal(signal, RouteExecutionPolicy())

    assert result.accepted is True
    assert result.reason is None
    assert result.advisory == "AI confidence is low."


def test_low_confidence_does_not_override_missing_required_fields() -> None:
    signal = ParsedSignal(
        action=SignalAction.open_market,
        symbol="XAUUSD",
        direction="buy",
        confidence=0.42,
    )

    result = validate_signal(signal, RouteExecutionPolicy())

    assert result.accepted is False
    assert result.reason == "Signal is waiting for required trade details."


def test_pending_order_always_requires_entry_price() -> None:
    signal = ParsedSignal(
        action=SignalAction.place_pending,
        symbol="XAUUSD",
        direction="buy",
        confidence=1,
    )

    result = validate_signal(
        signal,
        RouteExecutionPolicy(minimum_fields="direction_symbol"),
    )

    assert result.accepted is False
    assert result.reason == "Pending order is waiting for an entry price."


def test_additional_tp_requires_complete_new_leg() -> None:
    signal = ParsedSignal(
        action=SignalAction.additional_tp,
        symbol="XAUUSD",
        direction="buy",
        confidence=1,
    )

    result = validate_signal(signal, RouteExecutionPolicy())

    assert result.accepted is False
    assert result.reason == "Additional take profit is waiting for a target price."


@pytest.mark.parametrize("fraction", [Decimal("0"), Decimal("1.01"), Decimal("-0.1")])
def test_partial_close_fraction_must_be_safe(fraction) -> None:
    signal = ParsedSignal(
        action=SignalAction.partial_close,
        symbol="XAUUSD",
        close_fraction=fraction,
        confidence=1,
    )

    result = validate_signal(signal, RouteExecutionPolicy())

    assert result.accepted is False
    assert result.reason == "Partial close must be greater than 0% and at most 100%."
