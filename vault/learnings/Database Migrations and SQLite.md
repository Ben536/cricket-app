# Learning: Database Migrations and SQLite

Captured 2026-06-27 while deploying migration 003 (extending the delivery outcome CHECK).

## migrate.py is NOT atomic
The runner executes statements one-by-one and SQLite **auto-commits DDL** (CREATE/DROP/ALTER). So a migration that fails halfway leaves the DB **partially applied** — a failed run dropped the `session_summaries` view and the `deliveries` trigger but didn't recreate them, and left an orphan `deliveries_new` table.

**Implication:** every migration must be **idempotent / re-runnable**. Use `DROP ... IF EXISTS`, `CREATE ... IF NOT EXISTS`, and drop any scratch tables (`DROP TABLE IF EXISTS deliveries_new`) at the top so a re-run converges from a partial state.

## Changing a CHECK constraint = table rebuild
SQLite can't `ALTER` a CHECK constraint. You must: create new table → copy rows → drop old → rename. 

## Order matters: drop dependents BEFORE swapping the table
Modern SQLite (Pi, >= 3.25) validates dependent **views/triggers** during `DROP TABLE` / `ALTER ... RENAME`. If a view still references the table mid-rebuild you get:
`error in view session_summaries: no such table: main.deliveries`.
→ **Drop the view and trigger first**, swap the table, then recreate them. (macOS system SQLite was more lenient and hid this — always test on the Pi.)

## The runner's "skip ALTER on error" is a trap
`migrate.py` swallows `OperationalError` containing "no such table" + "alter" and **continues**, which masked the real failure above. Watch for `WARNING: ... skipping` in migration output — it can hide a broken migration.

## Always back up before migrating real data
`cp db/cricket.db db/cricket.db.bak-<date>` on the Pi before running. Did this for 003; data (5 deliveries) survived every failed attempt because the final state rolled back, but don't rely on luck.

## See also
[[Pi Deployment and Ops]]
