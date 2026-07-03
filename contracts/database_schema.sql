-- Cricket App Database Schema
-- =============================================================================
-- GENERATED FILE - do not edit by hand and do NOT use for provisioning.
--
-- The single source of truth is db/migrations/ (run `python3 -m db.migrate`).
-- This file is a human-readable snapshot of the schema those migrations
-- produce, regenerated from a freshly-migrated database. To refresh it:
--
--     python3 tools/regen_schema_contract.py
--
-- Schema version: migrations 001-004
-- =============================================================================

CREATE TABLE active_sessions (
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

CREATE TABLE auth_tokens (
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

CREATE TABLE "deliveries" (
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
    outcome TEXT NOT NULL CHECK(outcome IN ('dot', '1', '2', '3', '4', '6', 'caught', 'dropped', 'misfield', 'W', 'wd', 'nb', 'bowled', 'lbw', 'run_out', 'stumped', 'hit_wicket')),
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

CREATE TABLE players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    name TEXT NOT NULL,
    batting_hand TEXT NOT NULL CHECK(batting_hand IN ('right', 'left')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    version INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
);

CREATE TABLE sessions (
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

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_active_sessions_last_heartbeat ON active_sessions(last_heartbeat);

CREATE INDEX idx_active_sessions_session_id ON active_sessions(session_id);

CREATE INDEX idx_active_sessions_websocket_id ON active_sessions(websocket_id);

CREATE INDEX idx_auth_tokens_expires_at ON auth_tokens(expires_at);

CREATE INDEX idx_auth_tokens_token ON auth_tokens(token);

CREATE INDEX idx_auth_tokens_user_id ON auth_tokens(user_id);

CREATE UNIQUE INDEX idx_deliveries_ball_number
    ON deliveries(session_id, ball_number);

CREATE INDEX idx_deliveries_outcome ON deliveries(outcome);

CREATE INDEX idx_deliveries_timestamp ON deliveries(timestamp);

CREATE INDEX idx_players_name ON players(name);

CREATE INDEX idx_players_user_id ON players(user_id);

CREATE INDEX idx_sessions_date ON sessions(date);

CREATE INDEX idx_sessions_is_completed ON sessions(is_completed);

CREATE INDEX idx_sessions_player_id ON sessions(player_id);

CREATE INDEX idx_users_email ON users(email);

CREATE TRIGGER active_sessions_updated_at
AFTER UPDATE ON active_sessions
BEGIN
    UPDATE active_sessions SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

CREATE TRIGGER auth_tokens_updated_at
AFTER UPDATE ON auth_tokens
BEGIN
    UPDATE auth_tokens SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), version = version + 1
    WHERE id = NEW.id;
END;

CREATE TRIGGER deliveries_updated_at
AFTER UPDATE ON deliveries
BEGIN
    UPDATE deliveries SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), version = version + 1
    WHERE id = NEW.id;
END;

CREATE TRIGGER players_updated_at
AFTER UPDATE ON players
BEGIN
    UPDATE players SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), version = version + 1
    WHERE id = NEW.id;
END;

CREATE TRIGGER sessions_updated_at
AFTER UPDATE ON sessions
BEGIN
    UPDATE sessions SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), version = version + 1
    WHERE id = NEW.id;
END;

CREATE TRIGGER users_updated_at
AFTER UPDATE ON users
BEGIN
    UPDATE users SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), version = version + 1
    WHERE id = NEW.id;
END;

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
    SUM(CASE WHEN d.outcome IS NOT NULL AND d.outcome NOT IN ('wd', 'nb') THEN 1 ELSE 0 END) as balls_faced,
    SUM(CASE WHEN d.outcome = '4' THEN 1 ELSE 0 END) as fours,
    SUM(CASE WHEN d.outcome = '6' THEN 1 ELSE 0 END) as sixes,
    SUM(CASE WHEN d.outcome IN ('wd', 'nb') THEN 1 ELSE 0 END) as extras,
    SUM(CASE WHEN d.outcome IN ('W', 'caught', 'bowled', 'lbw', 'run_out', 'stumped', 'hit_wicket') THEN 1 ELSE 0 END) as dismissals,
    CASE WHEN SUM(CASE WHEN d.outcome IS NOT NULL AND d.outcome NOT IN ('wd', 'nb') THEN 1 ELSE 0 END) > 0 THEN ROUND((CAST(COALESCE(SUM(d.runs), 0) AS REAL) / SUM(CASE WHEN d.outcome IS NOT NULL AND d.outcome NOT IN ('wd', 'nb') THEN 1 ELSE 0 END)) * 100, 2) ELSE 0 END as strike_rate,
    ROUND(AVG(d.exit_speed), 1) as avg_exit_speed,
    MAX(d.exit_speed) as max_exit_speed
FROM sessions s
JOIN players p ON s.player_id = p.id
LEFT JOIN deliveries d ON s.id = d.session_id
GROUP BY s.id;

