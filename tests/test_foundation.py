from __future__ import annotations

import logging
from pathlib import Path

from icloud_gcal_mirror.config import AppConfig, load_config, save_config
from icloud_gcal_mirror.credentials import (
    APPLE_APP_PASSWORD_KEY,
    GOOGLE_TOKEN_JSON_KEY,
    MemoryCredentialStore,
    credential_report,
)
from icloud_gcal_mirror.icloud import ICloudCalDAVService, ReadOnlyCalDAVViolationError
from icloud_gcal_mirror.logging_config import RedactingFilter
from icloud_gcal_mirror.redaction import redact
from icloud_gcal_mirror.storage import StateStore


def test_config_round_trip_normalizes_poll_interval(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    save_config(AppConfig(poll_interval_seconds=5, source_calendars=["a"]), path)

    loaded = load_config(path)

    assert loaded.poll_interval_seconds == 60
    assert loaded.source_calendars == ["a"]


def test_memory_credential_report_never_returns_values() -> None:
    store = MemoryCredentialStore(
        {
            APPLE_APP_PASSWORD_KEY: "abcd-efgh-ijkl-mnop",
            GOOGLE_TOKEN_JSON_KEY: '{"token":"secret"}',
        }
    )

    report = credential_report(store)

    assert report == {
        "apple_email": False,
        "apple_app_specific_password": True,
        "google_oauth_token": True,
    }


def test_redaction_filters_passwords_tokens_and_app_specific_passwords() -> None:
    text = redact(
        'password=abcd-efgh-ijkl-mnop {"refresh_token":"ya29.secret-token"} token=another-secret'
    )

    assert "abcd-efgh-ijkl-mnop" not in text
    assert "ya29.secret-token" not in text
    assert "another-secret" not in text
    assert "[REDACTED]" in text


def test_logging_filter_redacts_message() -> None:
    record = logging.LogRecord(
        "x", logging.INFO, "file", 1, "password=abcd-efgh-ijkl-mnop", (), None
    )
    RedactingFilter().filter(record)

    assert "abcd-efgh-ijkl-mnop" not in record.msg


def test_read_only_caldav_guard_blocks_mutating_methods() -> None:
    service = ICloudCalDAVService(
        base_url="https://caldav.icloud.com/", credentials=MemoryCredentialStore()
    )
    guarded = service._guarded_request(lambda *args, **kwargs: "ok")

    assert guarded("GET", "https://example.com") == "ok"
    try:
        guarded("PUT", "https://example.com")
    except ReadOnlyCalDAVViolationError:
        pass
    else:
        raise AssertionError("PUT should have been blocked")


def test_database_migration_creates_status_counts(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")

    assert store.status_counts() == {"active_mappings": 0, "deleted_mappings": 0}
    store.close()
