from __future__ import annotations

import platform
from dataclasses import dataclass, field
from pathlib import Path

from .config import AppConfig, config_errors
from .credentials import CredentialStore, credential_report
from .interfaces import SourceCalendarService, TargetCalendarService
from .startup import startup_task_status
from .storage import StateStore


@dataclass
class DoctorResult:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def render(self) -> str:
        lines = ["Doctor report"]
        lines.extend(f"PASS {item}" for item in self.passed)
        lines.extend(f"WARN {item}" for item in self.warnings)
        lines.extend(f"FAIL {item}" for item in self.failed)
        return "\n".join(lines)


def run_doctor(
    *,
    config: AppConfig,
    credentials: CredentialStore,
    store: StateStore,
    source: SourceCalendarService | None,
    target: TargetCalendarService | None,
    database_path: Path,
) -> DoctorResult:
    result = DoctorResult()

    result.passed.append(f"Python {platform.python_version()} is supported.")

    config_issues = config_errors(config)
    if config_issues:
        result.failed.extend(config_issues)
    else:
        result.passed.append("Configuration is complete.")

    creds = credential_report(credentials)
    for label, present in creds.items():
        if present:
            result.passed.append(f"Credential available: {label}.")
        else:
            result.failed.append(f"Credential missing: {label}.")

    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        store.status_counts()
        result.passed.append("SQLite database is accessible.")
    except Exception as exc:
        result.failed.append(f"SQLite database is not accessible: {exc}")

    if source is not None:
        try:
            calendars = source.list_calendars()
            result.passed.append(f"iCloud CalDAV discovery returned {len(calendars)} calendars.")
            available = {calendar.id for calendar in calendars}
            for calendar_id in config.source_calendars:
                if calendar_id in available:
                    result.passed.append(f"Selected iCloud calendar is available: {calendar_id}")
                else:
                    result.failed.append(f"Selected iCloud calendar is unavailable: {calendar_id}")
        except Exception as exc:
            result.failed.append(f"iCloud CalDAV check failed: {exc}")

    if target is not None:
        try:
            calendars = target.list_calendars()
            available = {calendar.id for calendar in calendars}
            if config.google_calendar_id and config.google_calendar_id in available:
                result.passed.append("Google target calendar is visible.")
            elif config.google_calendar_id:
                result.failed.append("Google target calendar is not visible to the OAuth account.")
            else:
                result.failed.append("Google target calendar is not configured.")
        except Exception as exc:
            result.failed.append(f"Google Calendar API check failed: {exc}")

    result.passed.append(f"Startup task status checked: {startup_task_status()}.")
    return result
