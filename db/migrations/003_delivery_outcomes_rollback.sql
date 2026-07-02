-- Rollback for Migration 003: restore original delivery outcome CHECK and view.
--
-- WARNING: This re-narrows the outcome CHECK to the original set. If any rows
-- now use 'W', 'wd', 'nb' or other newly-added outcomes, the rebuild INSERT
-- will fail. Remove or remap those rows before rolling back.
--
-- Dependents (view + trigger) are dropped before the table swap to avoid
-- dangling-reference errors on modern SQLite. Executed via sqlite3
-- executescript (not the line splitter), so plain SQL.

PRAGMA foreign_keys = OFF;

DROP VIEW IF EXISTS session_summaries;
DROP TRIGGER IF EXISTS deliveries_updated_at;

CREATE TABLE deliveries_old (
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

INSERT INTO deliveries_old SELECT * FROM deliveries;

DROP TABLE deliveries;
ALTER TABLE deliveries_old RENAME TO deliveries;

CREATE INDEX IF NOT EXISTS idx_deliveries_session_id ON deliveries(session_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_ball_number ON deliveries(session_id, ball_number);
CREATE INDEX IF NOT EXISTS idx_deliveries_outcome ON deliveries(outcome);
CREATE INDEX IF NOT EXISTS idx_deliveries_timestamp ON deliveries(timestamp);

CREATE TRIGGER deliveries_updated_at
AFTER UPDATE ON deliveries
BEGIN
    UPDATE deliveries SET updated_at = datetime('now'), version = version + 1
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

PRAGMA foreign_keys = ON;
