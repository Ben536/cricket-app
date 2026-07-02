-- Rollback for migration 004: restore the pre-004 indexes and triggers.
--
-- Renumbered ball_numbers are NOT restored (the originals are gone; the
-- renumbering preserved order, so nothing is lost by keeping it).
--
-- Executed via sqlite3 executescript (not the line splitter), so plain SQL.

PRAGMA foreign_keys = ON;

-- Non-unique composite index + prefix index, as created by migration 003
DROP INDEX IF EXISTS idx_deliveries_ball_number;
CREATE INDEX IF NOT EXISTS idx_deliveries_ball_number ON deliveries(session_id, ball_number);
CREATE INDEX IF NOT EXISTS idx_deliveries_session_id ON deliveries(session_id);

-- Triggers back to datetime('now') format
DROP TRIGGER IF EXISTS users_updated_at;
CREATE TRIGGER users_updated_at
AFTER UPDATE ON users
BEGIN
    UPDATE users SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS auth_tokens_updated_at;
CREATE TRIGGER auth_tokens_updated_at
AFTER UPDATE ON auth_tokens
BEGIN
    UPDATE auth_tokens SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS players_updated_at;
CREATE TRIGGER players_updated_at
AFTER UPDATE ON players
BEGIN
    UPDATE players SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS sessions_updated_at;
CREATE TRIGGER sessions_updated_at
AFTER UPDATE ON sessions
BEGIN
    UPDATE sessions SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

DROP TRIGGER IF EXISTS deliveries_updated_at;
CREATE TRIGGER deliveries_updated_at
AFTER UPDATE ON deliveries
BEGIN
    UPDATE deliveries SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;
