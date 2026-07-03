"""Shared fixtures: repo importability + a freshly-migrated temp database."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def migrated_db():
    """A temp database with all migrations applied. Yields its Path."""
    from db.migrate import MigrationRunner

    fd, raw = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_path = Path(raw)

    runner = MigrationRunner(db_path)
    runner.connect()
    runner.ensure_migrations_table()
    runner.run_all_pending()
    runner.close()

    yield db_path

    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()


@pytest.fixture
def repository(migrated_db):
    from db.repository import Repository

    return Repository(migrated_db)
