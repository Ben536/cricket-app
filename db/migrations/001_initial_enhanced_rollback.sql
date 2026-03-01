-- Rollback for Migration 001: Initial Enhanced Schema
-- WARNING: This only drops the NEW tables (users, auth_tokens).
-- Column additions to existing tables cannot be rolled back in SQLite
-- without recreating the tables (which risks data loss).

-- Drop view first
DROP VIEW IF EXISTS session_summaries;

-- Drop triggers
DROP TRIGGER IF EXISTS users_updated_at;
DROP TRIGGER IF EXISTS auth_tokens_updated_at;
DROP TRIGGER IF EXISTS players_updated_at;
DROP TRIGGER IF EXISTS sessions_updated_at;
DROP TRIGGER IF EXISTS deliveries_updated_at;

-- Drop new tables (cascade will handle foreign keys)
DROP TABLE IF EXISTS auth_tokens;
DROP TABLE IF EXISTS users;

-- Note: The following columns CANNOT be removed from existing tables
-- without recreating them (SQLite limitation):
-- - players: user_id, created_at, updated_at, version
-- - sessions: boundary_distance, difficulty, is_completed, created_at, updated_at, version
-- - deliveries: description, fielding_position, end_position, catch_analysis,
--               fielding_time, collection_difficulty, alignment_score, priority_score,
--               fielder_arrival_time, ball_arrival_time, is_manual_input,
--               created_at, updated_at, version
--
-- To fully rollback, restore from backup: cp cricket.db.backup cricket.db
