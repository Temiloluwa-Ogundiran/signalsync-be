"""Unit tests for the trade-review fallback (no LLM / DB)."""
from app.domains.ai.service._trade_review import _fallback_review


def test_fallback_review_formats_a_winning_trade():
    review, insight = _fallback_review(
        {
            "symbol": "XAUUSDm",
            "direction": "buy",
            "net_profit": 142.5,
            "hold_min": 15.0,
        }
    )
    assert "XAUUSDm" in review
    assert "+$142.50" in review
    assert "15.0 minutes" in review
    assert insight == ""


def test_fallback_review_formats_a_losing_trade():
    review, _ = _fallback_review(
        {
            "symbol": "BTCUSDm",
            "direction": "sell",
            "net_profit": -300.0,
            "hold_min": 8.0,
        }
    )
    assert "-$300.00" in review
    assert "sell" in review


def test_fallback_review_tolerates_missing_fields():
    review, insight = _fallback_review({})
    # No symbol/direction/net — must not raise and must produce text.
    assert isinstance(review, str) and review
    assert insight == ""
