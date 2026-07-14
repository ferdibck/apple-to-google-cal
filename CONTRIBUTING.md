# Contributing

This repository is for a personal Windows calendar mirror. Keep changes conservative and safety-focused.

## Development Setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Checks

```powershell
python -m ruff format --check .
python -m ruff check .
python -m mypy src
python -m pytest --cov=icloud_gcal_mirror --cov-report=term-missing
```

## Rules

- Keep iCloud strictly read-only.
- Never log secrets.
- Never touch unmarked Google events.
- Keep service adapters behind typed interfaces.
- Add fake-service tests for sync engine behavior.
- Do not require live credentials for unit tests.

## Live Testing

Live tests must be opt-in and should use disposable calendars. Do not run live write tests against important personal calendars.

