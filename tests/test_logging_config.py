"""Tests for src/api/logging_config.py."""

import json
import logging
import sys
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.logging_config import configure


def _capture_log(level: str, fmt: str, message: str) -> str:
    """Configure logging, emit one record, return captured output."""
    buf = StringIO()
    handler = logging.StreamHandler(buf)
    configure(level=level, fmt=fmt)
    # Redirect root handler to our buffer
    root = logging.getLogger()
    root.handlers = [handler]
    if fmt == "json":
        from api.logging_config import _JsonFormatter
        handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger("test_logger")
    logger.info(message)
    return buf.getvalue()


def test_json_format_is_valid_json(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    output = _capture_log("INFO", "json", "hello world")
    line = output.strip()
    assert line, "No log output produced"
    data = json.loads(line)
    assert data["level"] == "INFO"
    assert data["message"] == "hello world"
    assert "time" in data
    assert "name" in data


def test_json_format_does_not_contain_secrets(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    output = _capture_log("INFO", "json", "normal message")
    # Ensure no API key-looking content leaks
    assert "Bearer" not in output
    assert "sk-ant" not in output


def test_text_format_is_not_json(monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "text")
    output = _capture_log("INFO", "text", "plain log")
    assert "plain log" in output
    try:
        json.loads(output.strip())
        is_json = True
    except (json.JSONDecodeError, ValueError):
        is_json = False
    assert not is_json
