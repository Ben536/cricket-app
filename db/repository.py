"""
Cricket App Repository Layer

Data access layer with transaction support and optimistic locking.
All database operations go through this module - no raw SQL exposed to other modules.

Error codes used (from error_codes.md):
- E5001: DATABASE_ERROR - Generic database error
- E5002: VERSION_CONFLICT - Optimistic locking failure
- E5003: CONSTRAINT_VIOLATION - Foreign key or unique constraint
- E5004: RECORD_NOT_FOUND - Requested record does not exist
- E5005: DATABASE_LOCKED - SQLite database is locked
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Optional, TypeVar

# Type for generic entity
T = TypeVar('T')

# Default database path
DEFAULT_DB_PATH = Path(__file__).parent / "cricket.db"


# =============================================================================
# EXCEPTIONS
# =============================================================================

class RepositoryError(Exception):
    """Base exception for repository errors."""

    def __init__(self, message: str, code: str, details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class RecordNotFoundError(RepositoryError):
    """Record not found in database."""

    def __init__(self, entity: str, id_value: Any):
        super().__init__(
            message=f"{entity} not found: {id_value}",
            code="E5004",
            details={"entity": entity, "id": id_value}
        )


class VersionConflictError(RepositoryError):
    """Optimistic locking version conflict."""

    def __init__(self, entity: str, id_value: Any, expected: int, actual: int):
        super().__init__(
            message=f"Version conflict for {entity} {id_value}: expected {expected}, found {actual}",
            code="E5002",
            details={
                "entity": entity,
                "id": id_value,
                "expected_version": expected,
                "actual_version": actual
            }
        )


class ConstraintViolationError(RepositoryError):
    """Database constraint violation."""

    def __init__(self, message: str, constraint: Optional[str] = None):
        super().__init__(
            message=message,
            code="E5003",
            details={"constraint": constraint} if constraint else {}
        )


class DatabaseError(RepositoryError):
    """Generic database error."""

    def __init__(self, message: str, original_error: Optional[Exception] = None):
        super().__init__(
            message=message,
            code="E5001",
            details={"original_error": str(original_error)} if original_error else {}
        )


class DatabaseLockedError(RepositoryError):
    """Database is locked."""

    def __init__(self):
        super().__init__(
            message="Database is locked",
            code="E5005"
        )


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class User:
    """User entity."""
    id: int
    email: str
    password_hash: str
    display_name: str
    created_at: str
    updated_at: str
    version: int


@dataclass
class AuthToken:
    """Authentication token entity."""
    id: int
    user_id: int
    token: str
    expires_at: str
    device_info: Optional[str]
    created_at: str
    updated_at: str
    version: int


@dataclass
class Player:
    """Player profile entity."""
    id: int
    user_id: Optional[int]
    name: str
    batting_hand: str
    created_at: str
    updated_at: str
    version: int


@dataclass
class Session:
    """Practice session entity."""
    id: int
    player_id: int
    date: str
    field_config_json: Optional[str]
    boundary_distance: float
    difficulty: str
    notes: Optional[str]
    is_completed: bool
    created_at: str
    updated_at: str
    version: int


@dataclass
class ActiveSession:
    """Active WebSocket session entity."""
    id: int
    session_id: int
    websocket_id: str
    last_heartbeat: str
    client_ip: Optional[str]
    client_user_agent: Optional[str]
    created_at: str
    updated_at: str
    version: int


@dataclass
class Delivery:
    """Ball delivery entity."""
    id: int
    session_id: int
    ball_number: int
    timestamp: str
    bowling_speed: Optional[float]
    exit_speed: Optional[float]
    horizontal_angle: Optional[float]
    vertical_angle: Optional[float]
    landing_x: Optional[float]
    landing_y: Optional[float]
    projected_distance: Optional[float]
    max_height: Optional[float]
    radar_frames_captured: Optional[int]
    detection_confidence: Optional[float]
    outcome: str
    runs: int
    fielder_position: Optional[str]
    fielding_position: Optional[str]
    end_position: Optional[str]
    is_boundary: bool
    is_aerial: bool
    description: Optional[str]
    catch_analysis: Optional[str]
    fielding_time: Optional[float]
    collection_difficulty: Optional[float]
    alignment_score: Optional[float]
    priority_score: Optional[float]
    fielder_arrival_time: Optional[float]
    ball_arrival_time: Optional[float]
    is_manual_input: bool
    created_at: str
    updated_at: str
    version: int


# =============================================================================
# REPOSITORY CLASS
# =============================================================================

class Repository:
    """
    Data access layer for the cricket app database.

    All writes use optimistic locking (check version, increment on update).
    All operations use transactions.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._connection: Optional[sqlite3.Connection] = None

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with proper error handling."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            yield conn
        except sqlite3.IntegrityError as e:
            error_msg = str(e).lower()
            if "unique constraint" in error_msg:
                raise ConstraintViolationError(str(e), "unique")
            elif "foreign key" in error_msg:
                raise ConstraintViolationError(str(e), "foreign_key")
            else:
                raise ConstraintViolationError(str(e))
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                raise DatabaseLockedError()
            raise DatabaseError(str(e), e)
        except sqlite3.Error as e:
            raise DatabaseError(str(e), e)
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Execute operations within a transaction."""
        with self.connection() as conn:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # =========================================================================
    # USER OPERATIONS
    # =========================================================================

    def get_user(self, user_id: int) -> Optional[User]:
        """Get a user by ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (user_id,)
            )
            row = cursor.fetchone()
            return self._row_to_user(row) if row else None

    def get_user_by_email(self, email: str) -> Optional[User]:
        """Get a user by email address."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM users WHERE email = ?",
                (email,)
            )
            row = cursor.fetchone()
            return self._row_to_user(row) if row else None

    def create_user(self, email: str, password_hash: str, display_name: str) -> User:
        """Create a new user."""
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO users (email, password_hash, display_name)
                VALUES (?, ?, ?)
                """,
                (email, password_hash, display_name)
            )
            user_id = cursor.lastrowid

            # Fetch and return the created user
            cursor = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return self._row_to_user(row)

    def update_user(
        self,
        user_id: int,
        version: int,
        email: Optional[str] = None,
        password_hash: Optional[str] = None,
        display_name: Optional[str] = None
    ) -> User:
        """
        Update a user with optimistic locking.

        Args:
            user_id: The user ID
            version: Expected version (for optimistic locking)
            email: New email (optional)
            password_hash: New password hash (optional)
            display_name: New display name (optional)

        Returns:
            Updated user entity

        Raises:
            RecordNotFoundError: If user doesn't exist
            VersionConflictError: If version doesn't match
        """
        with self.transaction() as conn:
            # Check current version
            cursor = conn.execute(
                "SELECT version FROM users WHERE id = ?",
                (user_id,)
            )
            row = cursor.fetchone()

            if not row:
                raise RecordNotFoundError("User", user_id)

            current_version = row['version']
            if current_version != version:
                raise VersionConflictError("User", user_id, version, current_version)

            # Build update query dynamically
            updates = []
            params = []

            if email is not None:
                updates.append("email = ?")
                params.append(email)
            if password_hash is not None:
                updates.append("password_hash = ?")
                params.append(password_hash)
            if display_name is not None:
                updates.append("display_name = ?")
                params.append(display_name)

            if not updates:
                # Nothing to update, return current user
                cursor = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
                return self._row_to_user(cursor.fetchone())

            params.extend([user_id, version])

            # Update with version check (trigger will increment version)
            conn.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE id = ? AND version = ?",
                params
            )

            # Fetch and return updated user
            cursor = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            return self._row_to_user(cursor.fetchone())

    def delete_user(self, user_id: int) -> bool:
        """Delete a user. Returns True if deleted, False if not found."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM users WHERE id = ?",
                (user_id,)
            )
            return cursor.rowcount > 0

    def _row_to_user(self, row: sqlite3.Row) -> User:
        """Convert a database row to User entity."""
        return User(
            id=row['id'],
            email=row['email'],
            password_hash=row['password_hash'],
            display_name=row['display_name'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            version=row['version']
        )

    # =========================================================================
    # AUTH TOKEN OPERATIONS
    # =========================================================================

    def get_auth_token(self, token: str) -> Optional[AuthToken]:
        """Get an auth token by token value."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM auth_tokens WHERE token = ?",
                (token,)
            )
            row = cursor.fetchone()
            return self._row_to_auth_token(row) if row else None

    def get_auth_tokens_by_user(self, user_id: int) -> list[AuthToken]:
        """Get all auth tokens for a user."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM auth_tokens WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
            return [self._row_to_auth_token(row) for row in cursor.fetchall()]

    def create_auth_token(
        self,
        user_id: int,
        token: str,
        expires_at: str,
        device_info: Optional[str] = None
    ) -> AuthToken:
        """Create a new auth token."""
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO auth_tokens (user_id, token, expires_at, device_info)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, token, expires_at, device_info)
            )
            token_id = cursor.lastrowid

            cursor = conn.execute("SELECT * FROM auth_tokens WHERE id = ?", (token_id,))
            return self._row_to_auth_token(cursor.fetchone())

    def delete_auth_token(self, token: str) -> bool:
        """Delete an auth token. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM auth_tokens WHERE token = ?",
                (token,)
            )
            return cursor.rowcount > 0

    def delete_expired_tokens(self) -> int:
        """Delete all expired auth tokens. Returns count deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM auth_tokens WHERE expires_at < datetime('now')"
            )
            return cursor.rowcount

    def _row_to_auth_token(self, row: sqlite3.Row) -> AuthToken:
        """Convert a database row to AuthToken entity."""
        return AuthToken(
            id=row['id'],
            user_id=row['user_id'],
            token=row['token'],
            expires_at=row['expires_at'],
            device_info=row['device_info'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            version=row['version']
        )

    # =========================================================================
    # PLAYER (PROFILE) OPERATIONS
    # =========================================================================

    def get_profile(self, player_id: int) -> Optional[Player]:
        """Get a player profile by ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM players WHERE id = ?",
                (player_id,)
            )
            row = cursor.fetchone()
            return self._row_to_player(row) if row else None

    def get_profiles_by_user(self, user_id: int) -> list[Player]:
        """Get all profiles for a user."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM players WHERE user_id = ? ORDER BY name",
                (user_id,)
            )
            return [self._row_to_player(row) for row in cursor.fetchall()]

    def get_all_profiles(self) -> list[Player]:
        """Get all player profiles."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM players ORDER BY name"
            )
            return [self._row_to_player(row) for row in cursor.fetchall()]

    def create_profile(
        self,
        name: str,
        batting_hand: str,
        user_id: Optional[int] = None
    ) -> Player:
        """Create a new player profile."""
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO players (name, batting_hand, user_id)
                VALUES (?, ?, ?)
                """,
                (name, batting_hand, user_id)
            )
            player_id = cursor.lastrowid

            cursor = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,))
            return self._row_to_player(cursor.fetchone())

    def update_profile(
        self,
        player_id: int,
        version: int,
        name: Optional[str] = None,
        batting_hand: Optional[str] = None,
        user_id: Optional[int] = None
    ) -> Player:
        """Update a player profile with optimistic locking."""
        with self.transaction() as conn:
            # Check current version
            cursor = conn.execute(
                "SELECT version FROM players WHERE id = ?",
                (player_id,)
            )
            row = cursor.fetchone()

            if not row:
                raise RecordNotFoundError("Player", player_id)

            current_version = row['version']
            if current_version != version:
                raise VersionConflictError("Player", player_id, version, current_version)

            # Build update query
            updates = []
            params = []

            if name is not None:
                updates.append("name = ?")
                params.append(name)
            if batting_hand is not None:
                updates.append("batting_hand = ?")
                params.append(batting_hand)
            if user_id is not None:
                updates.append("user_id = ?")
                params.append(user_id)

            if not updates:
                cursor = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,))
                return self._row_to_player(cursor.fetchone())

            params.extend([player_id, version])

            conn.execute(
                f"UPDATE players SET {', '.join(updates)} WHERE id = ? AND version = ?",
                params
            )

            cursor = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,))
            return self._row_to_player(cursor.fetchone())

    def delete_profile(self, player_id: int) -> bool:
        """Delete a player profile. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM players WHERE id = ?",
                (player_id,)
            )
            return cursor.rowcount > 0

    def _row_to_player(self, row: sqlite3.Row) -> Player:
        """Convert a database row to Player entity."""
        return Player(
            id=row['id'],
            user_id=row['user_id'],
            name=row['name'],
            batting_hand=row['batting_hand'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            version=row['version']
        )

    # =========================================================================
    # SESSION OPERATIONS
    # =========================================================================

    def get_session(self, session_id: int) -> Optional[Session]:
        """Get a session by ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,)
            )
            row = cursor.fetchone()
            return self._row_to_session(row) if row else None

    def get_sessions_by_player(self, player_id: int) -> list[Session]:
        """Get all sessions for a player."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM sessions WHERE player_id = ? ORDER BY date DESC",
                (player_id,)
            )
            return [self._row_to_session(row) for row in cursor.fetchall()]

    def get_active_sessions(self) -> list[Session]:
        """Get all incomplete sessions."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM sessions WHERE is_completed = 0 ORDER BY date DESC"
            )
            return [self._row_to_session(row) for row in cursor.fetchall()]

    def create_session(
        self,
        player_id: int,
        date: str,
        field_config: Optional[list[dict]] = None,
        boundary_distance: float = 70.0,
        difficulty: str = "medium",
        notes: Optional[str] = None
    ) -> Session:
        """Create a new session."""
        field_config_json = json.dumps(field_config) if field_config else None

        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sessions
                    (player_id, date, field_config_json, boundary_distance, difficulty, notes, is_completed)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (player_id, date, field_config_json, boundary_distance, difficulty, notes)
            )
            session_id = cursor.lastrowid

            cursor = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            return self._row_to_session(cursor.fetchone())

    def update_session(
        self,
        session_id: int,
        version: int,
        field_config: Optional[list[dict]] = None,
        boundary_distance: Optional[float] = None,
        difficulty: Optional[str] = None,
        notes: Optional[str] = None,
        is_completed: Optional[bool] = None
    ) -> Session:
        """Update a session with optimistic locking."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "SELECT version FROM sessions WHERE id = ?",
                (session_id,)
            )
            row = cursor.fetchone()

            if not row:
                raise RecordNotFoundError("Session", session_id)

            current_version = row['version']
            if current_version != version:
                raise VersionConflictError("Session", session_id, version, current_version)

            updates = []
            params = []

            if field_config is not None:
                updates.append("field_config_json = ?")
                params.append(json.dumps(field_config))
            if boundary_distance is not None:
                updates.append("boundary_distance = ?")
                params.append(boundary_distance)
            if difficulty is not None:
                updates.append("difficulty = ?")
                params.append(difficulty)
            if notes is not None:
                updates.append("notes = ?")
                params.append(notes)
            if is_completed is not None:
                updates.append("is_completed = ?")
                params.append(1 if is_completed else 0)

            if not updates:
                cursor = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
                return self._row_to_session(cursor.fetchone())

            params.extend([session_id, version])

            conn.execute(
                f"UPDATE sessions SET {', '.join(updates)} WHERE id = ? AND version = ?",
                params
            )

            cursor = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            return self._row_to_session(cursor.fetchone())

    def complete_session(self, session_id: int, version: int) -> Session:
        """Mark a session as completed."""
        return self.update_session(session_id, version, is_completed=True)

    def delete_session(self, session_id: int) -> bool:
        """Delete a session. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE id = ?",
                (session_id,)
            )
            return cursor.rowcount > 0

    def _row_to_session(self, row: sqlite3.Row) -> Session:
        """Convert a database row to Session entity."""
        return Session(
            id=row['id'],
            player_id=row['player_id'],
            date=row['date'],
            field_config_json=row['field_config_json'],
            boundary_distance=row['boundary_distance'] or 70.0,
            difficulty=row['difficulty'] or 'medium',
            notes=row['notes'],
            is_completed=bool(row['is_completed']),
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            version=row['version']
        )

    # =========================================================================
    # ACTIVE SESSION OPERATIONS
    # =========================================================================

    def get_active_session(self, session_id: int) -> Optional[ActiveSession]:
        """Get an active session tracker by session ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM active_sessions WHERE session_id = ?",
                (session_id,)
            )
            row = cursor.fetchone()
            return self._row_to_active_session(row) if row else None

    def get_active_session_by_websocket(self, websocket_id: str) -> Optional[ActiveSession]:
        """Get an active session by WebSocket ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM active_sessions WHERE websocket_id = ?",
                (websocket_id,)
            )
            row = cursor.fetchone()
            return self._row_to_active_session(row) if row else None

    def create_active_session(
        self,
        session_id: int,
        websocket_id: str,
        client_ip: Optional[str] = None,
        client_user_agent: Optional[str] = None
    ) -> ActiveSession:
        """Create an active session tracker."""
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO active_sessions
                    (session_id, websocket_id, client_ip, client_user_agent)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, websocket_id, client_ip, client_user_agent)
            )
            active_id = cursor.lastrowid

            cursor = conn.execute("SELECT * FROM active_sessions WHERE id = ?", (active_id,))
            return self._row_to_active_session(cursor.fetchone())

    def update_heartbeat(self, session_id: int) -> bool:
        """Update the last heartbeat time for an active session."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE active_sessions SET last_heartbeat = datetime('now') WHERE session_id = ?",
                (session_id,)
            )
            return cursor.rowcount > 0

    def delete_active_session(self, session_id: int) -> bool:
        """Delete an active session tracker."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM active_sessions WHERE session_id = ?",
                (session_id,)
            )
            return cursor.rowcount > 0

    def delete_stale_active_sessions(self, timeout_seconds: int = 60) -> int:
        """Delete active sessions that haven't had a heartbeat in timeout_seconds."""
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                DELETE FROM active_sessions
                WHERE last_heartbeat < datetime('now', ? || ' seconds')
                """,
                (f"-{timeout_seconds}",)
            )
            return cursor.rowcount

    def _row_to_active_session(self, row: sqlite3.Row) -> ActiveSession:
        """Convert a database row to ActiveSession entity."""
        return ActiveSession(
            id=row['id'],
            session_id=row['session_id'],
            websocket_id=row['websocket_id'],
            last_heartbeat=row['last_heartbeat'],
            client_ip=row['client_ip'],
            client_user_agent=row['client_user_agent'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            version=row['version']
        )

    # =========================================================================
    # DELIVERY OPERATIONS
    # =========================================================================

    def get_delivery(self, delivery_id: int) -> Optional[Delivery]:
        """Get a delivery by ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM deliveries WHERE id = ?",
                (delivery_id,)
            )
            row = cursor.fetchone()
            return self._row_to_delivery(row) if row else None

    def get_deliveries_by_session(self, session_id: int) -> list[Delivery]:
        """Get all deliveries for a session."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM deliveries WHERE session_id = ? ORDER BY ball_number",
                (session_id,)
            )
            return [self._row_to_delivery(row) for row in cursor.fetchall()]

    def get_last_delivery(self, session_id: int) -> Optional[Delivery]:
        """Get the last delivery in a session."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM deliveries WHERE session_id = ? ORDER BY ball_number DESC LIMIT 1",
                (session_id,)
            )
            row = cursor.fetchone()
            return self._row_to_delivery(row) if row else None

    def create_delivery(
        self,
        session_id: int,
        ball_number: int,
        timestamp: str,
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
        radar_frames_captured: Optional[int] = None,
        detection_confidence: Optional[float] = None,
        fielder_position: Optional[dict] = None,
        fielding_position: Optional[dict] = None,
        end_position: Optional[dict] = None,
        is_boundary: bool = False,
        is_aerial: bool = False,
        description: Optional[str] = None,
        catch_analysis: Optional[dict] = None,
        fielding_time: Optional[float] = None,
        collection_difficulty: Optional[float] = None,
        alignment_score: Optional[float] = None,
        priority_score: Optional[float] = None,
        fielder_arrival_time: Optional[float] = None,
        ball_arrival_time: Optional[float] = None,
        is_manual_input: bool = False
    ) -> Delivery:
        """Create a new delivery record."""
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO deliveries (
                    session_id, ball_number, timestamp, outcome, runs,
                    bowling_speed, exit_speed, horizontal_angle, vertical_angle,
                    landing_x, landing_y, projected_distance, max_height,
                    radar_frames_captured, detection_confidence,
                    fielder_position, fielding_position, end_position,
                    is_boundary, is_aerial, description, catch_analysis,
                    fielding_time, collection_difficulty, alignment_score,
                    priority_score, fielder_arrival_time, ball_arrival_time,
                    is_manual_input
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id, ball_number, timestamp, outcome, runs,
                    bowling_speed, exit_speed, horizontal_angle, vertical_angle,
                    landing_x, landing_y, projected_distance, max_height,
                    radar_frames_captured, detection_confidence,
                    json.dumps(fielder_position) if fielder_position else None,
                    json.dumps(fielding_position) if fielding_position else None,
                    json.dumps(end_position) if end_position else None,
                    1 if is_boundary else 0,
                    1 if is_aerial else 0,
                    description,
                    json.dumps(catch_analysis) if catch_analysis else None,
                    fielding_time, collection_difficulty, alignment_score,
                    priority_score, fielder_arrival_time, ball_arrival_time,
                    1 if is_manual_input else 0
                )
            )
            delivery_id = cursor.lastrowid

            cursor = conn.execute("SELECT * FROM deliveries WHERE id = ?", (delivery_id,))
            return self._row_to_delivery(cursor.fetchone())

    def delete_delivery(self, delivery_id: int) -> bool:
        """Delete a delivery. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM deliveries WHERE id = ?",
                (delivery_id,)
            )
            return cursor.rowcount > 0

    def delete_last_delivery(self, session_id: int) -> bool:
        """Delete the last delivery in a session (for undo). Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                DELETE FROM deliveries
                WHERE id = (
                    SELECT id FROM deliveries
                    WHERE session_id = ?
                    ORDER BY ball_number DESC
                    LIMIT 1
                )
                """,
                (session_id,)
            )
            return cursor.rowcount > 0

    def _row_to_delivery(self, row: sqlite3.Row) -> Delivery:
        """Convert a database row to Delivery entity."""
        return Delivery(
            id=row['id'],
            session_id=row['session_id'],
            ball_number=row['ball_number'],
            timestamp=row['timestamp'],
            bowling_speed=row['bowling_speed'],
            exit_speed=row['exit_speed'],
            horizontal_angle=row['horizontal_angle'],
            vertical_angle=row['vertical_angle'],
            landing_x=row['landing_x'],
            landing_y=row['landing_y'],
            projected_distance=row['projected_distance'],
            max_height=row['max_height'],
            radar_frames_captured=row['radar_frames_captured'],
            detection_confidence=row['detection_confidence'],
            outcome=row['outcome'],
            runs=row['runs'],
            fielder_position=row['fielder_position'],
            fielding_position=row['fielding_position'],
            end_position=row['end_position'],
            is_boundary=bool(row['is_boundary']),
            is_aerial=bool(row['is_aerial']),
            description=row['description'],
            catch_analysis=row['catch_analysis'],
            fielding_time=row['fielding_time'],
            collection_difficulty=row['collection_difficulty'],
            alignment_score=row['alignment_score'],
            priority_score=row['priority_score'],
            fielder_arrival_time=row['fielder_arrival_time'],
            ball_arrival_time=row['ball_arrival_time'],
            is_manual_input=bool(row['is_manual_input']),
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            version=row['version']
        )

    # =========================================================================
    # SESSION SUMMARY (VIEW) OPERATIONS
    # =========================================================================

    def get_session_summary(self, session_id: int) -> Optional[dict]:
        """Get computed summary stats for a session."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM session_summaries WHERE session_id = ?",
                (session_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_session_summaries_by_player(self, player_id: int) -> list[dict]:
        """Get all session summaries for a player."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM session_summaries WHERE player_id = ? ORDER BY date DESC",
                (player_id,)
            )
            return [dict(row) for row in cursor.fetchall()]
