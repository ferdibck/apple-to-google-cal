from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import EventDateTime


def utc_now() -> datetime:
    return datetime.now(UTC)


def event_datetime_to_utc(value: EventDateTime, default_timezone: str) -> datetime:
    if isinstance(value.value, datetime):
        dt = value.value
        if dt.tzinfo is None:
            zone = ZoneInfo(value.time_zone or default_timezone)
            dt = dt.replace(tzinfo=zone)
        return dt.astimezone(UTC)

    zone = ZoneInfo(value.time_zone or default_timezone)
    return datetime.combine(value.value, time.min, tzinfo=zone).astimezone(UTC)


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(UTC)


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def window_bounds(
    past_days: int, future_days: int, now: datetime | None = None
) -> tuple[datetime, datetime]:
    current = now or utc_now()
    return (
        current.astimezone(UTC) - timedelta(days=past_days),
        current.astimezone(UTC) + timedelta(days=future_days),
    )
