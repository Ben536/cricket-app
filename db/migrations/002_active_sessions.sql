-- Migration 002: Active Sessions Table
-- Version: 1.0.0
-- Description: Create active_sessions table for tracking currently live WebSocket sessions
-- IDEMPOTENT: Safe to run multiple times

-- Enable foreign keys
PRAGMA foreign_keys = ON;

-- =============================================================================
-- ACTIVE_SESSIONS TABLE
-- Tracks which sessions are currently live (for WebSocket reconnection)
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
-- TRIGGER FOR updated_at
-- =============================================================================
DROP TRIGGER IF EXISTS active_sessions_updated_at;

CREATE TRIGGER active_sessions_updated_at
AFTER UPDATE ON active_sessions
BEGIN
    UPDATE active_sessions SET updated_at = datetime('now'), version = version + 1
    WHERE id = NEW.id;
END;
