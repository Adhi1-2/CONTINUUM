"""Durable storage engines."""

from pathlib import Path

from continuum.storage.base import (
    CheckpointNotFound,
    ConcurrentWriteError,
    CorruptedRecord,
    RunNotFound,
    SchemaVersionError,
    Storage,
    StorageError,
)
from continuum.storage.sqlite import SQLiteStorage

__all__ = [
    "CheckpointNotFound",
    "ConcurrentWriteError",
    "CorruptedRecord",
    "RunNotFound",
    "SQLiteStorage",
    "SchemaVersionError",
    "Storage",
    "StorageError",
    "open_storage",
]


def open_storage(url: str | Path = ":memory:") -> Storage:
    """Open a storage engine from a URL.

    Supported: ``sqlite:///path.db`` (or a bare path / ``:memory:``) and
    ``postgresql://`` / ``postgres://`` (requires the ``[postgres]`` extra).
    An unrecognized scheme fails clearly rather than silently falling back.
    """
    raw = str(url)
    scheme = raw.split("://", 1)[0].lower() if "://" in raw else ""

    if scheme in ("postgres", "postgresql"):
        from continuum.storage.postgres import PostgresStorage

        return PostgresStorage(raw)
    if scheme not in ("", "sqlite"):
        raise ValueError(f"unsupported storage URL scheme: {scheme!r}")
    return SQLiteStorage(raw)
