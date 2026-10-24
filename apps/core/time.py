"""Time helpers shared by selectors and services. Pure stdlib (no Django)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

UK_TIMEZONE_NAME = "Europe/London"
UK_TZ = ZoneInfo(UK_TIMEZONE_NAME)


def uk_midnight_utc(day: date) -> datetime:
    """The UTC instant at which the UK calendar day `day` starts."""
    return datetime.combine(day, time(0, 0), tzinfo=UK_TZ).astimezone(UTC)


def delivery_window_utc(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    """Half-open UTC range [start, end) covering the inclusive UK delivery dates.

    Correct across clock changes: a UK day can be 23, 24 or 25 hours long.
    """
    if date_from > date_to:
        raise ValueError("date_from must be on or before date_to")
    return uk_midnight_utc(date_from), uk_midnight_utc(date_to + timedelta(days=1))


def iso_z(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("refusing to format a naive datetime")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
