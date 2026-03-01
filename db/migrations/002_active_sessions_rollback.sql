-- Rollback for Migration 002: Active Sessions Table

-- Drop trigger first
DROP TRIGGER IF EXISTS active_sessions_updated_at;

-- Drop the active_sessions table
DROP TABLE IF EXISTS active_sessions;
