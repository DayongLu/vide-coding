"""
SQLite database layer for the Finance Agent API.

Provides schema initialisation and a FastAPI dependency that yields a
per-request connection. WAL mode is enabled once at init time.
"""

import sqlite3
from collections.abc import Generator
from pathlib import Path

_DDL = """
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content_json    TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    is_internal     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, timestamp);

CREATE TABLE IF NOT EXISTS email_invoices (
    id              TEXT PRIMARY KEY,
    email_id        TEXT NOT NULL,
    subject         TEXT,
    from_address    TEXT,
    received_at     TEXT,
    attachment_name TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    extracted_data  TEXT,
    vendor_id       TEXT,
    vendor_name     TEXT,
    bill_id         TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_email_invoices_status
    ON email_invoices(status, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_email_invoices_email_id
    ON email_invoices(email_id);
"""


def init_db(db_path: Path) -> None:
    """Create database file, enable WAL mode, and apply schema DDL.

    Args:
        db_path: Filesystem path for the SQLite database file.
            Parent directories are created if they do not exist.
            Pass ``Path(":memory:")`` for an in-memory database in tests
            (uses shared-cache URI so all connections share the same instance).
    """
    if str(db_path) == ":memory:":
        conn = sqlite3.connect(
            "file::memory:?cache=shared", uri=True, check_same_thread=False
        )
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_DDL)
        conn.commit()
    finally:
        conn.close()


# Module-level path used by the FastAPI dependency.
# Overridden by create_app() via _set_db_path().
_db_path: Path = Path("data/conversations.db")


def _set_db_path(path: Path) -> None:
    """Override the database path (used by the app factory in tests).

    Args:
        path: New database path.
    """
    global _db_path
    _db_path = path


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """FastAPI dependency: yield a SQLite connection, close on teardown.

    Yields:
        An open ``sqlite3.Connection`` with foreign-key enforcement enabled.
    """
    if str(_db_path) == ":memory:":
        conn = sqlite3.connect(
            "file::memory:?cache=shared", uri=True, check_same_thread=False
        )
    else:
        conn = sqlite3.connect(str(_db_path), check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
