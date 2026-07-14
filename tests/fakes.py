from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from icloud_gcal_mirror.config import AppConfig
from icloud_gcal_mirror.interfaces import SourceCalendarService, TargetCalendarService
from icloud_gcal_mirror.models import (
    PRIVATE_SOURCE_HASH_KEY,
    PRIVATE_SOURCE_ID_KEY,
    PRIVATE_SOURCE_KEY,
    PRIVATE_SOURCE_VALUE,
    CalendarRef,
    CalendarSyncState,
    ChangeBatch,
    EventDateTime,
    GoogleEventRecord,
    SourceEvent,
)
from icloud_gcal_mirror.retry import RetryPolicy
from icloud_gcal_mirror.storage import StateStore
from icloud_gcal_mirror.sync import SyncEngine


class TransientStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


@dataclass
class FakeSource(SourceCalendarService):
    calendar: CalendarRef = field(
        default_factory=lambda: CalendarRef(id="icloud-cal", name="Personal")
    )
    batch: ChangeBatch = field(
        default_factory=lambda: ChangeBatch(events=(), collection_tag="tag-1")
    )
    list_failures: int = 0
    change_failures: int = 0
    calls: list[tuple[bool, str | None]] = field(default_factory=list)

    def list_calendars(self) -> list[CalendarRef]:
        if self.list_failures:
            self.list_failures -= 1
            raise ConnectionError("temporary iCloud discovery failure")
        return [self.calendar]

    def get_changes(
        self,
        calendar: CalendarRef,
        state: CalendarSyncState,
        window_start: datetime,
        window_end: datetime,
        *,
        full: bool,
    ) -> ChangeBatch:
        del calendar, window_start, window_end
        self.calls.append((full, state.sync_token))
        if self.change_failures:
            self.change_failures -= 1
            raise ConnectionError("temporary iCloud event failure")
        return self.batch


@dataclass
class FakeTarget(TargetCalendarService):
    calendar: CalendarRef = field(
        default_factory=lambda: CalendarRef(id="google-cal", name="Apple Calendar Mirror")
    )
    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    create_failures: int = 0
    update_failures: int = 0
    delete_failures: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    _next_id: int = 1
    _etag: int = 1

    def list_calendars(self) -> list[CalendarRef]:
        return [self.calendar]

    def create_calendar(self, name: str) -> CalendarRef:
        self.calendar = CalendarRef(id="created-google-cal", name=name)
        return self.calendar

    def get_event(
        self, calendar_id: str, event_id: str, source_key: str
    ) -> GoogleEventRecord | None:
        del calendar_id
        event = self.events.get(event_id)
        if event is None:
            return None
        private = event.get("extendedProperties", {}).get("private", {})
        if private.get(PRIVATE_SOURCE_KEY) != PRIVATE_SOURCE_VALUE:
            return None
        if private.get(PRIVATE_SOURCE_ID_KEY) != source_key:
            return None
        return GoogleEventRecord(
            id=event_id,
            etag=event.get("etag"),
            source_key=source_key,
            source_hash=private.get(PRIVATE_SOURCE_HASH_KEY),
        )

    def create_event(self, calendar_id: str, body: dict[str, Any]) -> GoogleEventRecord:
        del calendar_id
        if self.create_failures:
            self.create_failures -= 1
            raise TransientStatusError(429)
        event_id = f"event-{self._next_id}"
        self._next_id += 1
        record = self._store(event_id, body)
        self.created += 1
        return record

    def update_event(
        self,
        calendar_id: str,
        event_id: str,
        body: dict[str, Any],
    ) -> GoogleEventRecord:
        del calendar_id
        if self.update_failures:
            self.update_failures -= 1
            raise ConnectionError("temporary Google failure")
        record = self._store(event_id, body)
        self.updated += 1
        return record

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        del calendar_id
        if self.delete_failures:
            self.delete_failures -= 1
            raise ConnectionError("temporary Google delete failure")
        self.events.pop(event_id, None)
        self.deleted += 1

    def manual_modify(self, event_id: str) -> None:
        self.events[event_id]["summary"] = "Manual Google edit"
        self.events[event_id]["etag"] = self._new_etag()

    def add_nonmanaged(self, event_id: str) -> None:
        self.events[event_id] = {"id": event_id, "etag": self._new_etag(), "summary": "User event"}

    def _store(self, event_id: str, body: dict[str, Any]) -> GoogleEventRecord:
        stored = dict(body)
        stored["id"] = event_id
        stored["etag"] = self._new_etag()
        self.events[event_id] = stored
        private = stored["extendedProperties"]["private"]
        return GoogleEventRecord(
            id=event_id,
            etag=stored["etag"],
            source_key=private[PRIVATE_SOURCE_ID_KEY],
            source_hash=private[PRIVATE_SOURCE_HASH_KEY],
        )

    def _new_etag(self) -> str:
        value = f"etag-{self._etag}"
        self._etag += 1
        return value


def make_config() -> AppConfig:
    return AppConfig(
        source_calendars=["icloud-cal"],
        google_calendar_id="google-cal",
        default_timezone="Europe/Berlin",
    )


def make_engine(
    tmp_path: Path, source: FakeSource, target: FakeTarget
) -> tuple[SyncEngine, StateStore]:
    store = StateStore(tmp_path / "state.sqlite3")
    return (
        SyncEngine(
            config=make_config(),
            source=source,
            target=target,
            store=store,
            retry_policy=RetryPolicy(initial_delay_seconds=0, max_delay_seconds=0),
        ),
        store,
    )


def source_event(
    key: str,
    *,
    uid: str | None = None,
    summary: str = "Dentist",
    source_hash: str | None = None,
    recurrence_id: str | None = None,
    status: str | None = None,
    recurrence: tuple[str, ...] = (),
    all_day: bool = False,
    start: date | datetime | None = None,
    end: date | datetime | None = None,
) -> SourceEvent:
    zone = ZoneInfo("Europe/Berlin")
    if start is None:
        start = datetime(2026, 3, 28, 9, 0, tzinfo=zone)
    if end is None:
        end = date(2026, 3, 29) if all_day else datetime(2026, 3, 28, 10, 0, tzinfo=zone)
    if all_day and isinstance(start, datetime):
        start = start.date()
    return SourceEvent(
        source_key=key,
        calendar_id="icloud-cal",
        calendar_name="Personal",
        uid=uid or key,
        recurrence_id=recurrence_id,
        href=f"https://icloud.example/{key}.ics",
        etag="source-etag",
        last_modified="2026-03-28T08:00:00Z",
        source_hash=source_hash or f"hash-{key}",
        summary=summary,
        description="Notes",
        location="Office",
        start=EventDateTime(start, all_day=all_day, time_zone="Europe/Berlin"),
        end=EventDateTime(end, all_day=all_day, time_zone="Europe/Berlin"),
        url="https://example.com",
        transparency="OPAQUE",
        status=status,
        recurrence=recurrence,
    )
