# iCloud Google Calendar Mirror

iCloud Google Calendar Mirror is a personal Windows command-line application that mirrors selected Apple/iCloud calendars into one dedicated Google Calendar.

The synchronization direction is strictly:

```text
Apple/iCloud Calendar -> Google Calendar
```

Apple/iCloud remains authoritative. Google Calendar is only a readable mirror.

## One-Way Safety Guarantee

The application never writes to iCloud. The CalDAV adapter blocks mutating CalDAV methods before dispatch and only allows read/report methods: `GET`, `HEAD`, `OPTIONS`, `PROPFIND`, and `REPORT`.

The Google adapter only creates, updates, or deletes Google events marked with private extended properties:

```text
source = icloud-google-calendar-mirror
source_key = <stable source key>
source_hash = <meaningful source hash>
```

User-created Google events and unmarked Google events are not modified or deleted, even if stale local mappings point at them.

## What It Does

- Reads iCloud calendars over CalDAV using an Apple app-specific password.
- Uses Google OAuth 2.0 installed-application flow.
- Mirrors selected source calendars into one Google calendar, normally named `Apple Calendar Mirror`.
- Stores non-secret configuration in `%LOCALAPPDATA%\iCloudGoogleCalendarMirror\config.json`.
- Stores secrets through Python `keyring`, backed by Windows Credential Manager on Windows.
- Stores sync state in SQLite under the per-user app data directory.
- Logs to rotating log files under the per-user app data directory.
- Supports dry runs, one-shot syncs, continuous polling, status reporting, doctor checks, and per-user Task Scheduler installation.

## Known Limitations

- Live iCloud and Google integration tests are opt-in and were not run automatically.
- The automated suite uses fake CalDAV and Google services.
- Recurring series are mirrored using Google recurrence rules where representable. Detached and cancelled recurrence instances are tracked with stable source keys, but provider-specific edge cases can still be skipped rather than risk corruption.
- The first run and periodic reconciliation scan the configured rolling window. Incremental efficiency uses CalDAV sync tokens where the Python CalDAV library exposes them, then collection tags, then bounded reconciliation.
- Attendees are intentionally not mirrored in this first version to avoid accidental invitations or notification emails.

## Install On Windows

Use Python 3.12 or newer. A virtual environment is recommended:

