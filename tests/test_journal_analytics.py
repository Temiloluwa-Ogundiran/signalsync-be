"""
Unit tests for the analytics pure-computation layer.

_compute_summary and _compute_time_performance have no DB dependency — they
receive a list of trade objects and return typed response models.  All other
analytics service functions are thin wrappers that fetch data then delegate
to these two, so thorough coverage here exercises the core logic.
"""
import pytest
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

# SQLAlchemy mapper dependencies — must be imported before app service modules
import app.domains.journal.models   # noqa: F401
import app.domains.auth.models      # noqa: F401

from app.domains.journal.service._analytics import (
    _compute_summary,
    _compute_time_performance,
)

_UTC = ZoneInfo("UTC")


# ── shared factory helpers ─────────────────────────────────────────────────────

def _dt(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=_UTC)


def _trade(
    net_profit: float,
    closed_at: datetime,
    *,
    opened_at: datetime | None = None,
    duration_seconds: int = 60,
) -> SimpleNamespace:
    return SimpleNamespace(
        net_profit=Decimal(str(net_profit)),
        closed_at=closed_at,
        opened_at=opened_at or closed_at,
        duration_seconds=duration_seconds,
    )


# ── _compute_summary ───────────────────────────────────────────────────────────

class TestComputeSummary:
    def test_empty_trades_returns_zeroes(self):
        r = _compute_summary(trades=[], starting_balance=Decimal("10000"))
        assert r.total_trades == 0
        assert r.win_rate == 0.0
        assert r.profit_factor is None  # no losses → ∞, surfaced as None (JSON-safe)
        assert r.avg_win == 0.0
        assert r.avg_loss == 0.0
        assert r.total_net_pnl == 0.0
        assert r.max_drawdown == 0.0
        assert r.avg_trade_duration_seconds == 0.0
        assert r.best_day is None
        assert r.worst_day is None

    def test_all_wins(self):
        trades = [_trade(+5, _dt(2025, 1, 1)), _trade(+10, _dt(2025, 1, 2))]
        r = _compute_summary(trades=trades, starting_balance=Decimal("100"))
        assert r.win_rate == 100.0
        assert r.profit_factor is None  # no losses → ∞, surfaced as None (JSON-safe)
        assert r.avg_win == pytest.approx(7.5)
        assert r.avg_loss == 0.0
        assert r.total_net_pnl == pytest.approx(15.0)

    def test_all_losses(self):
        trades = [_trade(-5, _dt(2025, 1, 1)), _trade(-10, _dt(2025, 1, 2))]
        r = _compute_summary(trades=trades, starting_balance=Decimal("100"))
        assert r.win_rate == 0.0
        assert r.profit_factor == pytest.approx(0.0)
        assert r.avg_win == 0.0
        assert r.avg_loss == pytest.approx(-7.5)

    def test_mixed_trades_core_stats(self):
        # wins: +10, +7 → gross_win=17; losses: -3, -2 → gross_loss=5
        trades = [
            _trade(+10, _dt(2025, 1, 1)),
            _trade(-3,  _dt(2025, 1, 2)),
            _trade(+7,  _dt(2025, 1, 3)),
            _trade(-2,  _dt(2025, 1, 4)),
        ]
        r = _compute_summary(trades=trades, starting_balance=Decimal("100"))
        assert r.total_trades == 4
        assert r.win_rate == pytest.approx(50.0)
        assert r.profit_factor == pytest.approx(17 / 5)
        assert r.avg_win == pytest.approx(8.5)
        assert r.avg_loss == pytest.approx(-2.5)
        assert r.total_net_pnl == pytest.approx(12.0)

    def test_max_drawdown_tracks_peak_to_trough(self):
        # cumulative after each trade: 10, 5, 8, -12, -11
        # running max:                  10, 10, 10,  10,  10
        # drawdown from max:             0,  5,  2,  22,  21  → max = 22
        trades = [
            _trade(+10, _dt(2025, 1, 1)),
            _trade(-5,  _dt(2025, 1, 2)),
            _trade(+3,  _dt(2025, 1, 3)),
            _trade(-20, _dt(2025, 1, 4)),
            _trade(+1,  _dt(2025, 1, 5)),
        ]
        r = _compute_summary(trades=trades, starting_balance=Decimal("100"))
        assert r.max_drawdown == pytest.approx(22.0)

    def test_max_drawdown_monotone_gains_is_zero(self):
        trades = [_trade(+5, _dt(2025, 1, i)) for i in range(1, 6)]
        r = _compute_summary(trades=trades, starting_balance=Decimal("100"))
        assert r.max_drawdown == pytest.approx(0.0)

    def test_best_and_worst_day_aggregated_by_utc_date(self):
        # Jan 10: +20 then -5 → net +15  (best)
        # Jan 11: -30          → net -30 (worst)
        trades = [
            _trade(+20, _dt(2025, 1, 10, 10)),
            _trade(-5,  _dt(2025, 1, 10, 15)),
            _trade(-30, _dt(2025, 1, 11, 9)),
        ]
        r = _compute_summary(trades=trades, starting_balance=Decimal("100"))
        assert r.best_day.date == date(2025, 1, 10)
        assert float(r.best_day.pnl) == pytest.approx(15.0)
        assert r.worst_day.date == date(2025, 1, 11)
        assert float(r.worst_day.pnl) == pytest.approx(-30.0)

    def test_best_and_worst_day_single_trade(self):
        trades = [_trade(+10, _dt(2025, 1, 1))]
        r = _compute_summary(trades=trades, starting_balance=Decimal("100"))
        assert r.best_day.date == date(2025, 1, 1)
        assert r.worst_day.date == date(2025, 1, 1)

    def test_zero_starting_balance_does_not_raise(self):
        trades = [_trade(+10, _dt(2025, 1, 1))]
        r = _compute_summary(trades=trades, starting_balance=Decimal("0"))
        assert r.starting_balance == 0.0
        assert r.total_net_pnl == pytest.approx(10.0)

    def test_avg_duration_seconds(self):
        trades = [
            _trade(+1, _dt(2025, 1, 1), duration_seconds=120),
            _trade(+1, _dt(2025, 1, 2), duration_seconds=60),
        ]
        r = _compute_summary(trades=trades, starting_balance=Decimal("100"))
        assert r.avg_trade_duration_seconds == pytest.approx(90.0)

    def test_starting_balance_reported_on_response(self):
        r = _compute_summary(trades=[], starting_balance=Decimal("5000"))
        assert r.starting_balance == pytest.approx(5000.0)


