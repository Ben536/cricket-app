"""Migration runner: fresh install, idempotency, 004 integrity, rollback."""

import sqlite3

import pytest

from db.migrate import MigrationRunner


def test_fresh_install_applies_all_migrations(migrated_db):
    conn = sqlite3.connect(migrated_db)
    applied = [r[0] for r in conn.execute("SELECT name FROM _migrations ORDER BY name")]
    conn.close()
    assert len(applied) >= 4
    assert applied[0].startswith("001") and applied[3].startswith("004")


def test_rerun_is_noop(migrated_db):
    runner = MigrationRunner(migrated_db)
    runner.connect()
    runner.ensure_migrations_table()
    assert runner.run_all_pending() == 0
    runner.close()


def test_004_renumbers_duplicates_and_enforces_uniqueness(migrated_db):
    conn = sqlite3.connect(migrated_db)
    # Simulate a pre-004 database state with duplicate ball numbers
    conn.execute("DROP INDEX idx_deliveries_ball_number")
    conn.execute("INSERT INTO players (name, batting_hand) VALUES ('T','right')")
    conn.execute("INSERT INTO sessions (player_id, date, difficulty) VALUES (1,'2026-07-03','medium')")
    for bn in (1, 2, 2, 3):
        conn.execute(
            "INSERT INTO deliveries (session_id, ball_number, timestamp, outcome, runs) "
            "VALUES (1, ?, ?, 'dot', 0)", (bn, f"t{bn}"),
        )
    conn.execute("DELETE FROM _migrations WHERE name LIKE '004%'")
    conn.commit()
    conn.close()

    runner = MigrationRunner(migrated_db)
    runner.connect()
    runner.ensure_migrations_table()
    assert runner.run_all_pending() == 1
    runner.close()

    conn = sqlite3.connect(migrated_db)
    numbers = [r[0] for r in conn.execute("SELECT ball_number FROM deliveries ORDER BY id")]
    assert numbers == [1, 2, 3, 4]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO deliveries (session_id, ball_number, timestamp, outcome, runs) "
            "VALUES (1, 2, 't', 'dot', 0)"
        )
    conn.close()


def test_004_triggers_write_iso_utc_timestamps(migrated_db):
    conn = sqlite3.connect(migrated_db)
    conn.execute("INSERT INTO players (name, batting_hand) VALUES ('T','right')")
    conn.commit()
    conn.execute("UPDATE players SET name = 'T2' WHERE id = 1")
    conn.commit()
    ts = conn.execute("SELECT updated_at FROM players WHERE id = 1").fetchone()[0]
    conn.close()
    assert "T" in ts and ts.endswith("Z"), ts


def test_rollback_then_reapply(migrated_db):
    runner = MigrationRunner(migrated_db)
    runner.connect()
    assert runner.rollback(1) == 1
    assert runner.run_all_pending() == 1
    runner.close()


def test_statement_splitter_ignores_semicolons_in_comments(migrated_db):
    runner = MigrationRunner(migrated_db)
    statements = runner._split_statements(
        "-- comment with a ; semicolon\n"
        "CREATE TABLE t (a INTEGER); -- trailing ; comment\n"
        "INSERT INTO t VALUES (1);\n"
    )
    assert len(statements) == 2, statements
