from __future__ import annotations

import json
from pathlib import Path

from .config import AppConfig
from .startup import startup_task_status
from .storage import StateStore


def format_status(config: AppConfig, store: StateStore, database: Path, logs: Path) -> str:
    latest = store.latest_run()
    success = store.last_successful_run()
    counts = store.status_counts()

    latest_counts: dict[str, int] = {}
    latest_errors: list[str] = []
    if latest is not None:
        latest_counts = json.loads(latest["counts_json"] or "{}")
        latest_errors = json.loads(latest["errors_json"] or "[]")

    lines = [
        "iCloud Google Calendar Mirror status",
        f"Last attempted synchronization: {latest['started_at'] if latest else 'never'}",
        f"Last successful synchronization: {success['ended_at'] if success else 'never'}",
        f"Selected iCloud calendars: {', '.join(config.source_calendars) or 'none'}",
        f"Google mirror calendar: {config.google_calendar_id or 'not configured'}",
        (
            "Latest counts: "
            + ", ".join(
                f"{name}={latest_counts.get(name, 0)}"
                for name in ("created", "updated", "deleted", "unchanged", "skipped", "failed")
            )
        ),
        f"Active managed mappings: {counts['active_mappings']}",
        f"Deleted managed mappings retained in state: {counts['deleted_mappings']}",
        f"Startup task: {startup_task_status()}",
        f"Database: {database}",
        f"Logs: {logs}",
    ]
    if latest_errors:
        lines.append("Current warnings/errors:")
        lines.extend(f"- {error}" for error in latest_errors[-10:])
    return "\n".join(lines)
