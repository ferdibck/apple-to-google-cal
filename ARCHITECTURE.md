# Architecture

## Research Sources Checked

- Apple confirms app-specific passwords are used for third-party access to iCloud Mail, Contacts, and Calendars: <https://support.apple.com/en-us/102654>.
- Google installed applications use OAuth 2.0 with system browser and loopback/custom redirect options: <https://developers.google.com/identity/protocols/oauth2/native-app>.
- Google Calendar events support private extended properties, recurrence, all-day dates, time-zone aware date-times, and `sendUpdates`: <https://developers.google.com/workspace/calendar/api/v3/reference/events>.
- Google Calendar event listing supports sync tokens and 410 recovery semantics: <https://developers.google.com/workspace/calendar/api/v3/reference/events/list>.
- Google secondary calendars can be created with `calendars.insert`: <https://developers.google.com/workspace/calendar/api/v3/reference/calendars/insert>.
- CalDAV calendar access is standardized in RFC 4791: <https://datatracker.ietf.org/doc/html/rfc4791>.
- WebDAV collection synchronization is standardized in RFC 6578: <https://datatracker.ietf.org/doc/html/rfc6578>.
- iCalendar data modeling is standardized in RFC 5545: <https://datatracker.ietf.org/doc/html/rfc5545>.

## Main Components

- `cli.py`: command dispatcher for setup, doctor, sync, run, status, and startup task management.
- `config.py`: non-secret JSON configuration.
- `credentials.py`: keyring-backed secret storage.
- `paths.py`: per-user app data paths.
- `logging_config.py` and `redaction.py`: rotating logs and secret redaction.
- `lock.py`: single-instance lock.
- `storage.py`: SQLite migrations, mappings, sync state, and run history.
- `icloud.py`: read-only iCloud CalDAV adapter.
- `google.py`: Google Calendar API adapter.
- `google_event.py`: source event to Google event resource mapping.
- `sync.py`: synchronization engine and safety checks.
- `startup.py`: per-user Windows Task Scheduler XML.

## Data Model

SQLite stores:

- `mappings`: one row per managed mirrored event.
- `calendar_state`: CalDAV sync token, collection tag, and last full sync per source calendar.
- `sync_runs`: run timestamps, dry-run flag, counts, and warnings/errors.

The source key is derived from:

```text
source calendar id + iCalendar UID + RECURRENCE-ID when present
```

The app also stores iCalendar UID separately. If an event moves between selected iCloud calendars and the UID/RECURRENCE-ID is uniquely identifiable, the engine reuses the existing Google event and updates the private source key.

## Synchronization Algorithm

1. Acquire the single-instance lock.
2. Load config and credentials.
3. List iCloud calendars and validate selected calendars.
4. For each selected calendar, choose incremental or full mode:
   - Use CalDAV sync-token support where exposed by the client.
   - Otherwise compare collection tags where available.
   - Otherwise perform bounded reconciliation.
5. Parse VEVENT data into typed `SourceEvent` values.
6. For each source event:
   - Skip unsupported events with a clear warning.
   - Delete managed Google mirrors for cancelled/deleted events.
   - Create a Google mirror when no mapping exists.
   - Update the existing Google mirror when source hash or Google etag changed.
   - Leave unchanged mirrors untouched.
7. During full reconciliation, delete only mapped events that are still inside the current rolling window and were missing from the full scan. This avoids deleting events merely because they aged out of the window.
8. Save new sync state unless running dry-run.

## Google Write Safety

Before updating or deleting a Google event, the target adapter fetches the event and verifies:

```text
extendedProperties.private.source == icloud-google-calendar-mirror
extendedProperties.private.source_key == expected source key
```

If the marker is missing or mismatched, the event is treated as non-managed and is not modified.

## iCloud Read-Only Safety

The CalDAV adapter installs a request guard on the underlying client/session objects when available. It blocks CalDAV `PUT`, `POST`, `DELETE`, and any method not in:

```text
GET, HEAD, OPTIONS, PROPFIND, REPORT
```

No setup, doctor, or sync path calls CalDAV creation/update/delete APIs.

## Recurrence And Deletion Semantics

- Master recurring events carry RRULE/RDATE/EXDATE lines to Google.
- Detached recurrence instances receive their own stable source key including RECURRENCE-ID.
- Cancelled detached instances delete only their managed mirror.
- Unsupported recurrence data is skipped and reported rather than silently transformed.

## Retry Model

Transient failures use bounded exponential backoff with jitter. Transient statuses include 408, 409, 425, 429, 500, 502, 503, and 504. One event failure increments `failed` and does not stop the whole cycle.

