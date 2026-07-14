from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from .models import CalendarRef, CalendarSyncState, ChangeBatch, GoogleEventRecord


class SourceCalendarService(Protocol):
    def list_calendars(self) -> list[CalendarRef]: ...

    def get_changes(
        self,
        calendar: CalendarRef,
        state: CalendarSyncState,
        window_start: datetime,
        window_end: datetime,
        *,
        full: bool,
    ) -> ChangeBatch: ...


class TargetCalendarService(Protocol):
    def list_calendars(self) -> list[CalendarRef]: ...

    def create_calendar(self, name: str) -> CalendarRef: ...

    def get_event(
        self,
        calendar_id: str,
        event_id: str,
        source_key: str,
    ) -> GoogleEventRecord | None: ...

    def create_event(self, calendar_id: str, body: dict[str, Any]) -> GoogleEventRecord: ...

    def update_event(
        self,
        calendar_id: str,
        event_id: str,
        body: dict[str, Any],
    ) -> GoogleEventRecord: ...

    def delete_event(self, calendar_id: str, event_id: str) -> None: ...
