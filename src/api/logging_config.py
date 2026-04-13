"""
Logging configuration for the Finance Agent API.

Supports two output formats controlled by the LOG_FORMAT environment variable:
- ``text`` (default): human-readable ``%(asctime)s %(levelname)s ...``
- ``json``: newline-delimited JSON for log aggregation pipelines

Log lines never contain message content, API keys, or OAuth tokens.
"""

import json
import logging
import os
from datetime import datetime, timezone


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record on a single line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure(level: str | None = None, fmt: str | None = None) -> None:
    """Configure the root logger.

    Args:
        level: Log level string (e.g. ``"INFO"``). Defaults to ``LOG_LEVEL``
            env var or ``"INFO"``.
        fmt: Format string: ``"text"`` or ``"json"``. Defaults to
            ``LOG_FORMAT`` env var or ``"text"``.
    """
    level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    fmt = fmt or os.getenv("LOG_FORMAT", "text")

    handler = logging.StreamHandler()
    if fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )

    root = logging.getLogger()
    root.setLevel(level)
    # Replace any existing handlers to avoid duplicate output
    root.handlers = [handler]
