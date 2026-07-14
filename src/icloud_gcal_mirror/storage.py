from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .models import CalendarSyncState, SyncCounts
from .time_utils import iso_utc, parse_utc

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class MappingRecord:
    source_key: str
    source_calendar_id: str
    source_uid: str
    source_recurrence_id: str | None
    source_href: str | None
    source_etag: str | None
    source_last_modified: str | None
    source_hash: str
    source_start_utc: datetime | None
    source_end_utc: datetime | None
    google_event_id: str
    google_etag: str | None
    deleted: bool


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self.migrate()

    def close(self) -> None:
        self._conn.close()

    def migrate(self) -> None:
        conn = self._conn
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            )
            """
        )
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        current = int(row["version"]) if row else 0
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema {current} is newer than supported {SCHEMA_VERSION}."
            )
        if current < 1:
            self._migrate_1()
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()

    def _migrate_1(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS mappings (
                source_key TEXT PRIMARY KEY,
                source_calendar_id TEXT NOT NULL,
                source_uid TEXT NOT NULL,
                source_recurrence_id TEXT,
                source_href TEXT,
                source_etag TEXT,
                source_last_modified TEXT,
                source_hash TEXT NOT NULL,
                source_start_utc TEXT,
                source_end_utc TEXT,
                google_event_id TEXT NOT NULL,
                google_etag TEXT,
                deleted INTEGER NOT NULL DEFAULT 0,
                last_seen_run_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_mappings_calendar
            ON mappings(source_calendar_id, deleted);

            CREATE TABLE IF NOT EXISTS calendar_state (
                calendar_id TEXT PRIMARY KEY,
                sync_token TEXT,
                collection_tag TEXT,
                last_full_sync TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                dry_run INTEGER NOT NULL,
                success INTEGER,
                counts_json TEXT NOT NULL DEFAULT '{}',
                errors_json TEXT NOT NULL DEFAULT '[]'
            );
            """
        )

    def begin_run(self, *, dry_run: bool, started_at: datetime) -> int:
        cursor = self._conn.execute(
            "INSERT INTO sync_runs(started_at, dry_run) VALUES (?, ?)",
            (iso_utc(started_at), int(dry_run)),
        )
        self._conn.commit()
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a sync run id.")
        return cursor.lastrowid

    def finish_run(
        self,
        run_id: int,
        *,
        ended_at: datetime,
        success: bool,
        counts: SyncCounts,
        errors: list[str],
    ) -> None:
        self._conn.execute(
            """
            UPDATE sync_runs
            SET ended_at = ?, success = ?, counts_json = ?, errors_json = ?
            WHERE id = ?
            """,
            (
                iso_utc(ended_at),
                int(success),
                json.dumps(counts.as_dict(), sort_keys=True),
                json.dumps(errors),
                run_id,
            ),
        )
        self._conn.commit()

    def latest_run(self) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self._conn.execute(
                "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1",
            ).fetchone(),
        )

    def last_successful_run(self) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self._conn.execute(
                "SELECT * FROM sync_runs WHERE success = 1 ORDER BY id DESC LIMIT 1",
            ).fetchone(),
        )

    def get_calendar_state(self, calendar_id: str) -> CalendarSyncState:
        row = self._conn.execute(
            "SELECT * FROM calendar_state WHERE calendar_id = ?",
            (calendar_id,),
        ).fetchone()
        if row is None:
            return CalendarSyncState(calendar_id=calendar_id)
        return CalendarSyncState(
            calendar_id=calendar_id,
            sync_token=row["sync_token"],
            collection_tag=row["collection_tag"],
            last_full_sync=parse_utc(row["last_full_sync"]),
        )

    def set_calendar_state(self, state: CalendarSyncState) -> None:
        self._conn.execute(
            """
            INSERT INTO calendar_state(
                calendar_id, sync_token, collection_tag, last_full_sync, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(calendar_id) DO UPDATE SET
                sync_token = excluded.sync_token,
                collection_tag = excluded.collection_tag,
                last_full_sync = excluded.last_full_sync,
                updated_at = excluded.updated_at
            """,
            (
                state.calendar_id,
                state.sync_token,
                state.collection_tag,
                iso_utc(state.last_full_sync),
                iso_utc(datetime.now(UTC)),
            ),
        )
        self._conn.commit()

    def get_mapping(self, source_key: str) -> MappingRecord | None:
        row = self._conn.execute(
            "SELECT * FROM mappings WHERE source_key = ?",
            (source_key,),
        ).fetchone()
        return self._mapping_from_row(row) if row else None

    def find_active_by_uid(self, uid: str, recurrence_id: str | None) -> MappingRecord | None:
        rows = self._conn.execute(
            """
            SELECT * FROM mappings
            WHERE source_uid = ?
              AND COALESCE(source_recurrence_id, '') = COALESCE(?, '')
              AND deleted = 0
            """,
            (uid, recurrence_id),
        ).fetchall()
        if len(rows) != 1:
            return None
        return self._mapping_from_row(rows[0])

    def move_source_key(self, old_source_key: str, new_source_key: str) -> None:
        self._conn.execute(
            "UPDATE mappings SET source_key = ?, updated_at = ? WHERE source_key = ?",
            (new_source_key, iso_utc(datetime.now(UTC)), old_source_key),
        )
        self._conn.commit()

    def upsert_mapping(
        self,
        *,
        run_id: int,
        source_key: str,
        source_calendar_id: str,
        source_uid: str,
        source_recurrence_id: str | None,
        source_href: str | None,
        source_etag: str | None,
        source_last_modified: str | None,
        source_hash: str,
        source_start_utc: datetime | None,
        source_end_utc: datetime | None,
        google_event_id: str,
        google_etag: str | None,
    ) -> None:
        now = iso_utc(datetime.now(UTC))
        self._conn.execute(
            """
            INSERT INTO mappings(
                source_key, source_calendar_id, source_uid, source_recurrence_id,
                source_href, source_etag, source_last_modified, source_hash,
                source_start_utc, source_end_utc, google_event_id, google_etag,
                deleted, last_seen_run_id, created_at, updated_at, deleted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, NULL)
            ON CONFLICT(source_key) DO UPDATE SET
                source_calendar_id = excluded.source_calendar_id,
                source_uid = excluded.source_uid,
                source_recurrence_id = excluded.source_recurrence_id,
                source_href = excluded.source_href,
                source_etag = excluded.source_etag,
                source_last_modified = excluded.source_last_modified,
                source_hash = excluded.source_hash,
                source_start_utc = excluded.source_start_utc,
                source_end_utc = excluded.source_end_utc,
                google_event_id = excluded.google_event_id,
                google_etag = excluded.google_etag,
                deleted = 0,
                last_seen_run_id = excluded.last_seen_run_id,
                updated_at = excluded.updated_at,
                deleted_at = NULL
            """,
            (
                source_key,
                source_calendar_id,
                source_uid,
                source_recurrence_id,
                source_href,
                source_etag,
                source_last_modified,
                source_hash,
                iso_utc(source_start_utc),
                iso_utc(source_end_utc),
                google_event_id,
                google_etag,
                run_id,
                now,
                now,
            ),
        )
        self._conn.commit()

    def touch_mapping_seen(self, source_key: str, run_id: int) -> None:
        self._conn.execute(
            "UPDATE mappings SET last_seen_run_id = ?, updated_at = ? WHERE source_key = ?",
            (run_id, iso_utc(datetime.now(UTC)), source_key),
        )
        self._conn.commit()

    def mark_mapping_deleted(self, source_key: str) -> None:
        now = iso_utc(datetime.now(UTC))
        self._conn.execute(
            """
            UPDATE mappings
            SET deleted = 1, deleted_at = ?, updated_at = ?
            WHERE source_key = ?
            """,
            (now, now, source_key),
        )
        self._conn.commit()

    def active_mappings_for_calendar(self, calendar_id: str) -> list[MappingRecord]:
        rows = self._conn.execute(
            "SELECT * FROM mappings WHERE source_calendar_id = ? AND deleted = 0",
            (calendar_id,),
        ).fetchall()
        return [self._mapping_from_row(row) for row in rows]

    def mappings_missing_from_full_scan(
        self,
        calendar_id: str,
        seen_source_keys: set[str],
        window_start: datetime,
        window_end: datetime,
    ) -> list[MappingRecord]:
        missing: list[MappingRecord] = []
        for mapping in self.active_mappings_for_calendar(calendar_id):
            if mapping.source_key in seen_source_keys:
                continue
            if mapping.source_start_utc is None or mapping.source_end_utc is None:
                continue
            if mapping.source_end_utc >= window_start and mapping.source_start_utc <= window_end:
                missing.append(mapping)
        return missing

    def status_counts(self) -> dict[str, int]:
        row = self._conn.execute(
            """
            SELECT
                SUM(CASE WHEN deleted = 0 THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN deleted = 1 THEN 1 ELSE 0 END) AS deleted
            FROM mappings
            """
        ).fetchone()
        return {
            "active_mappings": int(row["active"] or 0),
            "deleted_mappings": int(row["deleted"] or 0),
        }

    @staticmethod
    def _mapping_from_row(row: sqlite3.Row) -> MappingRecord:
        return MappingRecord(
            source_key=row["source_key"],
            source_calendar_id=row["source_calendar_id"],
            source_uid=row["source_uid"],
            source_recurrence_id=row["source_recurrence_id"],
            source_href=row["source_href"],
            source_etag=row["source_etag"],
            source_last_modified=row["source_last_modified"],
            source_hash=row["source_hash"],
            source_start_utc=parse_utc(row["source_start_utc"]),
            source_end_utc=parse_utc(row["source_end_utc"]),
            google_event_id=row["google_event_id"],
            google_etag=row["google_etag"],
            deleted=bool(row["deleted"]),
        )
