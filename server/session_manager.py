"""
Session Manager for Cricket App WebSocket Server

Tracks in-memory session state including current session, active profile,
field configuration, and difficulty. Bridges WebSocket handlers with the
database repository and game engine.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ActiveSessionState:
    """
    In-memory state for an active session.

    This is separate from the database Session entity - it tracks
    ephemeral state like the current field config and connected clients.
    """
    session_id: int
    player_id: int
    websocket_id: str  # The client that owns this session

    # Session configuration
    difficulty: str = "medium"
    field_config: list[dict] = field(default_factory=list)
    boundary_distance: float = 70.0

    # Session stats (in-memory mirror of DB)
    runs: int = 0
    balls: int = 0
    fours: int = 0
    sixes: int = 0
    wickets: int = 0

    # Overs tracking
    current_over: list[str] = field(default_factory=list)  # Ball results in current over
    overs: list[dict] = field(default_factory=list)  # Completed overs

    # Timestamps
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_out(self) -> bool:
        """Whether the batter is out."""
        return self.wickets > 0

    @property
    def strike_rate(self) -> float:
        """Calculate current strike rate."""
        if self.balls == 0:
            return 0.0
        return (self.runs / self.balls) * 100

    def add_delivery(self, result: str, runs: int, is_boundary: bool) -> None:
        """
        Add a delivery to the session.

        Args:
            result: The ball result (dot, 1, 2, 3, 4, 6, W, etc.)
            runs: Runs scored
            is_boundary: Whether it was a boundary
        """
        # Wides and no-balls are extras: they score runs but do not count as a
        # legal ball faced, nor as a ball in the over.
        is_extra = result in ("wd", "nb")

        if not is_extra:
            self.balls += 1
        self.runs += runs

        if is_boundary:
            if runs == 4:
                self.fours += 1
            elif runs == 6:
                self.sixes += 1

        if result == "W":
            self.wickets += 1

        if is_extra:
            return

        # Track legal balls in current over
        self.current_over.append(result)

        # Complete over if 6 legal balls
        if len(self.current_over) >= 6:
            over_runs = sum(
                int(r) if r.isdigit() else 0
                for r in self.current_over
            )
            self.overs.append({
                "balls": self.current_over.copy(),
                "runs": over_runs
            })
            self.current_over = []

    def undo_delivery(self, result: str, runs: int, is_boundary: bool) -> None:
        """
        Undo the last delivery.

        Args:
            result: The ball result that was undone
            runs: Runs that were scored
            is_boundary: Whether it was a boundary
        """
        # Mirror add_delivery: extras did not consume a legal ball.
        is_extra = result in ("wd", "nb")

        if not is_extra and self.balls <= 0:
            return

        if not is_extra:
            self.balls -= 1
        self.runs -= runs

        if is_boundary:
            if runs == 4:
                self.fours = max(0, self.fours - 1)
            elif runs == 6:
                self.sixes = max(0, self.sixes - 1)

        if result == "W":
            self.wickets = max(0, self.wickets - 1)

        if is_extra:
            return

        # Remove from current over
        if self.current_over:
            self.current_over.pop()
        elif self.overs:
            # Restore previous over
            last_over = self.overs.pop()
            self.current_over = last_over["balls"]
            self.current_over.pop()  # Remove the last ball

    def to_session_data(self) -> dict:
        """Convert to SessionData format for API responses."""
        return {
            "id": str(self.session_id),
            "player_id": str(self.player_id),
            "date": self.started_at.isoformat().replace("+00:00", "Z"),
            "runs": self.runs,
            "balls": self.balls,
            "fours": self.fours,
            "sixes": self.sixes,
            "wickets": self.wickets,
            "is_out": self.is_out,
            "overs": self.overs,
            "strike_rate": self.strike_rate,
            "field_config": self.field_config,
        }


class SessionManager:
    """
    Manages in-memory session state.

    Tracks:
    - Active sessions (session_id -> ActiveSessionState)
    - Client to session mapping (websocket_id -> session_id)
    - Active profile per client (websocket_id -> profile_id)
    """

    def __init__(self) -> None:
        # Active sessions: session_id -> state
        self._sessions: dict[int, ActiveSessionState] = {}

        # Client session mapping: websocket_id -> session_id
        self._client_sessions: dict[str, int] = {}

        # Active profile per client: websocket_id -> profile_id
        self._active_profiles: dict[str, int] = {}

        # Default field config (standard field positions)
        self._default_field_config: list[dict] = [
            {"x": 0, "y": 30, "name": "mid-off"},
            {"x": -20, "y": 25, "name": "cover"},
            {"x": -30, "y": 15, "name": "point"},
            {"x": 30, "y": 15, "name": "square-leg"},
            {"x": 20, "y": 25, "name": "midwicket"},
            {"x": 0, "y": 60, "name": "long-off"},
            {"x": -40, "y": 50, "name": "deep-cover"},
            {"x": 40, "y": 50, "name": "deep-midwicket"},
            {"x": -35, "y": -10, "name": "third-man"},
            {"x": 35, "y": -10, "name": "fine-leg"},
            {"x": 0, "y": -40, "name": "long-stop"},
        ]

        # Global default difficulty
        self._default_difficulty = "medium"

        logger.info("SessionManager initialized")

    @property
    def default_field_config(self) -> list[dict]:
        """Get the default field configuration."""
        return self._default_field_config.copy()

    @property
    def default_difficulty(self) -> str:
        """Get the default difficulty."""
        return self._default_difficulty

    def get_active_session(self, websocket_id: str) -> Optional[ActiveSessionState]:
        """Get the active session for a client."""
        session_id = self._client_sessions.get(websocket_id)
        if session_id is None:
            return None
        return self._sessions.get(session_id)

    def get_session_by_id(self, session_id: int) -> Optional[ActiveSessionState]:
        """Get a session by its ID."""
        return self._sessions.get(session_id)

    def has_active_session(self, websocket_id: str) -> bool:
        """Check if a client has an active session."""
        return websocket_id in self._client_sessions

    def create_session(
        self,
        session_id: int,
        player_id: int,
        websocket_id: str,
        field_config: Optional[list[dict]] = None,
        difficulty: Optional[str] = None,
        boundary_distance: float = 70.0,
    ) -> ActiveSessionState:
        """
        Create a new active session.

        Args:
            session_id: The database session ID
            player_id: The player profile ID
            websocket_id: The client's WebSocket ID
            field_config: Optional field configuration
            difficulty: Optional difficulty level
            boundary_distance: Boundary distance in metres

        Returns:
            The created ActiveSessionState
        """
        state = ActiveSessionState(
            session_id=session_id,
            player_id=player_id,
            websocket_id=websocket_id,
            difficulty=difficulty or self._default_difficulty,
            field_config=field_config or self._default_field_config.copy(),
            boundary_distance=boundary_distance,
        )

        self._sessions[session_id] = state
        self._client_sessions[websocket_id] = session_id

        logger.info(
            f"Created session {session_id} for client {websocket_id}, "
            f"player={player_id}, difficulty={state.difficulty}"
        )

        return state

    def end_session(self, session_id: int) -> Optional[ActiveSessionState]:
        """
        End an active session.

        Args:
            session_id: The session to end

        Returns:
            The ended session state, or None if not found
        """
        state = self._sessions.pop(session_id, None)
        if state:
            self._client_sessions.pop(state.websocket_id, None)
            logger.info(f"Ended session {session_id}")
        return state

    def end_session_by_client(self, websocket_id: str) -> Optional[ActiveSessionState]:
        """
        End the active session for a client.

        Args:
            websocket_id: The client's WebSocket ID

        Returns:
            The ended session state, or None if no active session
        """
        session_id = self._client_sessions.get(websocket_id)
        if session_id is None:
            return None
        return self.end_session(session_id)

    def update_field_config(
        self,
        session_id: int,
        field_config: list[dict],
        boundary_distance: Optional[float] = None,
    ) -> bool:
        """
        Update field configuration for a session.

        Args:
            session_id: The session to update
            field_config: New field configuration
            boundary_distance: Optional new boundary distance

        Returns:
            True if updated, False if session not found
        """
        state = self._sessions.get(session_id)
        if not state:
            return False

        state.field_config = field_config
        if boundary_distance is not None:
            state.boundary_distance = boundary_distance

        logger.debug(f"Updated field config for session {session_id}")
        return True

    def update_difficulty(self, session_id: int, difficulty: str) -> bool:
        """
        Update difficulty for a session.

        Args:
            session_id: The session to update
            difficulty: New difficulty level

        Returns:
            True if updated, False if session not found
        """
        state = self._sessions.get(session_id)
        if not state:
            return False

        state.difficulty = difficulty
        logger.debug(f"Updated difficulty to {difficulty} for session {session_id}")
        return True

    def get_active_profile(self, websocket_id: str) -> Optional[int]:
        """Get the active profile ID for a client."""
        return self._active_profiles.get(websocket_id)

    def set_active_profile(self, websocket_id: str, profile_id: int) -> None:
        """Set the active profile for a client."""
        self._active_profiles[websocket_id] = profile_id
        logger.debug(f"Set active profile {profile_id} for client {websocket_id}")

    def clear_active_profile(self, websocket_id: str) -> None:
        """Clear the active profile for a client."""
        self._active_profiles.pop(websocket_id, None)

    def cleanup_client(self, websocket_id: str) -> None:
        """
        Clean up all state for a disconnecting client.

        Args:
            websocket_id: The client's WebSocket ID
        """
        # End any active session
        self.end_session_by_client(websocket_id)

        # Clear active profile
        self.clear_active_profile(websocket_id)

        logger.debug(f"Cleaned up state for client {websocket_id}")

    def get_clients_in_session(self, session_id: int) -> list[str]:
        """
        Get all client IDs that are in a session.

        Note: Currently we only support one client per session,
        but this is designed to support spectators in the future.
        """
        clients = []
        for websocket_id, sid in self._client_sessions.items():
            if sid == session_id:
                clients.append(websocket_id)
        return clients
