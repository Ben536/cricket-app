-- Migration 004: Data integrity - unique ball numbers, consistent timestamps
--
-- Why:
--   (a) ball_number had no uniqueness guarantee. Assignment used to be
--       read-then-insert in the handler, so two clients in one session could
--       insert duplicate numbers - and undo (delete highest ball_number)
--       would then remove an arbitrary one. Numbers are renumbered by insert
--       order and locked with a UNIQUE index; assignment now happens
--       atomically inside the INSERT (repository.create_delivery).
--   (b) updated_at triggers wrote local-format 'YYYY-MM-DD HH:MM:SS' while
--       application code writes ISO-8601 UTC with 'Z'. Mixed formats break
--       lexicographic ordering and string comparison. Triggers are recreated
--       with the ISO-8601 UTC format. (created_at DEFAULTs keep the legacy
--       format - changing a DEFAULT requires a full table rebuild, and
--       nothing orders by created_at.)
--
-- migrate.py is not transactional (SQLite auto-commits DDL), so this
-- migration is idempotent: renumbering is deterministic, and all drops/
-- creates use IF EXISTS / IF NOT EXISTS.

PRAGMA foreign_keys = ON;

-- 1. Renumber deliveries per session by insert order (deterministic, fixes
--    any historical duplicates so the UNIQUE index below can be created)
UPDATE deliveries SET ball_number = (
    SELECT COUNT(*) FROM deliveries d2
    WHERE d2.session_id = deliveries.session_id AND d2.id <= deliveries.id
);

-- 2. Enforce uniqueness (also serves the (session_id, ball_number) lookups)
DROP INDEX IF EXISTS idx_deliveries_ball_number;
CREATE UNIQUE INDEX IF NOT EXISTS idx_deliveries_ball_number
    ON deliveries(session_id, ball_number);

-- 3. Drop the redundant prefix index (idx_deliveries_ball_number covers all
--    session_id lookups; the extra index just costs a write per delivery on
--    the SD card)
DROP INDEX IF EXISTS idx_deliveries_session_id;

-- 4. Recreate updated_at triggers with ISO-8601 UTC ('Z') timestamps
DROP TRIGGER IF EXISTS users_updated_at;
CREATE TRIGGER users_updated_at
AFTER UPDATE ON users
BEGIN
    UPDATE users SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), version = version + 1
    WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS auth_tokens_updated_at;
CREATE TRIGGER auth_tokens_updated_at
AFTER UPDATE ON auth_tokens
BEGIN
    UPDATE auth_tokens SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), version = version + 1
    WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS players_updated_at;
CREATE TRIGGER players_updated_at
AFTER UPDATE ON players
BEGIN
    UPDATE players SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), version = version + 1
    WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS sessions_updated_at;
CREATE TRIGGER sessions_updated_at
AFTER UPDATE ON sessions
BEGIN
    UPDATE sessions SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), version = version + 1
    WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS deliveries_updated_at;
CREATE TRIGGER deliveries_updated_at
AFTER UPDATE ON deliveries
BEGIN
    UPDATE deliveries SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), version = version + 1
    WHERE id = NEW.id;
END;
