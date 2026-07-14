from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .config import AppConfig
from .google_event import to_google_event
from .interfaces import SourceCalendarService, TargetCalendarService
from .logging_config import get_logger
from .models import CalendarRef, CalendarSyncState, SourceEvent, SyncCounts, SyncResult
from .retry import RetryPolicy, call_with_retry
from .storage import StateStore
from .time_utils import event_datetime_to_utc, utc_now, window_bounds

LOGGER = get_logger("sync")


@dataclass(frozen=True)
class SyncEngine:
    config: AppConfig
    source: SourceCalendarService
    target: TargetCalendarService
    store: StateStore
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def sync_once(self, *, dry_run: bool = False, force_full: bool = False) -> SyncResult:
        started_at = utc_now()
        run_id = self.store.begin_run(dry_run=dry_run, started_at=started_at)
        counts = SyncCounts()
        errors: list[str] = []
        try:
            try:
                calendars_by_id = {
                    calendar.id: calendar
                    for calendar in call_with_retry(
                        self.source.list_calendars,
                        policy=self.retry_policy,
                    )
                }
            except Exception as exc:
                counts.failed += 1
                errors.append(f"Failed to list iCloud calendars: {exc}")
                return SyncResult(
                    started_at=started_at,
                    ended_at=utc_now(),
                    dry_run=dry_run,
                    counts=counts,
                    errors=errors,
                )
            selected = [
                calendars_by_id[id_]
                for id_ in self.config.source_calendars
                if id_ in calendars_by_id
            ]
            missing = sorted(set(self.config.source_calendars) - set(calendars_by_id))
            for calendar_id in missing:
                counts.failed += 1
                errors.append(f"Selected iCloud calendar is unavailable: {calendar_id}")

            window_start, window_end = window_bounds(
                self.config.past_days,
                self.config.future_days,
                now=started_at,
            )

            for calendar in selected:
                self._sync_calendar(
                    calendar,
                    window_start,
                    window_end,
                    counts,
                    errors,
                    run_id,
                    dry_run=dry_run,
                    force_full=force_full,
                    now=started_at,
                )
        finally:
            ended_at = utc_now()
            success = counts.failed == 0
            self.store.finish_run(
                run_id,
                ended_at=ended_at,
                success=success,
                counts=counts,
                errors=errors,
            )
        return SyncResult(
            started_at=started_at,
            ended_at=ended_at,
            dry_run=dry_run,
            counts=counts,
            errors=errors,
        )

    def _sync_calendar(
        self,
        calendar: CalendarRef,
        window_start: datetime,
        window_end: datetime,
        counts: SyncCounts,
        errors: list[str],
        run_id: int,
        *,
        dry_run: bool,
        force_full: bool,
        now: datetime,
    ) -> None:
        state = self.store.get_calendar_state(calendar.id)
        full = force_full or self._full_reconcile_due(state, now)
        if state.sync_token is None and state.collection_tag is None:
            full = True

        try:
            batch = call_with_retry(
                lambda: self.source.get_changes(
                    calendar,
                    state,
                    window_start,
                    window_end,
                    full=full,
                ),
                policy=self.retry_policy,
            )
        except Exception as exc:
            counts.failed += 1
            message = f"Failed to read iCloud calendar {calendar.name}: {exc}"
            LOGGER.warning(message)
            errors.append(message)
            return

        if batch.full_sync_required and not full:
            self.store.set_calendar_state(CalendarSyncState(calendar_id=calendar.id))
            self._sync_calendar(
                calendar,
                window_start,
                window_end,
                counts,
                errors,
                run_id,
                dry_run=dry_run,
                force_full=True,
                now=now,
            )
            return

        seen: set[str] = set()
        for event in batch.events:
            seen.add(event.source_key)
            self._apply_event(event, counts, errors, run_id, dry_run=dry_run)

        for source_key in batch.deleted_source_keys:
            self._delete_mapping(source_key, counts, errors, dry_run=dry_run)

        if full:
            for mapping in self.store.mappings_missing_from_full_scan(
                calendar.id,
                seen,
                window_start,
                window_end,
            ):
                self._delete_mapping(mapping.source_key, counts, errors, dry_run=dry_run)

        if not dry_run:
            self.store.set_calendar_state(
                CalendarSyncState(
                    calendar_id=calendar.id,
                    sync_token=batch.sync_token,
                    collection_tag=batch.collection_tag,
                    last_full_sync=now if full else state.last_full_sync,
                )
            )

    def _apply_event(
        self,
        event: SourceEvent,
        counts: SyncCounts,
        errors: list[str],
        run_id: int,
        *,
        dry_run: bool,
    ) -> None:
        if event.unsupported_reason:
            counts.skipped += 1
            message = f"Skipped unsupported event {event.source_key}: {event.unsupported_reason}"
            LOGGER.warning(message)
            errors.append(message)
            return

        if event.cancelled:
            self._delete_mapping(event.source_key, counts, errors, dry_run=dry_run)
            return

        try:
            mapping = self.store.get_mapping(event.source_key)
            target_lookup_source_key = event.source_key
            body = to_google_event(event)
            source_start_utc = event_datetime_to_utc(event.start, self.config.default_timezone)
            source_end_utc = event_datetime_to_utc(event.end, self.config.default_timezone)

            if mapping is None or mapping.deleted:
                moved_mapping = self.store.find_active_by_uid(event.uid, event.recurrence_id)
                if moved_mapping is not None and not dry_run:
                    target_lookup_source_key = moved_mapping.source_key
                    self.store.move_source_key(moved_mapping.source_key, event.source_key)
                    mapping = self.store.get_mapping(event.source_key)
                elif moved_mapping is not None and dry_run:
                    counts.updated += 1
                    return

            if mapping is None or mapping.deleted:
                if dry_run:
                    counts.created += 1
                    return
                google_record = call_with_retry(
                    lambda: self.target.create_event(self._target_calendar_id(), body),
                    policy=self.retry_policy,
                )
                self._save_mapping(
                    run_id,
                    event,
                    source_start_utc,
                    source_end_utc,
                    google_record.id,
                    google_record.etag,
                )
                counts.created += 1
                return

            target_record = call_with_retry(
                lambda: self.target.get_event(
                    self._target_calendar_id(),
                    mapping.google_event_id,
                    target_lookup_source_key,
                ),
                policy=self.retry_policy,
            )
            source_unchanged = mapping.source_hash == event.source_hash
            target_unchanged = (
                target_record is not None
                and target_record.etag == mapping.google_etag
                and target_record.source_hash == event.source_hash
            )
            if source_unchanged and target_unchanged:
                if not dry_run:
                    self.store.touch_mapping_seen(event.source_key, run_id)
                counts.unchanged += 1
                return

            if dry_run:
                if target_record is None:
                    counts.created += 1
                else:
                    counts.updated += 1
                return

            if target_record is None:
                google_record = call_with_retry(
                    lambda: self.target.create_event(self._target_calendar_id(), body),
                    policy=self.retry_policy,
                )
                counts.created += 1
            else:
                google_record = call_with_retry(
                    lambda: self.target.update_event(
                        self._target_calendar_id(),
                        mapping.google_event_id,
                        body,
                    ),
                    policy=self.retry_policy,
                )
                counts.updated += 1
            self._save_mapping(
                run_id,
                event,
                source_start_utc,
                source_end_utc,
                google_record.id,
                google_record.etag,
            )
        except Exception as exc:
            counts.failed += 1
            message = f"Failed to mirror event {event.source_key}: {exc}"
            LOGGER.warning(message)
            errors.append(message)

    def _delete_mapping(
        self,
        source_key: str,
        counts: SyncCounts,
        errors: list[str],
        *,
        dry_run: bool,
    ) -> None:
        mapping = self.store.get_mapping(source_key)
        if mapping is None or mapping.deleted:
            counts.unchanged += 1
            return

        try:
            target_record = call_with_retry(
                lambda: self.target.get_event(
                    self._target_calendar_id(),
                    mapping.google_event_id,
                    source_key,
                ),
                policy=self.retry_policy,
            )
            if target_record is None:
                if not dry_run:
                    self.store.mark_mapping_deleted(source_key)
                counts.unchanged += 1
                return
            if dry_run:
                counts.deleted += 1
                return
            call_with_retry(
                lambda: self.target.delete_event(
                    self._target_calendar_id(), mapping.google_event_id
                ),
                policy=self.retry_policy,
            )
            self.store.mark_mapping_deleted(source_key)
            counts.deleted += 1
        except Exception as exc:
            counts.failed += 1
            message = f"Failed to delete mirror for {source_key}: {exc}"
            LOGGER.warning(message)
            errors.append(message)

    def _save_mapping(
        self,
        run_id: int,
        event: SourceEvent,
        source_start_utc: datetime,
        source_end_utc: datetime,
        google_event_id: str,
        google_etag: str | None,
    ) -> None:
        self.store.upsert_mapping(
            run_id=run_id,
            source_key=event.source_key,
            source_calendar_id=event.calendar_id,
            source_uid=event.uid,
            source_recurrence_id=event.recurrence_id,
            source_href=event.href,
            source_etag=event.etag,
            source_last_modified=event.last_modified,
            source_hash=event.source_hash,
            source_start_utc=source_start_utc,
            source_end_utc=source_end_utc,
            google_event_id=google_event_id,
            google_etag=google_etag,
        )

    def _full_reconcile_due(self, state: CalendarSyncState, now: datetime) -> bool:
        if state.last_full_sync is None:
            return True
        return (
            state.last_full_sync.astimezone(UTC) + timedelta(hours=self.config.full_reconcile_hours)
            <= now
        )

    def _target_calendar_id(self) -> str:
        if not self.config.google_calendar_id:
            raise RuntimeError("Google mirror calendar is not configured.")
        return self.config.google_calendar_id
