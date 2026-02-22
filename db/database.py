"""
Cricket App Database Module

SQLite database for storing players, sessions, and delivery data.
Provides CRUD operations and query functions for UI visualisations.
"""

import sqlite3
import json
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

# Default database path
DEFAULT_DB_PATH = Path(__file__).parent / "cricket.db"


@contextmanager
def get_connection(db_path: Path = DEFAULT_DB_PATH):
    """Context manager for database connections with foreign keys enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Return rows as dict-like objects
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """
    Initialise the database and create tables if they don't exist.

    Creates three tables:
    - players: Player profiles with batting hand preference
    - sessions: Practice sessions linked to players
    - deliveries: Individual ball data with tracking metrics
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        # Players table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                batting_hand TEXT CHECK(batting_hand IN ('right', 'left'))
            )
        """)

        # Sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                field_config_json TEXT,
                notes TEXT,
                FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
            )
        """)

        # Deliveries table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                ball_number INTEGER NOT NULL,
                bowling_speed REAL,
                exit_speed REAL,
                horizontal_angle REAL,
                vertical_angle REAL,
                landing_x REAL,
                landing_y REAL,
                projected_distance REAL,
                max_height REAL,
                outcome TEXT CHECK(outcome IN ('dot', '1', '2', '3', '4', '6', 'caught', 'dropped', 'misfield')),
                runs INTEGER,
                fielder_position TEXT,
                is_boundary INTEGER CHECK(is_boundary IN (0, 1)),
                is_aerial INTEGER CHECK(is_aerial IN (0, 1)),
                radar_frames_captured INTEGER,
                detection_confidence REAL CHECK(detection_confidence >= 0 AND detection_confidence <= 1),
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)

        conn.commit()


# =============================================================================
# Player CRUD Operations
# =============================================================================

def create_player(name: str, batting_hand: str, db_path: Path = DEFAULT_DB_PATH) -> int:
    """
    Create a new player.

    Args:
        name: Player's name
        batting_hand: 'right' or 'left'

    Returns:
        The new player's ID
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO players (name, batting_hand) VALUES (?, ?)",
            (name, batting_hand)
        )
        conn.commit()
        return cursor.lastrowid


def get_all_players(db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """Get all players."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM players ORDER BY name")
        return [dict(row) for row in cursor.fetchall()]


def get_player_by_id(player_id: int, db_path: Path = DEFAULT_DB_PATH) -> Optional[dict]:
    """Get a player by ID."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM players WHERE id = ?", (player_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


# =============================================================================
# Session CRUD Operations
# =============================================================================

def create_session(
    player_id: int,
    date: str,
    field_config: Optional[dict] = None,
    notes: Optional[str] = None,
    db_path: Path = DEFAULT_DB_PATH
) -> int:
    """
    Create a new session.

    Args:
        player_id: ID of the player
        date: ISO format date string (YYYY-MM-DD)
        field_config: Optional dict of fielder positions
        notes: Optional session notes

    Returns:
        The new session's ID
    """
    field_config_json = json.dumps(field_config) if field_config else None

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO sessions (player_id, date, field_config_json, notes) VALUES (?, ?, ?, ?)",
            (player_id, date, field_config_json, notes)
        )
        conn.commit()
        return cursor.lastrowid


def get_all_sessions(db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """Get all sessions with player names."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.*, p.name as player_name
            FROM sessions s
            JOIN players p ON s.player_id = p.id
            ORDER BY s.date DESC
        """)
        return [dict(row) for row in cursor.fetchall()]


def get_sessions_by_player(player_id: int, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """Get all sessions for a specific player."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM sessions WHERE player_id = ? ORDER BY date DESC",
            (player_id,)
        )
        return [dict(row) for row in cursor.fetchall()]


