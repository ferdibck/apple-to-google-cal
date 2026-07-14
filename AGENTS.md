# Repository Instructions For Future Codex Work

- This is a one-way synchronizer: iCloud CalDAV -> Google Calendar.
- Never add code that writes to iCloud or sends CalDAV mutating methods.
- Do not commit credentials, OAuth client files, tokens, logs, config, databases, or virtual environments.
- Use fake services for unit tests. Live Apple/Google tests must be explicit and opt-in.
- Run before finishing:
  - `python -m ruff format --check .`
  - `python -m ruff check .`
  - `python -m mypy src`
  - `python -m pytest --cov=icloud_gcal_mirror --cov-report=term-missing`
- If pytest temp directories fail in a mirrored folder, override `tmp_path` in tests rather than weakening the production code.
- Keep Google updates/deletes guarded by private extended properties.
- Keep attendees omitted unless there is a documented guarantee that no invitations or notifications are sent.

