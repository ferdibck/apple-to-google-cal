from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .paths import config_path, ensure_app_dirs

MIN_POLL_INTERVAL_SECONDS = 60


@dataclass
class AppConfig:
    icloud_base_url: str = "https://caldav.icloud.com/"
    source_calendars: list[str] = field(default_factory=list)
    google_calendar_id: str | None = None
    google_calendar_name: str = "Apple Calendar Mirror"
    google_oauth_client_secret_path: str | None = None
    poll_interval_seconds: int = 60
    full_reconcile_hours: int = 24
    past_days: int = 90
    future_days: int = 730
    default_timezone: str = "UTC"

    def normalized(self) -> AppConfig:
        if self.poll_interval_seconds < MIN_POLL_INTERVAL_SECONDS:
            self.poll_interval_seconds = MIN_POLL_INTERVAL_SECONDS
        if self.full_reconcile_hours < 1:
            self.full_reconcile_hours = 24
        if self.past_days < 0:
            self.past_days = 0
        if self.future_days < 1:
            self.future_days = 730
        try:
            ZoneInfo(self.default_timezone)
        except ZoneInfoNotFoundError:
            self.default_timezone = "UTC"
        return self


def load_config(path: Path | None = None) -> AppConfig:
    config_file = path or config_path()
    if not config_file.exists():
        return AppConfig()
    data = json.loads(config_file.read_text(encoding="utf-8"))
    return AppConfig(**data).normalized()


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    config_file = path or config_path()
    ensure_app_dirs(config_file.parent)
    config.normalized()
    config_file.write_text(json.dumps(asdict(config), indent=2, sort_keys=True), encoding="utf-8")
    return config_file


def config_errors(config: AppConfig) -> list[str]:
    errors: list[str] = []
    if not config.source_calendars:
        errors.append("No iCloud source calendars selected.")
    if not config.google_calendar_id:
        errors.append("No Google mirror calendar selected.")
    if not config.google_oauth_client_secret_path:
        errors.append("No Google OAuth desktop client-secret file path configured.")
    elif not Path(config.google_oauth_client_secret_path).exists():
        errors.append("Configured Google OAuth client-secret file does not exist.")
    if config.poll_interval_seconds < MIN_POLL_INTERVAL_SECONDS:
        errors.append("Poll interval must be at least 60 seconds.")
    try:
        ZoneInfo(config.default_timezone)
    except ZoneInfoNotFoundError:
        errors.append(f"Unknown default time zone: {config.default_timezone}")
    return errors
