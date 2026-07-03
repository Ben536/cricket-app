#!/usr/bin/env python3
"""Regenerate contracts/database_schema.sql from a freshly-migrated database.

Run after adding a migration so the human-readable contract stays true.
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.migrate import MigrationRunner  # noqa: E402

fd, db = tempfile.mkstemp(suffix=".db")
os.close(fd)
runner = MigrationRunner(Path(db))
runner.connect()
runner.ensure_migrations_table()
count = runner.run_all_pending()
runner.close()

conn = sqlite3.connect(db)
rows = conn.execute(
    "SELECT type, name, sql FROM sqlite_master "
    "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' AND name != '_migrations' "
    "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 "
    "WHEN 'trigger' THEN 2 WHEN 'view' THEN 3 END, name"
).fetchall()
conn.close()
os.unlink(db)

header = f"""-- Cricket App Database Schema
-- =============================================================================
-- GENERATED FILE - do not edit by hand and do NOT use for provisioning.
--
-- The single source of truth is db/migrations/ (run `python3 -m db.migrate`).
-- This file is a human-readable snapshot of the schema those migrations
-- produce, regenerated from a freshly-migrated database. To refresh it:
--
--     python3 tools/regen_schema_contract.py
--
-- Schema version: {count} migrations applied
-- =============================================================================

"""
out = Path(__file__).parent.parent / "contracts" / "database_schema.sql"
out.write_text(header + "".join(f"{sql};\n\n" for _, _, sql in rows))
print(f"regenerated {out} ({len(rows)} objects, {count} migrations)")
