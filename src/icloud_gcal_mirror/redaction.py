from __future__ import annotations

import re
from collections.abc import Iterable

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(password|passwd|pwd)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)(refresh_token|access_token|client_secret|token)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r'(?i)("(?:refresh_token|access_token|client_secret|password)"\s*:\s*")([^"]+)(")'),
    re.compile(r"\b[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}\b"),
    re.compile(r"\bya29\.[A-Za-z0-9._\-]+\b"),
)


def redact(value: object, extra_secrets: Iterable[str] = ()) -> str:
    text = str(value)
    for secret in extra_secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")

    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(r"\1[REDACTED]\3", text)
        elif pattern.groups >= 2:
            text = pattern.sub(r"\1=[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text
