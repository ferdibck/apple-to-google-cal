from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .models import (
    PRIVATE_SOURCE_HASH_KEY,
    PRIVATE_SOURCE_ID_KEY,
    PRIVATE_SOURCE_KEY,
    PRIVATE_SOURCE_VALUE,
    EventDateTime,
    SourceEvent,
)


def to_google_event(source: SourceEvent) -> dict[str, Any]:
    body: dict[str, Any] = {
        "summary": source.summary or "(No title)",
        "extendedProperties": {
            "private": {
                PRIVATE_SOURCE_KEY: PRIVATE_SOURCE_VALUE,
                PRIVATE_SOURCE_ID_KEY: source.source_key,
                PRIVATE_SOURCE_HASH_KEY: source.source_hash,
            }
        },
        "reminders": {"useDefault": False},
    }
    if source.description:
        body["description"] = source.description
    if source.location:
        body["location"] = source.location
    if source.url:
        body["source"] = {"url": source.url, "title": "iCloud event"}
    if source.transparency:
        transparency = source.transparency.upper()
        if transparency == "TRANSPARENT":
            body["transparency"] = "transparent"
        elif transparency == "OPAQUE":
            body["transparency"] = "opaque"
    if source.recurrence:
        body["recurrence"] = list(source.recurrence)

    body["start"] = _google_datetime(source.start)
    body["end"] = _google_datetime(source.end)
    return body


def _google_datetime(value: EventDateTime) -> dict[str, str]:
    raw = value.value
    if value.all_day or (isinstance(raw, date) and not isinstance(raw, datetime)):
        return {"date": raw.isoformat()}
    if raw.tzinfo is not None:
        payload = {"dateTime": raw.isoformat()}
    else:
        payload = {"dateTime": raw.isoformat()}
    if value.time_zone:
        payload["timeZone"] = value.time_zone
    return payload
