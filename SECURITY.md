# Security

## Secret Handling

Secrets are stored through Python `keyring`. On Windows, this normally uses Windows Credential Manager.

Secret keys:

- `apple-email`
- `apple-app-specific-password`
- `google-oauth-token-json`

The app does not store:

- Normal Apple Account password.
- Apple app-specific password in config.
- Google OAuth token JSON in config.
- OAuth desktop client JSON contents.

## Files Excluded From Git

The `.gitignore` excludes SQLite databases, logs, config, OAuth client secrets, token files, virtual environments, caches, and build output.

Do not commit:

- `client_secret*.json`
- `token*.json`
- `credentials*.json`
- `state.sqlite3`
- `config.json`
- `logs/`

## Logging

Logs are redacted for:

- Password-looking key/value pairs.
- OAuth token fields.
- Google access-token shapes.
- Apple app-specific password shape.

Do not add logging of full HTTP request/response bodies from Apple or Google APIs.

## iCloud Safety

The iCloud adapter is read-only. Mutating CalDAV methods are blocked in code. No normal Apple Account password is used; only an app-specific password should be provided.

## Google Safety

Google writes are limited to one configured target calendar. Events are only updated/deleted after the private app marker is verified. Attendees are intentionally omitted so the app does not send invitations.

## Reporting Vulnerabilities

This is a personal local application. Record security-sensitive issues privately and avoid putting credentials, OAuth files, logs, or database contents into issues or pull requests.

