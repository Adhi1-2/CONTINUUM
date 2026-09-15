"""Tests for the forward-only schema migration runner.

These prove the three behaviours B2.1 requires:

* a fresh database is seeded at ``SCHEMA_VERSION`` and records its baseline;
* an older database is migrated forward to ``SCHEMA_VERSION`` (and the v2
  additions -- the ``versions`` table and event provenance columns -- actually
  land);
* a newer, or an older shape with no registered path, fails closed with
  ``SchemaVersionError`` instead of being silently opened.
"""

from __future__ import annotations

import sqlite3

import pytest

from continuum.models import Run
from continuum.storage.base import SchemaVersionError
from continuum.storage.migrations import (
    MIGRATIONS,
    SCHEMA_VERSION,
    migrate_schema,
    schema_version_of,
)
from continuum.storage.sqlite import SQLiteStorage


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


# A plausible older (v1) shape: it has the core tables but lacks the v2
# ``versions`` table and the per-event ``source`` / ``prev_hash`` columns. The
# migration runner must bring exactly these up to the current schema.
_LEGACY_V1_SCHEMA = """
CREATE TABLE continuum_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE runs (
    run_id     TEXT PRIMARY KEY,
    goal       TEXT NOT NULL,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata   TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE events (
    run_id          TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    sequence        INTEGER NOT NULL,
    event_id        TEXT NOT NULL UNIQUE,
    type            TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    payload         TEXT NOT NULL,
    causer_event_id TEXT,
    hash            TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence)
);

CREATE TABLE checkpoints (
    checkpoint_id  TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    version        INTEGER NOT NULL,
    trigger        TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    integrity_hash TEXT NOT NULL,
    body           TEXT NOT NULL
);
"""


def _stamp(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT INTO continuum_meta(key, value) VALUES ('schema_version', ?)",
        (str(version),),
    )


def test_fresh_database_is_seeded_at_current_version():
    conn = _connect()
    final = migrate_schema(conn)
    assert final == SCHEMA_VERSION
    assert schema_version_of(conn) == SCHEMA_VERSION
    # The baseline is recorded as applied.
    row = conn.execute(
        "SELECT name FROM schema_migrations WHERE version = ?", (SCHEMA_VERSION,)
    ).fetchone()
    assert row is not None and row["name"] == "baseline"
    # All current tables exist.
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "events", "versions", "checkpoints"} <= tables
    conn.close()


def test_older_database_migrates_forward():
    conn = _connect()
    conn.executescript(_LEGACY_V1_SCHEMA)
    _stamp(conn, 1)

    final = migrate_schema(conn)
    assert final == SCHEMA_VERSION
    assert schema_version_of(conn) == SCHEMA_VERSION

    # The v2 additions actually landed.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    assert "source" in cols and "prev_hash" in cols
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "versions" in tables

    # The forward step was recorded.
    row = conn.execute("SELECT name FROM schema_migrations WHERE version = 2").fetchone()
    assert row is not None and row["name"] == MIGRATIONS[2].name
    conn.close()


def test_migrated_database_is_usable_through_sqlite_storage(tmp_path):
    # A database left at v1 by an older build must open and operate normally
    # once migrated, without the caller knowing migration happened.
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.executescript(_LEGACY_V1_SCHEMA)
    _stamp(conn, 1)
    conn.close()

    storage = SQLiteStorage(str(db))
    # The v1->v2 migration ran on open; the store is fully functional.
    run = storage.create_run(Run(goal="recover me"))
    assert run.run_id
    storage.close()


def test_newer_database_fails_closed():
    conn = _connect()
    conn.executescript(_LEGACY_V1_SCHEMA)
    _stamp(conn, SCHEMA_VERSION + 1)
    with pytest.raises(SchemaVersionError):
        migrate_schema(conn)
    conn.close()


def test_unknown_older_shape_fails_closed():
    # A database at v0 would need a migration to v1, but none is registered:
    # it must be refused rather than guessed at.
    conn = _connect()
    conn.executescript(_LEGACY_V1_SCHEMA)
    _stamp(conn, 0)
    with pytest.raises(SchemaVersionError):
        migrate_schema(conn)
    conn.close()


def test_sqlite_storage_refuses_newer_database(tmp_path):
    # End-to-end: opening a too-new database raises, not silently opens.
    db = tmp_path / "future.db"
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.executescript(_LEGACY_V1_SCHEMA)
    _stamp(conn, SCHEMA_VERSION + 1)
    conn.close()
    with pytest.raises(SchemaVersionError):
        SQLiteStorage(str(db))