def get_session_by_id(session_id: int, db_path: Path = DEFAULT_DB_PATH) -> Optional[dict]:
    """Get a session by ID."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


# =============================================================================
# Delivery Operations
# =============================================================================

def insert_delivery(
    session_id: int,
    timestamp: str,
    ball_number: int,
    outcome: str,
    runs: int,
    bowling_speed: Optional[float] = None,
    exit_speed: Optional[float] = None,
    horizontal_angle: Optional[float] = None,
    vertical_angle: Optional[float] = None,
    landing_x: Optional[float] = None,
    landing_y: Optional[float] = None,
    projected_distance: Optional[float] = None,
    max_height: Optional[float] = None,
    fielder_position: Optional[str] = None,
    is_boundary: bool = False,
    is_aerial: bool = False,
    radar_frames_captured: Optional[int] = None,
    detection_confidence: Optional[float] = None,
    db_path: Path = DEFAULT_DB_PATH
) -> int:
    """
    Insert a new delivery record.

    Args:
        session_id: ID of the session
        timestamp: ISO format datetime string
        ball_number: Sequential ball number within session
        outcome: One of: dot, 1, 2, 3, 4, 6, caught, dropped, misfield
        runs: Numeric runs scored
        bowling_speed: Delivery speed in km/h (nullable)
        exit_speed: Ball speed off bat in km/h
        horizontal_angle: Degrees from straight (- leg side, + off side)
        vertical_angle: Launch angle above horizontal
        landing_x: X coordinate on field in metres from stumps
        landing_y: Y coordinate on field in metres from stumps
        projected_distance: Total projected distance in metres
        max_height: Peak height of trajectory in metres
        fielder_position: e.g. 'mid-off', 'deep square leg'
        is_boundary: Whether the shot reached the boundary
        is_aerial: Whether the shot was in the air
        radar_frames_captured: Number of radar frames detected
        detection_confidence: 0-1 confidence score

    Returns:
        The new delivery's ID
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO deliveries (
                session_id, timestamp, ball_number, bowling_speed, exit_speed,
                horizontal_angle, vertical_angle, landing_x, landing_y,
                projected_distance, max_height, outcome, runs, fielder_position,
                is_boundary, is_aerial, radar_frames_captured, detection_confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id, timestamp, ball_number, bowling_speed, exit_speed,
            horizontal_angle, vertical_angle, landing_x, landing_y,
            projected_distance, max_height, outcome, runs, fielder_position,
            1 if is_boundary else 0, 1 if is_aerial else 0,
            radar_frames_captured, detection_confidence
        ))
        conn.commit()
        return cursor.lastrowid


# =============================================================================
# Query Functions for UI Visualisations
# =============================================================================

