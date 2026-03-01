-- Migration 001: Initial Enhanced Schema
-- Version: 1.0.0
-- Description: Add users, auth_tokens tables. Enhance existing tables with
--              created_at, updated_at, version columns. Add indexes.
-- IDEMPOTENT: Safe to run multiple times
-- HANDLES: Both fresh installs and upgrades from legacy schema

-- Enable foreign keys
PRAGMA foreign_keys = ON;

-- =============================================================================
-- USERS TABLE (new)
-- =============================================================================
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

-- =============================================================================
-- AUTH_TOKENS TABLE (new)
-- =============================================================================
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

-- =============================================================================
-- PLAYERS TABLE
-- Create if not exists (fresh install) or alter existing (upgrade)
-- Note: ALTER TABLE cannot use datetime('now'), so we use empty string as
-- default and then UPDATE to set proper values
-- =============================================================================
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    name TEXT NOT NULL,
    batting_hand TEXT NOT NULL CHECK(batting_hand IN ('right', 'left')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

-- For existing tables, add new columns with constant defaults
ALTER TABLE players ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE players ADD COLUMN created_at TEXT DEFAULT '';
ALTER TABLE players ADD COLUMN updated_at TEXT DEFAULT '';
ALTER TABLE players ADD COLUMN version INTEGER DEFAULT 1;

-- Set timestamps for existing rows
UPDATE players SET created_at = datetime('now') WHERE created_at = '' OR created_at IS NULL;
UPDATE players SET updated_at = datetime('now') WHERE updated_at = '' OR updated_at IS NULL;
UPDATE players SET version = 1 WHERE version IS NULL;

CREATE INDEX IF NOT EXISTS idx_players_user_id ON players(user_id);
CREATE INDEX IF NOT EXISTS idx_players_name ON players(name);

-- =============================================================================
-- SESSIONS TABLE
-- Create if not exists (fresh install) or alter existing (upgrade)
-- =============================================================================
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    field_config_json TEXT,
    boundary_distance REAL DEFAULT 70.0,
    difficulty TEXT NOT NULL DEFAULT 'medium' CHECK(difficulty IN ('easy', 'medium', 'hard')),
    notes TEXT,
    is_completed INTEGER NOT NULL DEFAULT 0 CHECK(is_completed IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
);

-- For existing tables, add new columns
ALTER TABLE sessions ADD COLUMN boundary_distance REAL DEFAULT 70.0;
ALTER TABLE sessions ADD COLUMN difficulty TEXT DEFAULT 'medium';
ALTER TABLE sessions ADD COLUMN is_completed INTEGER DEFAULT 0;
ALTER TABLE sessions ADD COLUMN created_at TEXT DEFAULT '';
ALTER TABLE sessions ADD COLUMN updated_at TEXT DEFAULT '';
ALTER TABLE sessions ADD COLUMN version INTEGER DEFAULT 1;

-- Set timestamps for existing rows
UPDATE sessions SET created_at = datetime('now') WHERE created_at = '' OR created_at IS NULL;
UPDATE sessions SET updated_at = datetime('now') WHERE updated_at = '' OR updated_at IS NULL;
UPDATE sessions SET version = 1 WHERE version IS NULL;
UPDATE sessions SET difficulty = 'medium' WHERE difficulty IS NULL;
UPDATE sessions SET boundary_distance = 70.0 WHERE boundary_distance IS NULL;
UPDATE sessions SET is_completed = 0 WHERE is_completed IS NULL;

CREATE INDEX IF NOT EXISTS idx_sessions_player_id ON sessions(player_id);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);
CREATE INDEX IF NOT EXISTS idx_sessions_is_completed ON sessions(is_completed);