# ── _compute_time_performance ──────────────────────────────────────────────────

class TestComputeTimePerformance:
    def test_empty_returns_24_hourly_and_7_daily_buckets(self):
        r = _compute_time_performance(trades=[], account_timezone="UTC", time_basis="close")
        assert len(r.hourly) == 24
        assert len(r.daily) == 7

    def test_empty_all_buckets_are_zero(self):
        r = _compute_time_performance(trades=[], account_timezone="UTC", time_basis="close")
        assert all(p.trade_count == 0 and p.total_pnl == 0.0 for p in r.hourly)
        assert all(p.trade_count == 0 and p.total_pnl == 0.0 for p in r.daily)

    def test_daily_bucket_order(self):
        r = _compute_time_performance(trades=[], account_timezone="UTC", time_basis="close")
        assert [p.bucket for p in r.daily] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def test_hourly_bucket_labels_are_zero_padded(self):
        r = _compute_time_performance(trades=[], account_timezone="UTC", time_basis="close")
        assert r.hourly[0].bucket == "00"
        assert r.hourly[9].bucket == "09"
        assert r.hourly[23].bucket == "23"

    def test_hourly_grouping_utc(self):
        # Two trades at 14:xx UTC, one at 09:xx UTC — account TZ = UTC
        trades = [
            _trade(+10, _dt(2025, 3, 3, 14)),
            _trade(-3,  _dt(2025, 3, 3, 14)),
            _trade(+5,  _dt(2025, 3, 4, 9)),
        ]
        r = _compute_time_performance(trades=trades, account_timezone="UTC", time_basis="close")

        h14 = next(p for p in r.hourly if p.bucket == "14")
        assert h14.trade_count == 2
        assert h14.total_pnl == pytest.approx(7.0)
        assert h14.win_rate == pytest.approx(50.0)
        assert h14.avg_pnl == pytest.approx(3.5)

        h09 = next(p for p in r.hourly if p.bucket == "09")
        assert h09.trade_count == 1
        assert h09.total_pnl == pytest.approx(5.0)
        assert h09.win_rate == pytest.approx(100.0)

    def test_weekday_grouping(self):
        # 2025-03-03 = Monday, 2025-03-07 = Friday
        trades = [
            _trade(+10, _dt(2025, 3, 3, 12)),
            _trade(+5,  _dt(2025, 3, 7, 12)),
            _trade(-2,  _dt(2025, 3, 7, 15)),
        ]
        r = _compute_time_performance(trades=trades, account_timezone="UTC", time_basis="close")
        mon = next(p for p in r.daily if p.bucket == "Mon")
        fri = next(p for p in r.daily if p.bucket == "Fri")
        assert mon.trade_count == 1
        assert mon.total_pnl == pytest.approx(10.0)
        assert fri.trade_count == 2
        assert fri.total_pnl == pytest.approx(3.0)

    def test_time_basis_open_uses_opened_at(self):
        # opened_at = 08:xx, closed_at = 14:xx
        # time_basis="open" → assigned to hour 08
        # time_basis="close" → assigned to hour 14
        opened = _dt(2025, 3, 3, 8)
        closed = _dt(2025, 3, 3, 14)
        trade = _trade(+10, closed_at=closed, opened_at=opened)

        r_open  = _compute_time_performance(trades=[trade], account_timezone="UTC", time_basis="open")
        r_close = _compute_time_performance(trades=[trade], account_timezone="UTC", time_basis="close")

        assert next(p for p in r_open.hourly  if p.bucket == "08").trade_count == 1
        assert next(p for p in r_open.hourly  if p.bucket == "14").trade_count == 0
        assert next(p for p in r_close.hourly if p.bucket == "14").trade_count == 1
        assert next(p for p in r_close.hourly if p.bucket == "08").trade_count == 0

    def test_timezone_conversion_utc_to_eastern(self):
        # 14:00 UTC in March 2025 (before DST on Mar 9) = 09:00 America/New_York (EST = UTC-5)
        trade = _trade(+10, _dt(2025, 3, 3, 14))
        r_utc = _compute_time_performance(trades=[trade], account_timezone="UTC",              time_basis="close")
        r_ny  = _compute_time_performance(trades=[trade], account_timezone="America/New_York", time_basis="close")

        assert next(p for p in r_utc.hourly if p.bucket == "14").trade_count == 1
        assert next(p for p in r_ny.hourly  if p.bucket == "09").trade_count == 1
        assert next(p for p in r_ny.hourly  if p.bucket == "14").trade_count == 0

    def test_win_rate_in_bucket(self):
        # Three trades in same hour: 2 wins, 1 loss → 66.67%
        trades = [
            _trade(+5, _dt(2025, 1, 1, 10)),
            _trade(+3, _dt(2025, 1, 2, 10)),
            _trade(-4, _dt(2025, 1, 3, 10)),
        ]
        r = _compute_time_performance(trades=trades, account_timezone="UTC", time_basis="close")
        h10 = next(p for p in r.hourly if p.bucket == "10")
        assert h10.trade_count == 3
        assert h10.win_rate == pytest.approx(200 / 3)
