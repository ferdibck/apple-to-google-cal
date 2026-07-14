from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "iCloudGoogleCalendarMirror"
APP_HOME_ENV = "ICLOUD_GCAL_MIRROR_HOME"


def app_home() -> Path:
    override = os.environ.get(APP_HOME_ENV)
    if override:
        return Path(override).expanduser()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_DIR_NAME

    return Path.home() / ".local" / "share" / APP_DIR_NAME


def config_path(home: Path | None = None) -> Path:
    return (home or app_home()) / "config.json"


def database_path(home: Path | None = None) -> Path:
    return (home or app_home()) / "state.sqlite3"


def log_dir(home: Path | None = None) -> Path:
    return (home or app_home()) / "logs"


def lock_path(home: Path | None = None) -> Path:
    return (home or app_home()) / "mirror.lock"


def ensure_app_dirs(home: Path | None = None) -> Path:
    base = home or app_home()
    base.mkdir(parents=True, exist_ok=True)
    log_dir(base).mkdir(parents=True, exist_ok=True)
    return base
