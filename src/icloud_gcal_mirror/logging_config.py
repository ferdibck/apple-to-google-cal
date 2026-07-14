from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import ensure_app_dirs, log_dir
from .redaction import redact

LOG_NAME = "icloud_gcal_mirror"


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def configure_logging(home: Path | None = None, verbose: bool = False) -> Path:
    base = ensure_app_dirs(home)
    path = log_dir(base) / "mirror.log"

    logger = logging.getLogger(LOG_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    redactor = RedactingFilter()

    file_handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redactor)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(redactor)
    stream_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
    logger.addHandler(stream_handler)

    return path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOG_NAME}.{name}")
