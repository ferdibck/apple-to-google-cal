from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass

TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    initial_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    jitter_fraction: float = 0.25


def transient_status_code(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status

    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status

    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    if isinstance(status, int):
        return status

    return None


def is_transient(exc: BaseException) -> bool:
    status = transient_status_code(exc)
    if status in TRANSIENT_STATUS_CODES:
        return True
    return isinstance(exc, TimeoutError | ConnectionError)


def call_with_retry[T](
    func: Callable[[], T],
    *,
    policy: RetryPolicy = RetryPolicy(),
    sleeper: Callable[[float], None] = time.sleep,
) -> T:
    delay = policy.initial_delay_seconds
    attempt = 1
    while True:
        try:
            return func()
        except BaseException as exc:
            if attempt >= policy.max_attempts or not is_transient(exc):
                raise
            jitter = delay * policy.jitter_fraction * random.random()
            sleeper(min(policy.max_delay_seconds, delay + jitter))
            delay = min(policy.max_delay_seconds, delay * 2)
            attempt += 1
