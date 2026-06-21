from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.core.config import settings
from app.domains.copy_trading.models import CopyActivityLevel
from app.domains.copy_trading.workers import AiAction, _activity, _parse_message


@patch("langchain_openai.ChatOpenAI")
def test_parser_uses_configured_bounded_timeout_and_retry(model_class):
    parser = model_class.return_value.with_structured_output.return_value
    parser.invoke.return_value = AiAction(
        action="open_market",
        symbol="XAUUSD",
        direction="buy",
        confidence=0.95,
    )

    parsed = _parse_message("BUY XAUUSD", {})

    assert parsed.symbol == "XAUUSD"
    assert model_class.call_args.kwargs["timeout"] == settings.COPY_TRADING_AI_TIMEOUT_SECONDS
    assert model_class.call_args.kwargs["max_retries"] == settings.COPY_TRADING_AI_MAX_RETRIES
    assert settings.COPY_TRADING_AI_TIMEOUT_SECONDS > 0.7


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
        target_account_id="account-id",
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
