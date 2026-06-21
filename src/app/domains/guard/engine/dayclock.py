"""Firm-day boundaries.

A firm reset at 00:00 *server time* is not broker time, not UTC, and not the
trader's local time. We anchor the trading day to the firm's own clock and store
it explicitly. Two ticks belong to the same firm-day iff they fall in the same
[reset, next reset) window in the firm's timezone.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def firm_day_key(ts: datetime, reset_hour: int, tz_name: str,
                 reset_minute: int = 0) -> str:
    """A stable key identifying the firm-day that ``ts`` falls in.

    Day rolls at ``reset_hour:reset_minute`` in ``tz_name`` (firms reset at e.g.
    16:59 EST, not just on the hour). A tick before the reset time belongs to the
    previous calendar day's session.
    """
    local = _to_tz(ts, tz_name)
    session_date = local.date()
    if local.time() < time(hour=reset_hour, minute=reset_minute):
        session_date = (local - timedelta(days=1)).date()
    return session_date.isoformat()


def _to_tz(ts: datetime, tz_name: str) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return ts.astimezone(ZoneInfo(tz_name))