-- =============================================================================
-- DELIVERIES TABLE
-- Create if not exists (fresh install) or alter existing (upgrade)
-- =============================================================================
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    ball_number INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    bowling_speed REAL,
    exit_speed REAL,
    horizontal_angle REAL,
    vertical_angle REAL,
    landing_x REAL,
    landing_y REAL,
    projected_distance REAL,
    max_height REAL,
    radar_frames_captured INTEGER,
    detection_confidence REAL CHECK(detection_confidence IS NULL OR (detection_confidence >= 0 AND detection_confidence <= 1)),
    outcome TEXT NOT NULL CHECK(outcome IN ('dot', '1', '2', '3', '4', '6', 'caught', 'dropped', 'misfield')),
    runs INTEGER NOT NULL CHECK(runs >= 0 AND runs <= 6),
    fielder_position TEXT,
    fielding_position TEXT,
    end_position TEXT,
    is_boundary INTEGER NOT NULL DEFAULT 0 CHECK(is_boundary IN (0, 1)),
    is_aerial INTEGER NOT NULL DEFAULT 0 CHECK(is_aerial IN (0, 1)),
    description TEXT,
    catch_analysis TEXT,
    fielding_time REAL,
    collection_difficulty REAL CHECK(collection_difficulty IS NULL OR (collection_difficulty >= 0 AND collection_difficulty <= 1)),
    alignment_score REAL CHECK(alignment_score IS NULL OR (alignment_score >= 0 AND alignment_score <= 1)),
    priority_score REAL CHECK(priority_score IS NULL OR (priority_score >= 0 AND priority_score <= 1)),
    fielder_arrival_time REAL,
    ball_arrival_time REAL,
    is_manual_input INTEGER NOT NULL DEFAULT 0 CHECK(is_manual_input IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- For existing tables, add new columns
ALTER TABLE deliveries ADD COLUMN description TEXT;
ALTER TABLE deliveries ADD COLUMN fielding_position TEXT;
ALTER TABLE deliveries ADD COLUMN end_position TEXT;
ALTER TABLE deliveries ADD COLUMN catch_analysis TEXT;
ALTER TABLE deliveries ADD COLUMN fielding_time REAL;
ALTER TABLE deliveries ADD COLUMN collection_difficulty REAL;
ALTER TABLE deliveries ADD COLUMN alignment_score REAL;
ALTER TABLE deliveries ADD COLUMN priority_score REAL;
ALTER TABLE deliveries ADD COLUMN fielder_arrival_time REAL;
ALTER TABLE deliveries ADD COLUMN ball_arrival_time REAL;
ALTER TABLE deliveries ADD COLUMN is_manual_input INTEGER DEFAULT 0;
ALTER TABLE deliveries ADD COLUMN created_at TEXT DEFAULT '';
ALTER TABLE deliveries ADD COLUMN updated_at TEXT DEFAULT '';
ALTER TABLE deliveries ADD COLUMN version INTEGER DEFAULT 1;

-- Set timestamps for existing rows
UPDATE deliveries SET created_at = datetime('now') WHERE created_at = '' OR created_at IS NULL;
UPDATE deliveries SET updated_at = datetime('now') WHERE updated_at = '' OR updated_at IS NULL;
UPDATE deliveries SET version = 1 WHERE version IS NULL;
UPDATE deliveries SET is_manual_input = 0 WHERE is_manual_input IS NULL;

CREATE INDEX IF NOT EXISTS idx_deliveries_session_id ON deliveries(session_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_ball_number ON deliveries(session_id, ball_number);
CREATE INDEX IF NOT EXISTS idx_deliveries_outcome ON deliveries(outcome);
CREATE INDEX IF NOT EXISTS idx_deliveries_timestamp ON deliveries(timestamp);

-- =============================================================================
-- TRIGGERS FOR updated_at (auto-increment version on update)
-- =============================================================================

-- Drop existing triggers if they exist (for idempotency)
DROP TRIGGER IF EXISTS users_updated_at;
DROP TRIGGER IF EXISTS auth_tokens_updated_at;
DROP TRIGGER IF EXISTS players_updated_at;
DROP TRIGGER IF EXISTS sessions_updated_at;
DROP TRIGGER IF EXISTS deliveries_updated_at;

CREATE TRIGGER users_updated_at
AFTER UPDATE ON users
BEGIN
    UPDATE users SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

CREATE TRIGGER auth_tokens_updated_at
AFTER UPDATE ON auth_tokens
BEGIN
    UPDATE auth_tokens SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

CREATE TRIGGER players_updated_at
AFTER UPDATE ON players
BEGIN
    UPDATE players SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

CREATE TRIGGER sessions_updated_at
AFTER UPDATE ON sessions
BEGIN
    UPDATE sessions SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

CREATE TRIGGER deliveries_updated_at
AFTER UPDATE ON deliveries
BEGIN
    UPDATE deliveries SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

-- =============================================================================
-- SESSION_SUMMARIES VIEW
-- =============================================================================
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