def get_deliveries_for_session(session_id: int, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """
    Get all deliveries for a session (for wagon wheel visualisation).

    Returns deliveries ordered by ball number with all tracking data.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM deliveries WHERE session_id = ? ORDER BY ball_number",
            (session_id,)
        )
        return [dict(row) for row in cursor.fetchall()]


def get_session_summary_stats(session_id: int, db_path: Path = DEFAULT_DB_PATH) -> dict:
    """
    Get summary statistics for a session.

    Returns:
        Dict with total_runs, balls_faced, strike_rate, fours, sixes, dismissals
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                COALESCE(SUM(runs), 0) as total_runs,
                COUNT(*) as balls_faced,
                SUM(CASE WHEN outcome = '4' THEN 1 ELSE 0 END) as fours,
                SUM(CASE WHEN outcome = '6' THEN 1 ELSE 0 END) as sixes,
                SUM(CASE WHEN outcome = 'caught' THEN 1 ELSE 0 END) as dismissals
            FROM deliveries
            WHERE session_id = ?
        """, (session_id,))

        row = cursor.fetchone()
        stats = dict(row)

        # Calculate strike rate
        balls = stats['balls_faced'] or 0
        runs = stats['total_runs'] or 0
        stats['strike_rate'] = round((runs / balls * 100), 2) if balls > 0 else 0.0

        return stats


def get_scoring_breakdown_by_zone(session_id: int, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """
    Get scoring breakdown by zone (bucketed by horizontal angle).

    Zones (for right-hander, looking from bowler's end):
    - fine_leg: -90 to -60 degrees
    - square_leg: -60 to -30 degrees
    - midwicket: -30 to -10 degrees
    - straight: -10 to 10 degrees
    - cover: 10 to 40 degrees
    - point: 40 to 70 degrees
    - third_man: 70 to 90 degrees

    Returns:
        List of dicts with zone, total_runs, shot_count, boundaries
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                CASE
                    WHEN horizontal_angle < -60 THEN 'fine_leg'
                    WHEN horizontal_angle < -30 THEN 'square_leg'
                    WHEN horizontal_angle < -10 THEN 'midwicket'
                    WHEN horizontal_angle <= 10 THEN 'straight'
                    WHEN horizontal_angle <= 40 THEN 'cover'
                    WHEN horizontal_angle <= 70 THEN 'point'
                    ELSE 'third_man'
                END as zone,
                COALESCE(SUM(runs), 0) as total_runs,
                COUNT(*) as shot_count,
                SUM(CASE WHEN is_boundary = 1 THEN 1 ELSE 0 END) as boundaries
            FROM deliveries
            WHERE session_id = ? AND horizontal_angle IS NOT NULL
            GROUP BY zone
            ORDER BY
                CASE zone
                    WHEN 'fine_leg' THEN 1
                    WHEN 'square_leg' THEN 2
                    WHEN 'midwicket' THEN 3
                    WHEN 'straight' THEN 4
                    WHEN 'cover' THEN 5
                    WHEN 'point' THEN 6
                    WHEN 'third_man' THEN 7
                END
        """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]


def get_deliveries_by_over(session_id: int, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """
    Get deliveries grouped by over (every 6 balls) for manhattan chart.

    Returns:
        List of dicts with over_number, runs, balls, dots, boundaries
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                ((ball_number - 1) / 6) + 1 as over_number,
                COALESCE(SUM(runs), 0) as runs,
                COUNT(*) as balls,
                SUM(CASE WHEN outcome = 'dot' THEN 1 ELSE 0 END) as dots,
                SUM(CASE WHEN is_boundary = 1 THEN 1 ELSE 0 END) as boundaries
            FROM deliveries
            WHERE session_id = ?
            GROUP BY over_number
            ORDER BY over_number
        """, (session_id,))
        return [dict(row) for row in cursor.fetchall()]


def get_player_session_summaries(player_id: int, db_path: Path = DEFAULT_DB_PATH) -> list[dict]:
    """
    Get all session summaries for a player ordered by date (for progress tracking).

    Returns:
        List of dicts with session info and summary stats
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                s.id as session_id,
                s.date,
                s.notes,
                COALESCE(SUM(d.runs), 0) as total_runs,
                COUNT(d.id) as balls_faced,
                SUM(CASE WHEN d.outcome = '4' THEN 1 ELSE 0 END) as fours,
                SUM(CASE WHEN d.outcome = '6' THEN 1 ELSE 0 END) as sixes,
                SUM(CASE WHEN d.outcome = 'caught' THEN 1 ELSE 0 END) as dismissals,
                ROUND(AVG(d.exit_speed), 1) as avg_exit_speed,
                MAX(d.exit_speed) as max_exit_speed
            FROM sessions s
            LEFT JOIN deliveries d ON s.id = d.session_id
            WHERE s.player_id = ?
            GROUP BY s.id
            ORDER BY s.date DESC
        """, (player_id,))

        results = []
        for row in cursor.fetchall():
            stats = dict(row)
            balls = stats['balls_faced'] or 0
            runs = stats['total_runs'] or 0
            stats['strike_rate'] = round((runs / balls * 100), 2) if balls > 0 else 0.0
            results.append(stats)

        return results


def get_speed_statistics(session_id: int, db_path: Path = DEFAULT_DB_PATH) -> dict:
    """
    Get speed statistics for a session.

    Returns:
        Dict with avg_exit_speed, max_exit_speed, avg_bowling_speed, max_bowling_speed
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                ROUND(AVG(exit_speed), 1) as avg_exit_speed,
                MAX(exit_speed) as max_exit_speed,
                ROUND(AVG(bowling_speed), 1) as avg_bowling_speed,
                MAX(bowling_speed) as max_bowling_speed
            FROM deliveries
            WHERE session_id = ?
        """, (session_id,))

        row = cursor.fetchone()
        return dict(row) if row else {}


# =============================================================================
# Utility Functions
# =============================================================================

def delete_player(player_id: int, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Delete a player and all their sessions/deliveries (cascades)."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM players WHERE id = ?", (player_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_session(session_id: int, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Delete a session and all its deliveries (cascades)."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return cursor.rowcount > 0
