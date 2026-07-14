from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from icloud_gcal_mirror.models import (
    PRIVATE_SOURCE_HASH_KEY,
    PRIVATE_SOURCE_ID_KEY,
    PRIVATE_SOURCE_KEY,
    PRIVATE_SOURCE_VALUE,
    ChangeBatch,
)

from .fakes import FakeSource, FakeTarget, TransientStatusError, make_engine, source_event


def test_create_and_idempotent_restart_without_duplicates(tmp_path) -> None:
    source = FakeSource(batch=ChangeBatch(events=(source_event("a"),), collection_tag="tag-1"))
    target = FakeTarget()
    engine, store = make_engine(tmp_path, source, target)

    first = engine.sync_once()
    second = engine.sync_once()
    store.close()
    restarted, restarted_store = make_engine(tmp_path, source, target)
    third = restarted.sync_once()

    assert first.counts.created == 1
    assert second.counts.unchanged == 1
    assert third.counts.unchanged == 1
    assert target.created == 1
    assert len(target.events) == 1
    restarted_store.close()


def test_update_uses_existing_google_event(tmp_path) -> None:
    source = FakeSource(batch=ChangeBatch(events=(source_event("a", source_hash="h1"),)))
    target = FakeTarget()
    engine, store = make_engine(tmp_path, source, target)
    engine.sync_once()

    event_id = next(iter(target.events))
    source.batch = ChangeBatch(events=(source_event("a", summary="New title", source_hash="h2"),))
    result = engine.sync_once(force_full=True)

    assert result.counts.updated == 1
    assert next(iter(target.events)) == event_id
    assert target.events[event_id]["summary"] == "New title"
    store.close()


def test_delete_only_managed_google_mirror(tmp_path) -> None:
    source = FakeSource(batch=ChangeBatch(events=(source_event("a"),)))
    target = FakeTarget()
    engine, store = make_engine(tmp_path, source, target)
    engine.sync_once()
    event_id = next(iter(target.events))
    target.add_nonmanaged("user-event")

    source.batch = ChangeBatch(events=(), deleted_source_keys=("a",), collection_tag="tag-2")
    result = engine.sync_once(force_full=True)

    assert result.counts.deleted == 1
    assert event_id not in target.events
    assert "user-event" in target.events
    assert target.deleted == 1
    store.close()


def test_dry_run_does_not_modify_target_or_mapping_state(tmp_path) -> None:
    source = FakeSource(batch=ChangeBatch(events=(source_event("a"),)))
    target = FakeTarget()
    engine, store = make_engine(tmp_path, source, target)

    result = engine.sync_once(dry_run=True)

    assert result.counts.created == 1
    assert target.events == {}
    assert store.get_mapping("a") is None
    store.close()


def test_all_day_unicode_and_identical_title_time_events(tmp_path) -> None:
    first = source_event(
        "a",
        uid="uid-a",
        summary="Frühstück ☕",
        all_day=True,
        start=date(2026, 7, 14),
        end=date(2026, 7, 15),
    )
    second = source_event("b", uid="uid-b", summary="Frühstück ☕")
    third = source_event("c", uid="uid-c", summary="Frühstück ☕")
    source = FakeSource(batch=ChangeBatch(events=(first, second, third)))
    target = FakeTarget()
    engine, store = make_engine(tmp_path, source, target)

    result = engine.sync_once()

    assert result.counts.created == 3
    assert len(target.events) == 3
    all_day_event = next(event for event in target.events.values() if "date" in event["start"])
    assert all_day_event["start"]["date"] == "2026-07-14"
    assert all_day_event["end"]["date"] == "2026-07-15"
    assert any(event["summary"] == "Frühstück ☕" for event in target.events.values())
    store.close()


def test_recurring_changed_and_cancelled_instances(tmp_path) -> None:
    master = source_event(
        "series",
        uid="series-uid",
        recurrence=("RRULE:FREQ=WEEKLY;COUNT=4",),
    )
    override = source_event(
        "series-override",
        uid="series-uid",
        recurrence_id="2026-03-28T09:00:00+01:00",
        summary="Moved occurrence",
        source_hash="override-1",
    )
    source = FakeSource(batch=ChangeBatch(events=(master, override)))
    target = FakeTarget()
    engine, store = make_engine(tmp_path, source, target)
    engine.sync_once()

    source.batch = ChangeBatch(
        events=(
            master,
            source_event(
                "series-override",
                uid="series-uid",
                recurrence_id="2026-03-28T09:00:00+01:00",
                status="CANCELLED",
                source_hash="override-cancelled",
            ),
        )
    )
    result = engine.sync_once(force_full=True)

    assert result.counts.deleted == 1
    assert len(target.events) == 1
    remaining = next(iter(target.events.values()))
    assert remaining["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=4"]
    store.close()


def test_invalid_incremental_token_triggers_full_reconciliation(tmp_path) -> None:
    source = FakeSource(batch=ChangeBatch(events=(source_event("a"),), sync_token="good"))
    target = FakeTarget()
    engine, store = make_engine(tmp_path, source, target)
    engine.sync_once()

    source.batch = ChangeBatch(events=(), full_sync_required=True)
    engine.sync_once()

    assert source.calls[-1][0] is True
    store.close()


