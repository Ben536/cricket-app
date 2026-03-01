#!/usr/bin/env python3
"""
Cricket App Database Migration Runner

Tracks applied migrations in a migrations table and runs pending migrations in order.
Supports rollback with --rollback flag.

Usage:
    python migrate.py                  # Run all pending migrations
    python migrate.py --rollback       # Rollback last migration
    python migrate.py --rollback 2     # Rollback last 2 migrations
    python migrate.py --status         # Show migration status
    python migrate.py --db /path/to.db # Use specific database file
"""

import argparse
import hashlib
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Default paths
DEFAULT_DB_PATH = Path(__file__).parent / "cricket.db"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class MigrationError(Exception):
    """Migration-related error."""
    pass


class MigrationRunner:
    """Handles database migrations with tracking and rollback support."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        """Connect to the database."""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        logger.info(f"Connected to database: {self.db_path}")

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def ensure_migrations_table(self) -> None:
        """Create migrations tracking table if it doesn't exist."""
        if not self.conn:
            raise MigrationError("Not connected to database")

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now')),
                execution_time_ms INTEGER,
                success INTEGER NOT NULL DEFAULT 1
            )
        """)
        self.conn.commit()
        logger.debug("Migrations table ready")

    def get_applied_migrations(self) -> list[dict]:
        """Get list of already applied migrations."""
        if not self.conn:
            raise MigrationError("Not connected to database")

        cursor = self.conn.execute(
            "SELECT name, checksum, applied_at, execution_time_ms, success "
            "FROM _migrations ORDER BY id"
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_pending_migrations(self) -> list[Path]:
        """Get list of migration files that haven't been applied."""
        if not MIGRATIONS_DIR.exists():
            logger.warning(f"Migrations directory not found: {MIGRATIONS_DIR}")
            return []

        # Get all SQL files sorted by name, excluding rollback files
        migration_files = sorted(
            f for f in MIGRATIONS_DIR.glob("*.sql")
            if not f.name.endswith("_rollback.sql")
        )

        # Get names of already applied migrations
        applied = {m['name'] for m in self.get_applied_migrations()}

        # Return pending migrations
        pending = [f for f in migration_files if f.name not in applied]
        return pending

    def calculate_checksum(self, content: str) -> str:
        """Calculate SHA-256 checksum of migration content."""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]

    def run_migration(self, migration_path: Path) -> None:
        """Run a single migration file."""
        if not self.conn:
            raise MigrationError("Not connected to database")

        name = migration_path.name
        content = migration_path.read_text()
        checksum = self.calculate_checksum(content)

        logger.info(f"Running migration: {name}")

        start_time = datetime.now()

        try:
            # Execute migration within transaction
            # Split by statements to handle ALTER TABLE which can fail
            statements = self._split_statements(content)

            for statement in statements:
                statement = statement.strip()
                if not statement:
                    continue

                try:
                    self.conn.execute(statement)
                except sqlite3.OperationalError as e:
                    # Handle "duplicate column" errors for idempotent ALTER TABLE
                    error_msg = str(e).lower()
                    if "duplicate column name" in error_msg:
                        logger.debug(f"Column already exists, skipping: {e}")
                        continue
                    elif "no such table" in error_msg and "alter" in statement.lower():
                        # Table doesn't exist for ALTER, might be a fresh install
                        logger.warning(f"Table not found for ALTER, skipping: {e}")
                        continue
                    else:
                        raise

            # Record successful migration
            execution_time_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self.conn.execute(
                "INSERT INTO _migrations (name, checksum, execution_time_ms, success) "
                "VALUES (?, ?, ?, 1)",
                (name, checksum, execution_time_ms)
            )
            self.conn.commit()

            logger.info(f"Migration completed: {name} ({execution_time_ms}ms)")

        except Exception as e:
            self.conn.rollback()
            logger.error(f"Migration failed: {name} - {e}")
            raise MigrationError(f"Migration {name} failed: {e}")

    def _split_statements(self, content: str) -> list[str]:
        """
        Split SQL content into individual statements.
        Handles triggers with BEGIN/END blocks.
        """
        statements = []
        current = []
        in_trigger = False

        for line in content.split('\n'):
            stripped = line.strip().upper()

            # Track if we're inside a trigger definition
            if 'CREATE TRIGGER' in stripped:
                in_trigger = True
            elif in_trigger and stripped == 'END;':
                current.append(line)
                statements.append('\n'.join(current))
                current = []
                in_trigger = False
                continue

            if in_trigger:
                current.append(line)
                continue

            # For non-trigger statements, split by semicolon
            if ';' in line and not in_trigger:
                parts = line.split(';')
                for i, part in enumerate(parts):
                    if i < len(parts) - 1:
                        current.append(part)
                        statements.append('\n'.join(current))
                        current = []
                    elif part.strip():
                        current.append(part)
            else:
                current.append(line)

        if current and ''.join(current).strip():
            statements.append('\n'.join(current))

        return statements

    def run_all_pending(self) -> int:
        """Run all pending migrations. Returns count of migrations run."""
        pending = self.get_pending_migrations()

        if not pending:
            logger.info("No pending migrations")
            return 0

        logger.info(f"Found {len(pending)} pending migration(s)")

        for migration_path in pending:
            self.run_migration(migration_path)

        return len(pending)

    def rollback(self, count: int = 1) -> int:
        """
        Rollback the last N migrations.

        Note: This only removes migration records. Actual schema rollback
        requires manual intervention as SQLite has limited ALTER TABLE support.
        """
        if not self.conn:
            raise MigrationError("Not connected to database")

        applied = self.get_applied_migrations()

        if not applied:
            logger.info("No migrations to rollback")
            return 0

        # Get the last N migrations
        to_rollback = applied[-count:]

        logger.warning(
            f"Rolling back {len(to_rollback)} migration(s). "
            "Note: Schema changes must be manually reverted."
        )

        for migration in reversed(to_rollback):
            name = migration['name']
            logger.info(f"Rolling back: {name}")

            # Remove migration record
            self.conn.execute("DELETE FROM _migrations WHERE name = ?", (name,))

            # Check for corresponding rollback file
            rollback_file = MIGRATIONS_DIR / name.replace('.sql', '_rollback.sql')
            if rollback_file.exists():
                logger.info(f"Executing rollback script: {rollback_file.name}")
                content = rollback_file.read_text()
                try:
                    self.conn.executescript(content)
                except sqlite3.Error as e:
                    logger.error(f"Rollback script failed: {e}")
                    self.conn.rollback()
                    raise MigrationError(f"Rollback failed: {e}")

        self.conn.commit()
        logger.info(f"Rolled back {len(to_rollback)} migration(s)")

        return len(to_rollback)

    def status(self) -> None:
        """Print migration status."""
        applied = self.get_applied_migrations()
        pending = self.get_pending_migrations()

        print("\n=== Migration Status ===\n")
        print(f"Database: {self.db_path}")
        print(f"Migrations directory: {MIGRATIONS_DIR}")
        print()

        if applied:
            print("Applied migrations:")
            for m in applied:
                status = "OK" if m['success'] else "FAILED"
                print(f"  [{status}] {m['name']} (applied: {m['applied_at']}, {m['execution_time_ms']}ms)")
        else:
            print("No migrations have been applied yet.")

        print()

        if pending:
            print("Pending migrations:")
            for p in pending:
                print(f"  [ ] {p.name}")
        else:
            print("No pending migrations.")

        print()


def main():
    parser = argparse.ArgumentParser(
        description="Cricket App Database Migration Runner"
    )
    parser.add_argument(
        '--db',
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Database file path (default: {DEFAULT_DB_PATH})"
    )
    parser.add_argument(
        '--rollback',
        nargs='?',
        const=1,
        type=int,
        metavar='N',
        help="Rollback last N migrations (default: 1)"
    )
    parser.add_argument(
        '--status',
        action='store_true',
        help="Show migration status"
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    runner = MigrationRunner(args.db)

    try:
        runner.connect()
        runner.ensure_migrations_table()

        if args.status:
            runner.status()
        elif args.rollback:
            count = runner.rollback(args.rollback)
            sys.exit(0 if count > 0 else 1)
        else:
            count = runner.run_all_pending()
            if count == 0:
                logger.info("Database is up to date")

    except MigrationError as e:
        logger.error(str(e))
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)
    finally:
        runner.close()


if __name__ == "__main__":
    main()
