from datetime import date
from types import SimpleNamespace

from app.domains.demo.service import latest_trading_day


def test_latest_trading_day_uses_fallback_when_history_is_empty() -> None:
    fallback = date(2026, 7, 18)
    data = SimpleNamespace(days=[])

    assert latest_trading_day(data, fallback=fallback) == fallback


def test_latest_trading_day_uses_latest_day_with_trades() -> None:
    data = SimpleNamespace(
        days=[
            SimpleNamespace(day=date(2026, 7, 16), trades=[object()]),
            SimpleNamespace(day=date(2026, 7, 17), trades=[]),
            SimpleNamespace(day=date(2026, 7, 18), trades=[object()]),
        ]
    )

    assert latest_trading_day(data, fallback=date(2026, 7, 1)) == date(2026, 7, 18)
