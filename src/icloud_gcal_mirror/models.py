from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

APP_SOURCE = "icloud-google-calendar-mirror"
PRIVATE_SOURCE_KEY = "source"
PRIVATE_SOURCE_VALUE = APP_SOURCE
PRIVATE_SOURCE_ID_KEY = "source_key"
PRIVATE_SOURCE_HASH_KEY = "source_hash"


@dataclass(frozen=True)
class CalendarRef:
    id: str
    name: str
    color: str | None = None


@dataclass(frozen=True)
class EventDateTime:
    value: date | datetime
    all_day: bool = False
    time_zone: str | None = None


@dataclass(frozen=True)
class SourceEvent:
    source_key: str
    calendar_id: str
    calendar_name: str
    uid: str
    recurrence_id: str | None
    href: str | None
    etag: str | None
    last_modified: str | None
    source_hash: str
    summary: str
    description: str | None
    location: str | None
    start: EventDateTime
    end: EventDateTime
    url: str | None = None
    transparency: str | None = None
    status: str | None = None
    recurrence: tuple[str, ...] = ()
    sequence: int | None = None
    unsupported_reason: str | None = None

    @property
    def cancelled(self) -> bool:
        return (self.status or "").upper() == "CANCELLED"


@dataclass(frozen=True)
class CalendarSyncState:
    calendar_id: str
    sync_token: str | None = None
    collection_tag: str | None = None
    last_full_sync: datetime | None = None


@dataclass(frozen=True)
class ChangeBatch:
    events: tuple[SourceEvent, ...]
    deleted_source_keys: tuple[str, ...] = ()
    sync_token: str | None = None
    collection_tag: str | None = None
    full_sync_required: bool = False


@dataclass(frozen=True)
class GoogleEventRecord:
    id: str
    etag: str | None
    source_key: str
    source_hash: str | None


@dataclass
class SyncCounts:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "created": self.created,
            "updated": self.updated,
            "deleted": self.deleted,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "failed": self.failed,
        }


@dataclass
class SyncResult:
    started_at: datetime
    ended_at: datetime
    dry_run: bool
    counts: SyncCounts
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.counts.failed == 0