def test_temporary_icloud_and_google_failures_are_retried(tmp_path) -> None:
    source = FakeSource(batch=ChangeBatch(events=(source_event("a"),)), change_failures=1)
    target = FakeTarget(create_failures=1)
    engine, store = make_engine(tmp_path, source, target)

    result = engine.sync_once()

    assert result.success
    assert result.counts.created == 1
    assert target.created == 1
    store.close()


def test_google_failure_for_one_event_does_not_stop_cycle(tmp_path) -> None:
    source = FakeSource(batch=ChangeBatch(events=(source_event("a"), source_event("b"))))
    target = FakeTarget()

    def broken_create(calendar_id, body):  # type: ignore[no-untyped-def]
        private = body["extendedProperties"]["private"]
        if private[PRIVATE_SOURCE_ID_KEY] == "a":
            raise RuntimeError("permanent Google failure")
        return FakeTarget.create_event(target, calendar_id, body)

    target.create_event = broken_create  # type: ignore[method-assign]
    engine, store = make_engine(tmp_path, source, target)

    result = engine.sync_once()

    assert result.counts.failed == 1
    assert result.counts.created == 1
    assert len(target.events) == 1
    store.close()


def test_manual_google_delete_recreates_managed_mirror(tmp_path) -> None:
    source = FakeSource(batch=ChangeBatch(events=(source_event("a"),)))
    target = FakeTarget()
    engine, store = make_engine(tmp_path, source, target)
    engine.sync_once()
    old_id = next(iter(target.events))
    target.events.pop(old_id)

    result = engine.sync_once(force_full=True)

    assert result.counts.created == 1
    assert old_id not in target.events
    assert len(target.events) == 1
    store.close()


def test_manual_google_modify_is_restored_without_source_change(tmp_path) -> None:
    source = FakeSource(batch=ChangeBatch(events=(source_event("a"),)))
    target = FakeTarget()
    engine, store = make_engine(tmp_path, source, target)
    engine.sync_once()
    event_id = next(iter(target.events))
    target.manual_modify(event_id)

    result = engine.sync_once(force_full=True)

    assert result.counts.updated == 1
    assert target.events[event_id]["summary"] == "Dentist"
    store.close()


def test_nonmanaged_google_event_is_never_deleted_even_if_mapping_is_stale(tmp_path) -> None:
    source = FakeSource(batch=ChangeBatch(events=(source_event("a"),)))
    target = FakeTarget()
    engine, store = make_engine(tmp_path, source, target)
    target.add_nonmanaged("user-event")
    store.upsert_mapping(
        run_id=1,
        source_key="a",
        source_calendar_id="icloud-cal",
        source_uid="a",
        source_recurrence_id=None,
        source_href=None,
        source_etag=None,
        source_last_modified=None,
        source_hash="hash-a",
        source_start_utc=datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC")),
        source_end_utc=datetime(2026, 1, 1, 1, tzinfo=ZoneInfo("UTC")),
        google_event_id="user-event",
        google_etag="etag-user",
    )

    source.batch = ChangeBatch(events=(), deleted_source_keys=("a",))
    result = engine.sync_once(force_full=True)

    assert result.counts.unchanged == 1
    assert "user-event" in target.events
    assert target.deleted == 0
    store.close()


def test_moving_between_mirrored_icloud_calendars_reuses_mapping(tmp_path) -> None:
    original = source_event("old-key", uid="move-uid", source_hash="h1")
    moved = source_event("new-key", uid="move-uid", source_hash="h2")
    moved = moved.__class__(**{**moved.__dict__, "calendar_id": "icloud-cal-2"})
    source = FakeSource(batch=ChangeBatch(events=(original,)))
    target = FakeTarget()
    engine, store = make_engine(tmp_path, source, target)
    engine.sync_once()
    event_id = next(iter(target.events))

    source.calendar = source.calendar.__class__(id="icloud-cal-2", name="Work")
    engine.config.source_calendars.clear()
    engine.config.source_calendars.append("icloud-cal-2")
    source.batch = ChangeBatch(events=(moved,))
    result = engine.sync_once(force_full=True)

    assert result.counts.updated == 1
    assert next(iter(target.events)) == event_id
    private = target.events[event_id]["extendedProperties"]["private"]
    assert private[PRIVATE_SOURCE_ID_KEY] == "new-key"
    store.close()


def test_google_private_marker_is_written(tmp_path) -> None:
    source = FakeSource(batch=ChangeBatch(events=(source_event("a"),)))
    target = FakeTarget()
    engine, store = make_engine(tmp_path, source, target)

    engine.sync_once()

    private = next(iter(target.events.values()))["extendedProperties"]["private"]
    assert private[PRIVATE_SOURCE_KEY] == PRIVATE_SOURCE_VALUE
    assert private[PRIVATE_SOURCE_ID_KEY] == "a"
    assert private[PRIVATE_SOURCE_HASH_KEY] == "hash-a"
    store.close()


def test_rate_limit_exception_class_is_available_for_retry_tests() -> None:
    exc = TransientStatusError(429)
    assert exc.status_code == 429
