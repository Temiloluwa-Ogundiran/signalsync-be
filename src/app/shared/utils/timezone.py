from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc


def validate_timezone_name(tz_name: str) -> str:
    """Validate an IANA timezone and return it unchanged if valid."""
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid timezone: {tz_name}") from exc
    return tz_name


def normalize_broker_datetime_to_utc(value: datetime, broker_utc_offset_minutes: int) -> datetime:
    """
    Normalize broker-provided timestamps to UTC.

    Rules:
    - If value is timezone-aware, convert to UTC directly.
    - If value is naive, treat it as broker-local clock time and subtract the
      broker UTC offset (in minutes) to get UTC.
    """
    if value.tzinfo is not None:
        return value.astimezone(UTC)

    return (value - timedelta(minutes=broker_utc_offset_minutes)).replace(tzinfo=UTC)


def to_account_local_date(value_utc: datetime, account_timezone: str) -> date:
    """Convert a UTC datetime to account-local calendar date."""
    if value_utc.tzinfo is None:
        value_utc = value_utc.replace(tzinfo=UTC)
    return value_utc.astimezone(ZoneInfo(account_timezone)).date()


def to_account_local_datetime(value_utc: datetime, account_timezone: str) -> datetime:
    """Convert a UTC datetime to an account-local timezone-aware datetime."""
    if value_utc.tzinfo is None:
        value_utc = value_utc.replace(tzinfo=UTC)
    return value_utc.astimezone(ZoneInfo(account_timezone))


def local_date_to_utc_range(local_day: date, account_timezone: str) -> tuple[datetime, datetime]:
    """
    Convert an account-local calendar date to a UTC [start, end) range.

    Returns:
    - start_utc: inclusive UTC start
    - end_utc: exclusive UTC end (start of next local day)
    """
    zone = ZoneInfo(account_timezone)
    start_local = datetime.combine(local_day, time.min, tzinfo=zone)
    next_day_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), next_day_local.astimezone(UTC)


def classify_session(opened_at_utc: datetime) -> str:
    """Classify trade session strictly by UTC hour."""
    if opened_at_utc.tzinfo is None:
        opened_at_utc = opened_at_utc.replace(tzinfo=UTC)

    hour = opened_at_utc.astimezone(UTC).hour

    if 13 <= hour < 16:
        return "london_ny_overlap"
    if 16 <= hour < 21:
        return "new_york"
    if 8 <= hour < 16:
        return "london"
    if 0 <= hour < 8:
        return "asian"
    return "off_hours"
