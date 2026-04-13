"""Tests for src/api/db.py."""

import sqlite3
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.db import get_db, init_db, _set_db_path


@pytest.fixture()
def mem_db(tmp_path):
    """Initialise an in-memory database and point the module at it."""
    db_path = Path(":memory:")
    _set_db_path(db_path)
    init_db(db_path)
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    # Re-init on this connection so tables exist
    from api.db import _DDL
    conn.executescript(_DDL)
    return conn


def test_init_db_creates_tables(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "conversations" in tables
    assert "messages" in tables


def test_init_db_enables_wal(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_init_db_creates_parent_dirs(tmp_path):
    db_path = tmp_path / "subdir" / "nested" / "test.db"
    init_db(db_path)
    assert db_path.exists()


def test_get_db_yields_connection(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    _set_db_path(db_path)
    gen = get_db()
    conn = next(gen)
    assert isinstance(conn, sqlite3.Connection)
    try:
        next(gen)
    except StopIteration:
        pass


def test_cascade_delete(mem_db):
    mem_db.execute(
        "INSERT INTO conversations (id, created_at, updated_at) VALUES ('c1', 'now', 'now')"
    )
    mem_db.execute(
        "INSERT INTO messages (id, conversation_id, role, content_json, timestamp, is_internal) "
        "VALUES ('m1', 'c1', 'user', '\"hi\"', 'now', 0)"
    )
    mem_db.commit()
    assert mem_db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
    mem_db.execute("DELETE FROM conversations WHERE id = 'c1'")
    mem_db.commit()
    assert mem_db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
