-- Cricket App Database Schema
-- Version: 1.0.0
-- All tables include: id, created_at, updated_at, version (for optimistic locking)
-- Foreign key constraints enforced

PRAGMA foreign_keys = ON;

-- =============================================================================
-- USERS TABLE
-- User accounts for authentication (future: multi-device sync)
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
-- AUTH_TOKENS TABLE
-- Session tokens for API authentication
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
-- Player profiles with batting preferences
-- =============================================================================
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,  -- NULL for local-only profiles
    name TEXT NOT NULL,
    batting_hand TEXT NOT NULL CHECK(batting_hand IN ('right', 'left')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_players_user_id ON players(user_id);
CREATE INDEX IF NOT EXISTS idx_players_name ON players(name);

-- =============================================================================
-- SESSIONS TABLE
-- Practice sessions linked to players
-- =============================================================================
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    field_config_json TEXT,  -- JSON array of fielder positions
    boundary_distance REAL DEFAULT 70.0,
    difficulty TEXT NOT NULL DEFAULT 'medium' CHECK(difficulty IN ('easy', 'medium', 'hard')),
    notes TEXT,
    is_completed INTEGER NOT NULL DEFAULT 0 CHECK(is_completed IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_player_id ON sessions(player_id);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);
CREATE INDEX IF NOT EXISTS idx_sessions_is_completed ON sessions(is_completed);

-- =============================================================================
-- ACTIVE_SESSIONS TABLE
-- Currently active sessions (for WebSocket reconnection)
-- =============================================================================
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

-- =============================================================================
-- DELIVERIES TABLE
-- Individual ball data with tracking metrics
-- =============================================================================
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    ball_number INTEGER NOT NULL,
    timestamp TEXT NOT NULL,

    -- Radar tracking data (nullable if manual input)
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

    -- Simulation result
    outcome TEXT NOT NULL CHECK(outcome IN ('dot', '1', '2', '3', '4', '6', 'caught', 'dropped', 'misfield')),
    runs INTEGER NOT NULL CHECK(runs >= 0 AND runs <= 6),
    fielder_position TEXT,  -- JSON: {x, y, name}
    fielding_position TEXT, -- JSON: {x, y} where fielder collected ball
    end_position TEXT,      -- JSON: {x, y} where ball ended up
    is_boundary INTEGER NOT NULL DEFAULT 0 CHECK(is_boundary IN (0, 1)),
    is_aerial INTEGER NOT NULL DEFAULT 0 CHECK(is_aerial IN (0, 1)),
    description TEXT,

    -- Extended simulation data
    catch_analysis TEXT,    -- JSON: full CatchAnalysis object
    fielding_time REAL,
    collection_difficulty REAL CHECK(collection_difficulty IS NULL OR (collection_difficulty >= 0 AND collection_difficulty <= 1)),
    alignment_score REAL CHECK(alignment_score IS NULL OR (alignment_score >= 0 AND alignment_score <= 1)),
    priority_score REAL CHECK(priority_score IS NULL OR (priority_score >= 0 AND priority_score <= 1)),
    fielder_arrival_time REAL,
    ball_arrival_time REAL,

    -- Metadata
    is_manual_input INTEGER NOT NULL DEFAULT 0 CHECK(is_manual_input IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    version INTEGER NOT NULL DEFAULT 1,

    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_deliveries_session_id ON deliveries(session_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_ball_number ON deliveries(session_id, ball_number);
CREATE INDEX IF NOT EXISTS idx_deliveries_outcome ON deliveries(outcome);
CREATE INDEX IF NOT EXISTS idx_deliveries_timestamp ON deliveries(timestamp);

-- =============================================================================
-- SESSION_SUMMARIES VIEW
-- Computed summary stats for sessions (materialized for performance)
-- =============================================================================
CREATE VIEW IF NOT EXISTS session_summaries AS
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

-- =============================================================================
-- TRIGGERS FOR updated_at
-- =============================================================================
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
