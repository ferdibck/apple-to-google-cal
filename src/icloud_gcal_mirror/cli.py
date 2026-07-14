from __future__ import annotations

import argparse
import getpass
import sys
import time
from pathlib import Path

from .config import AppConfig, load_config, save_config
from .credentials import APPLE_APP_PASSWORD_KEY, APPLE_EMAIL_KEY, KeyringCredentialStore
from .doctor import run_doctor
from .google import GoogleCalendarService
from .icloud import ICloudCalDAVService
from .lock import InstanceAlreadyRunningError, InstanceLock
from .logging_config import configure_logging, get_logger
from .paths import config_path, database_path, ensure_app_dirs, lock_path, log_dir
from .startup import install_startup_task, uninstall_startup_task
from .status import format_status
from .storage import StateStore
from .sync import SyncEngine

LOGGER = get_logger("cli")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    home = ensure_app_dirs()
    configure_logging(home, verbose=bool(getattr(args, "verbose", False)))

    command = args.command
    if command == "setup":
        return _setup()
    if command == "doctor":
        return _doctor()
    if command == "sync-once":
        return _sync_once(dry_run=False)
    if command == "dry-run":
        return _sync_once(dry_run=True)
    if command == "run":
        return _run()
    if command == "status":
        return _status()
    if command == "install-startup-task":
        install_startup_task(home)
        print("Startup task installed.")
        return 0
    if command == "uninstall-startup-task":
        uninstall_startup_task()
        print("Startup task removed.")
        return 0
    parser.print_help()
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="icloud-gcal-mirror")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in (
        "setup",
        "doctor",
        "sync-once",
        "run",
        "status",
        "dry-run",
        "install-startup-task",
        "uninstall-startup-task",
    ):
        subparsers.add_parser(command)
    return parser


def _services(
    config: AppConfig,
) -> tuple[KeyringCredentialStore, ICloudCalDAVService, GoogleCalendarService]:
    credentials = KeyringCredentialStore()
    source = ICloudCalDAVService(base_url=config.icloud_base_url, credentials=credentials)
    target = GoogleCalendarService(credentials)
    return credentials, source, target


def _store() -> StateStore:
    return StateStore(database_path())


def _engine() -> SyncEngine:
    config = load_config()
    _, source, target = _services(config)
    return SyncEngine(config=config, source=source, target=target, store=_store())


def _setup() -> int:
    config = load_config()
    credentials, source, target = _services(config)

    email = input("Apple Account email: ").strip()
    app_password = getpass.getpass("Apple app-specific password: ")
    credentials.set(APPLE_EMAIL_KEY, email)
    credentials.set(APPLE_APP_PASSWORD_KEY, app_password)

    calendars = source.list_calendars()
    if not calendars:
        print("No iCloud calendars were found.")
        return 1
    print("iCloud calendars:")
    for index, calendar in enumerate(calendars, start=1):
        print(f"{index}. {calendar.name}")
    selected = _select_indexes(
        "Select source calendar numbers separated by commas: ", len(calendars)
    )
    config.source_calendars = [calendars[index - 1].id for index in selected]

    client_secret = input("Path to Google OAuth desktop client-secret JSON: ").strip().strip('"')
    if not Path(client_secret).exists():
        print("That file does not exist.")
        return 1
    config.google_oauth_client_secret_path = client_secret
    target.authorize(client_secret)

    google_calendars = target.list_calendars()
    print("Google calendars:")
    for index, calendar in enumerate(google_calendars, start=1):
        print(f"{index}. {calendar.name}")
    create = input(f"Create '{config.google_calendar_name}'? [Y/n]: ").strip().lower()
    if create in {"", "y", "yes"}:
        mirror_calendar = target.create_calendar(config.google_calendar_name)
    else:
        selected_google = _select_indexes(
            "Select one target Google calendar number: ", len(google_calendars)
        )
        mirror_calendar = google_calendars[selected_google[0] - 1]
    config.google_calendar_id = mirror_calendar.id
    save_config(config)

    print(f"Configuration saved to {config_path()}.")
    dry = input("Run an initial dry run now? [Y/n]: ").strip().lower()
    if dry in {"", "y", "yes"}:
        return _sync_once(dry_run=True)
    return 0


def _select_indexes(prompt: str, count: int) -> list[int]:
    raw = input(prompt)
    indexes: list[int] = []
    for part in raw.split(","):
        try:
            index = int(part.strip())
        except ValueError:
            continue
        if 1 <= index <= count:
            indexes.append(index)
    if not indexes:
        raise SystemExit("No valid selection was made.")
    return indexes


def _doctor() -> int:
    config = load_config()
    credentials, source, target = _services(config)
    store = _store()
    result = run_doctor(
        config=config,
        credentials=credentials,
        store=store,
        source=source,
        target=target,
        database_path=database_path(),
    )
    print(result.render())
    return 0 if result.ok else 1


def _sync_once(*, dry_run: bool) -> int:
    try:
        with InstanceLock(lock_path()):
            engine = _engine()
            result = engine.sync_once(dry_run=dry_run)
    except InstanceAlreadyRunningError as exc:
        print(str(exc))
        return 1

    counts = result.counts.as_dict()
    print(
        "Sync complete"
        + (" (dry run)" if dry_run else "")
        + ": "
        + ", ".join(f"{key}={value}" for key, value in counts.items())
    )
    for error in result.errors[-10:]:
        print(f"Warning/error: {error}")
    return 0 if result.success else 1


def _run() -> int:
    config = load_config()
    try:
        with InstanceLock(lock_path()):
            engine = _engine()
            while True:
                result = engine.sync_once(dry_run=False)
                if result.errors:
                    LOGGER.warning(
                        "Synchronization completed with warnings/errors: %s", result.errors
                    )
                time.sleep(config.poll_interval_seconds)
    except KeyboardInterrupt:
        print("Stopped.")
        return 0
    except InstanceAlreadyRunningError as exc:
        print(str(exc))
        return 1


def _status() -> int:
    config = load_config()
    store = _store()
    print(format_status(config, store, database_path(), log_dir()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