```powershell
cd "D:\beckf\Documents\Google Drive Mirror\Agentic\apple-to-google-cal local rep\apple-to-google-cal"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If your launcher uses Python 3.13, that is also supported:

```powershell
python -m pip install -e ".[dev]"
```

## Apple App-Specific Password

Apple requires an app-specific password for third-party apps that access iCloud Calendar data. Generate one at [account.apple.com](https://account.apple.com/) under Sign-In and Security, App-Specific Passwords.

Use that generated password during `setup`. Do not use or store your normal Apple Account password.

Apple reference: [Sign in to apps with your Apple Account using app-specific passwords](https://support.apple.com/en-us/102654).

## Google Cloud Setup

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create or select a project.
3. Enable the Google Calendar API.
4. Configure the OAuth consent screen for your account.
5. Create an OAuth client for a desktop application.
6. Download the OAuth client JSON file.
7. Keep that JSON file outside git. The app stores only its path in non-secret config.

Google references:

- [OAuth 2.0 for iOS & Desktop Apps](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Google Calendar API Events](https://developers.google.com/workspace/calendar/api/v3/reference/events)
- [Google Calendar API Calendars: insert](https://developers.google.com/workspace/calendar/api/v3/reference/calendars/insert)

## Run Setup

```powershell
icloud-gcal-mirror setup
```

Setup will:

1. Ask for your Apple Account email.
2. Ask for the Apple app-specific password without echoing it.
3. Store Apple credentials in Windows Credential Manager.
4. Connect to iCloud CalDAV.
5. List available iCloud calendars.
6. Let you select one or more source calendars.
7. Run Google OAuth in your browser.
8. List Google calendars.
9. Let you create or select the mirror calendar.
10. Save non-secret configuration.
11. Offer an initial dry run.

## Dry Run

```powershell
icloud-gcal-mirror dry-run
```

Dry run computes planned creates, updates, and deletes without modifying either service and without updating sync mappings.

## Sync Once

```powershell
icloud-gcal-mirror sync-once
```

This runs one synchronization cycle and exits.

## Continuous Synchronizer

```powershell
icloud-gcal-mirror run
```

The default poll interval is 60 seconds. Press `Ctrl+C` to stop. The app uses a lock file to prevent two synchronizers from running simultaneously.

## Status

```powershell
icloud-gcal-mirror status
```

Status reports:

- Last successful synchronization.
- Last attempted synchronization.
- Selected iCloud calendars.
- Current Google mirror calendar.
- Created, updated, deleted, unchanged, skipped, and failed counts from the latest run.
- Current warnings/errors.
- Database and log paths.
- Startup task status.

## Doctor

```powershell
icloud-gcal-mirror doctor
```

Doctor checks the Python environment, configuration, credentials, iCloud discovery, selected source calendars, Google OAuth visibility, target calendar visibility, SQLite accessibility, and startup task status. It returns a nonzero exit status if required checks fail.

## Startup Task

Install a per-user Windows Task Scheduler task:

```powershell
icloud-gcal-mirror install-startup-task
```

Remove it:

```powershell
icloud-gcal-mirror uninstall-startup-task
```

The task runs at user login, uses least privilege, avoids multiple simultaneous instances, and asks Task Scheduler to restart after failure. It is not installed until you explicitly run the install command.

## Logs And State

Default app data location:

```text
%LOCALAPPDATA%\iCloudGoogleCalendarMirror
```

Typical files:

```text
config.json
state.sqlite3
logs\mirror.log
mirror.lock
startup-task.xml
```

Secrets are not stored in these files. They are stored via `keyring`.

## Backup, Reset, And Uninstall

Back up synchronization state by copying `state.sqlite3` while the app is not running.

To reset sync state:

1. Stop the synchronizer.
2. Back up `state.sqlite3`.
3. Delete `state.sqlite3`.
4. Run `icloud-gcal-mirror dry-run`.
5. Run `icloud-gcal-mirror sync-once` if the dry run is correct.

To uninstall:

```powershell
icloud-gcal-mirror uninstall-startup-task
python -m pip uninstall icloud-google-calendar-mirror
```

Then remove `%LOCALAPPDATA%\iCloudGoogleCalendarMirror` if you no longer need state or logs.

## Revoke Access

Apple:

- Revoke the app-specific password at [account.apple.com](https://account.apple.com/).

Google:

- Revoke the OAuth grant from your Google Account security settings.
- Optionally delete the OAuth client in Google Cloud Console.

## Privacy And Security

- No normal Apple Account password is accepted or stored.
- App-specific password and Google OAuth token JSON are stored through Windows Credential Manager.
- Logs are redacted for passwords and OAuth-like tokens.
- Runtime state, logs, OAuth client files, tokens, and databases are excluded from git.
- Google attendee mirroring is intentionally omitted to avoid notifications.
- Google writes use `sendUpdates=none`.

## Troubleshooting

`Apple email or app-specific password is missing`

Run `icloud-gcal-mirror setup` again.

`iCloud CalDAV check failed`

Verify the app-specific password has not been revoked and that the Apple Account has two-factor authentication enabled.

`Google OAuth credentials are invalid or expired`

Run `icloud-gcal-mirror setup` again and complete browser consent.

`redirect_uri_mismatch`

Create a Google OAuth desktop client and use its downloaded JSON file. Do not use a web-app OAuth client.

`Selected iCloud calendar is unavailable`

Run setup again and reselect calendars.

`Another synchronizer instance is running`

Stop the existing `run` process or check the Task Scheduler task.

## Development

Run checks:

```powershell
python -m ruff format --check .
python -m ruff check .
python -m mypy src
python -m pytest --cov=icloud_gcal_mirror --cov-report=term-missing
```

The normal unit test suite uses fake services and does not require live credentials.

## Integration Tests

Live integration tests are intentionally opt-in. Use a dedicated Google calendar and a disposable iCloud test calendar. Do not run live tests against important calendars until dry-run output is reviewed.

