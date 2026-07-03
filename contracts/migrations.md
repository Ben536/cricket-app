# Database Migration Guide

**The single provisioning and upgrade path is the migration runner:**

```bash
python3 -m db.migrate            # apply all pending migrations to db/cricket.db
python3 -m db.migrate --status   # show what's applied
python3 -m db.migrate --rollback # roll back the last migration
```

Fresh installs and upgrades use the SAME command - the runner tracks applied
migrations in the `_migrations` table and each migration is idempotent.
NEVER provision from `database_schema.sql`; that file is a generated,
read-only snapshot of what the migrations produce (see its header). A DB
created from it would have no `_migrations` records and the runner would
re-apply everything over it.

The live database is `db/cricket.db` (see `db/repository.py DEFAULT_DB_PATH`).
`scripts/deploy_to_pi.sh` backs up and migrates it on every deploy.

## Version History

| Migration | Description |
|-----------|-------------|
| 001_initial_enhanced | Users, auth_tokens, players, sessions, deliveries, triggers, session_summaries view |
| 002_active_sessions | active_sessions WebSocket session tracker |
| 003_delivery_outcomes | Widened outcome CHECK (W/wd/nb/bowled/lbw/run_out/stumped/hit_wicket); extras excluded from balls faced |
| 004_integrity | UNIQUE(session_id, ball_number) + renumbering; ISO-8601 UTC trigger timestamps; drop redundant index |

## Migration Strategy

The cricket app uses SQLite with incremental migrations. Each migration is idempotent (safe to run multiple times).

### Pre-Migration Checklist

1. Back up the existing database: `cp db/cricket.db db/cricket.db.backup` (the deploy script does this automatically)
2. Verify SQLite version >= 3.35.0 for full feature support
3. Run migrations during low-traffic periods

---

## Migration: From Legacy Schema

If migrating from an earlier prototype, apply these changes incrementally.

### Step 1: Add missing columns to players table

```sql
-- Add version column for optimistic locking if missing
ALTER TABLE players ADD COLUMN version INTEGER NOT NULL DEFAULT 1;

-- Add updated_at if missing
ALTER TABLE players ADD COLUMN updated_at TEXT NOT NULL DEFAULT (datetime('now'));
```

### Step 2: Create users table (if not exists)

```sql
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
```

### Step 3: Create auth_tokens table (if not exists)

```sql
CREATE TABLE IF NOT EXISTS auth_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token TEXT UNIQUE NOT NULL,
    expires_at TEXT NOT NULL,
    device_info TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_auth_tokens_token ON auth_tokens(token);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_user_id ON auth_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_expires_at ON auth_tokens(expires_at);
```

### Step 4: Add user_id to players table

```sql
-- Add user_id column (nullable for local-only profiles)
ALTER TABLE players ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_players_user_id ON players(user_id);
```

### Step 5: Update sessions table

```sql
-- Add missing columns to sessions
ALTER TABLE sessions ADD COLUMN field_config_json TEXT;
ALTER TABLE sessions ADD COLUMN boundary_distance REAL DEFAULT 70.0;
ALTER TABLE sessions ADD COLUMN difficulty TEXT NOT NULL DEFAULT 'medium';
ALTER TABLE sessions ADD COLUMN notes TEXT;
ALTER TABLE sessions ADD COLUMN is_completed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN version INTEGER NOT NULL DEFAULT 1;

-- Validate difficulty values
UPDATE sessions SET difficulty = 'medium'
WHERE difficulty NOT IN ('easy', 'medium', 'hard');
```

### Step 6: Create active_sessions table

```sql
CREATE TABLE IF NOT EXISTS active_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER UNIQUE NOT NULL,
    websocket_id TEXT NOT NULL,
    last_heartbeat TEXT NOT NULL DEFAULT (datetime('now')),
    client_ip TEXT,
    client_user_agent TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_active_sessions_session_id ON active_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_active_sessions_websocket_id ON active_sessions(websocket_id);
CREATE INDEX IF NOT EXISTS idx_active_sessions_last_heartbeat ON active_sessions(last_heartbeat);
```

### Step 7: Update deliveries table

