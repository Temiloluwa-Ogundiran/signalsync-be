import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domains.copy_trading.metaapi_broker import MetaApiBroker
from app.domains.copy_trading.quality import ExecutionQualityError, check_execution_quality, within_trading_hours
from app.domains.copy_trading.symbols import BrokerSymbol
from app.domains.copy_trading.telemetry import percentile


SYMBOL = BrokerSymbol(name="EURUSD", contract_size=Decimal("100000"), point=Decimal("0.00001"))
NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)


def quality(**overrides):
    values = {
        "price": {"bid": 1.10000, "ask": 1.10010, "time": NOW},
        "symbol": SYMBOL,
        "direction": "buy",
        "entry": Decimal("1.10005"),
        "max_spread_points": 20,
        "max_slippage_points": 20,
        "max_quote_age_seconds": 10,
        "high_spread_behavior": "reject",
        "trading_start_hour_utc": None,
        "trading_end_hour_utc": None,
        "now": NOW,
    }
    values.update(overrides)
    return check_execution_quality(**values)


def test_quality_accepts_fresh_quote_inside_limits() -> None:
    result = quality()
    assert result.spread_points == Decimal("10")
    assert result.slippage_points == Decimal("5")


def test_stale_quote_is_retryable() -> None:
    with pytest.raises(ExecutionQualityError) as caught:
        quality(price={"bid": 1.1, "ask": 1.1001, "time": NOW - timedelta(seconds=11)})
    assert caught.value.code == "STALE_QUOTE"
    assert caught.value.retryable is True


@pytest.mark.parametrize(
    "price",
    [
        {},
        {"bid": None, "ask": 1.1001, "time": NOW},
        {"bid": "not-a-price", "ask": 1.1001, "time": NOW},
    ],
)
def test_missing_or_invalid_quote_is_a_safe_retryable_error(price) -> None:
    with pytest.raises(ExecutionQualityError) as caught:
        quality(price=price)

    assert caught.value.code == "QUOTE_UNAVAILABLE"
    assert caught.value.retryable is True
    assert caught.value.user_action_required is False
    assert "decimal" not in str(caught.value).lower()


def test_broker_subscribes_before_returning_an_uncached_quote() -> None:
    class TerminalState:
        def __init__(self):
            self.value = None

        def price(self, _symbol):
            return self.value

    class Connection:
        def __init__(self):
            self.terminal_state = TerminalState()
            self.calls = []

        async def subscribe_to_market_data(self, symbol, subscriptions, timeout_in_seconds, wait_for_quote):
            self.calls.append((symbol, subscriptions, timeout_in_seconds, wait_for_quote))
            self.terminal_state.value = {"bid": 1.1, "ask": 1.1001, "time": NOW}

    connection = Connection()
    result = asyncio.run(MetaApiBroker(connection).ensure_price("EURUSD", timeout_seconds=2))

    assert result["bid"] == 1.1
    assert connection.calls == [("EURUSD", [{"type": "quotes"}], 2, True)]


def test_wide_spread_waits_only_when_user_selected_wait() -> None:
    with pytest.raises(ExecutionQualityError) as caught:
        quality(max_spread_points=5, high_spread_behavior="wait")
    assert caught.value.code == "SPREAD_TOO_WIDE"
    assert caught.value.retryable is True


def test_slippage_and_overnight_window_are_enforced() -> None:
    with pytest.raises(ExecutionQualityError, match="signal entry"):
        quality(entry=Decimal("1.09900"), max_slippage_points=10)
    assert within_trading_hours(NOW.replace(hour=23), 22, 6)
    assert within_trading_hours(NOW.replace(hour=3), 22, 6)
    assert not within_trading_hours(NOW.replace(hour=12), 22, 6)


def test_latency_percentiles_are_deterministic() -> None:
    values = [100, 200, 300, 400, 500]
    assert percentile(values, 0.5) == 300
    assert percentile(values, 0.95) == 500
    assert percentile([], 0.5) is None
