-- Migration 003: Extend delivery outcome vocabulary (wickets + extras)
-- Version: 1.0.0
-- Description:
--   The original deliveries.outcome CHECK only allowed
--   ('dot','1','2','3','4','6','caught','dropped','misfield'), so manually
--   entered wickets ('W'), wides ('wd') and no-balls ('nb') were REJECTED by
--   the database and could never be recorded. This migration extends the
--   allowed outcomes and updates session_summaries to (a) count all dismissal
--   types and (b) exclude extras (wd/nb) from balls faced / strike rate.
--
--   SQLite cannot ALTER a CHECK constraint in place, so the deliveries table is
--   rebuilt. No other table references deliveries via foreign key (deliveries
--   only references sessions), so the rebuild is safe with foreign_keys = ON.
--   Idempotency is provided by the _migrations tracking table in migrate.py.

PRAGMA foreign_keys = ON;

-- 1. New deliveries table with extended outcome CHECK
CREATE TABLE deliveries_new (
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

-- 2. Copy existing rows verbatim (outcomes all remain valid under the new CHECK)
INSERT INTO deliveries_new (id, session_id, ball_number, timestamp, bowling_speed, exit_speed, horizontal_angle, vertical_angle, landing_x, landing_y, projected_distance, max_height, radar_frames_captured, detection_confidence, outcome, runs, fielder_position, fielding_position, end_position, is_boundary, is_aerial, description, catch_analysis, fielding_time, collection_difficulty, alignment_score, priority_score, fielder_arrival_time, ball_arrival_time, is_manual_input, created_at, updated_at, version) SELECT id, session_id, ball_number, timestamp, bowling_speed, exit_speed, horizontal_angle, vertical_angle, landing_x, landing_y, projected_distance, max_height, radar_frames_captured, detection_confidence, outcome, runs, fielder_position, fielding_position, end_position, is_boundary, is_aerial, description, catch_analysis, fielding_time, collection_difficulty, alignment_score, priority_score, fielder_arrival_time, ball_arrival_time, is_manual_input, created_at, updated_at, version FROM deliveries;

-- 3. Swap tables
DROP TABLE deliveries;
ALTER TABLE deliveries_new RENAME TO deliveries;

-- 4. Recreate indexes
CREATE INDEX IF NOT EXISTS idx_deliveries_session_id ON deliveries(session_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_ball_number ON deliveries(session_id, ball_number);
CREATE INDEX IF NOT EXISTS idx_deliveries_outcome ON deliveries(outcome);
CREATE INDEX IF NOT EXISTS idx_deliveries_timestamp ON deliveries(timestamp);

-- 5. Recreate updated_at / version trigger
DROP TRIGGER IF EXISTS deliveries_updated_at;
CREATE TRIGGER deliveries_updated_at
AFTER UPDATE ON deliveries
BEGIN
    UPDATE deliveries SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;

-- 6. Update session_summaries: count all dismissals, exclude extras from balls faced
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