```sql
-- Add extended simulation columns
ALTER TABLE deliveries ADD COLUMN catch_analysis TEXT;
ALTER TABLE deliveries ADD COLUMN fielding_time REAL;
ALTER TABLE deliveries ADD COLUMN collection_difficulty REAL;
ALTER TABLE deliveries ADD COLUMN alignment_score REAL;
ALTER TABLE deliveries ADD COLUMN priority_score REAL;
ALTER TABLE deliveries ADD COLUMN fielder_arrival_time REAL;
ALTER TABLE deliveries ADD COLUMN ball_arrival_time REAL;
ALTER TABLE deliveries ADD COLUMN version INTEGER NOT NULL DEFAULT 1;

-- Add check constraints (SQLite 3.25+)
-- Note: These require table recreation in older SQLite versions
```

### Step 8: Create session_summaries view

```sql
DROP VIEW IF EXISTS session_summaries;

CREATE VIEW session_summaries AS
SELECT
    s.id as session_id,
    s.player_id,
    s.date,
    s.difficulty,
    s.is_completed,
    p.name as player_name,
    p.batting_hand,
    COALESCE(SUM(d.runs), 0) as total_runs,
    COUNT(d.id) as balls_faced,
    SUM(CASE WHEN d.outcome = '4' THEN 1 ELSE 0 END) as fours,
    SUM(CASE WHEN d.outcome = '6' THEN 1 ELSE 0 END) as sixes,
    SUM(CASE WHEN d.outcome = 'caught' THEN 1 ELSE 0 END) as dismissals,
    CASE
        WHEN COUNT(d.id) > 0 THEN ROUND((CAST(COALESCE(SUM(d.runs), 0) AS REAL) / COUNT(d.id)) * 100, 2)
        ELSE 0
    END as strike_rate,
    ROUND(AVG(d.exit_speed), 1) as avg_exit_speed,
    MAX(d.exit_speed) as max_exit_speed
FROM sessions s
JOIN players p ON s.player_id = p.id
LEFT JOIN deliveries d ON s.id = d.session_id
GROUP BY s.id;
```

### Step 9: Create update triggers

```sql
-- These triggers auto-update the updated_at and version columns

CREATE TRIGGER IF NOT EXISTS users_updated_at
AFTER UPDATE ON users
BEGIN
    UPDATE users SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS auth_tokens_updated_at
AFTER UPDATE ON auth_tokens
BEGIN
    UPDATE auth_tokens SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS players_updated_at
AFTER UPDATE ON players
BEGIN
    UPDATE players SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS sessions_updated_at
AFTER UPDATE ON sessions
BEGIN
    UPDATE sessions SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS active_sessions_updated_at
AFTER UPDATE ON active_sessions
BEGIN
    UPDATE active_sessions SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS deliveries_updated_at
AFTER UPDATE ON deliveries
BEGIN
    UPDATE deliveries SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;
```

---

## Rollback Procedures

### Rollback to backup

```bash
cp cricket.db.backup cricket.db
```

### Drop new tables (if needed)

```sql
DROP TABLE IF EXISTS active_sessions;
DROP TABLE IF EXISTS auth_tokens;
DROP TABLE IF EXISTS users;
DROP VIEW IF EXISTS session_summaries;
```

---

## Data Validation Queries

Run these after migration to verify data integrity:

```sql
-- Check for orphaned sessions
SELECT s.id FROM sessions s
LEFT JOIN players p ON s.player_id = p.id
WHERE p.id IS NULL;

-- Check for orphaned deliveries
SELECT d.id FROM deliveries d
LEFT JOIN sessions s ON d.session_id = s.id
WHERE s.id IS NULL;

-- Verify difficulty values
SELECT DISTINCT difficulty FROM sessions;

-- Check batting_hand values
SELECT DISTINCT batting_hand FROM players;
```

---

## Notes on JSON Serialization

### CatchAnalysis Field Naming

The Python API uses snake_case internally (`can_catch`, `catch_type`), but JSON wire format uses camelCase (`canCatch`, `catchType`) to match TypeScript conventions.

When storing `catch_analysis` in the deliveries table, use camelCase JSON to maintain consistency with the wire protocol.

### SimulationResult Enums

Python enums (e.g., `ShotOutcome.CAUGHT`) serialize to their string values (e.g., `"caught"`) in JSON. No special handling is required when storing or retrieving from the database.

---

## Future Migrations

When adding new migrations:

1. Add a new section to this document with the migration version
2. Include both upgrade and rollback SQL
3. Update the version history table
4. Test migrations against a copy of production data before deploying
