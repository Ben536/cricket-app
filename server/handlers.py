"""
WebSocket Message Handlers for Cricket App

Implements handlers for all client message types, bridging WebSocket
messages to the database repository and game engine.

Each handler:
1. Validates the request
2. Performs the operation (DB + game engine)
3. Returns appropriate response(s)
4. Broadcasts updates to session clients when needed
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

# Local imports
from server.session_manager import SessionManager, ActiveSessionState
from server.message_router import (
    ErrorCode,
    create_error_response,
    generate_message_id,
    create_timestamp,
    Difficulty,
    BallResult,
)

# Database repository
from db.repository import (
    Repository,
    RecordNotFoundError,
    RepositoryError,
)

# Game engine
from engine.game_engine import simulate_delivery

# Radar recorder and streamer
from radar.recorder import get_recorder, RadarRecorder, SESSION_TYPES
from radar.streamer import get_streamer, RadarStreamer

if TYPE_CHECKING:
    from server.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)


# =============================================================================
# ERROR HELPERS
# =============================================================================

# Extended error codes for database errors
ERROR_DATABASE = "E5001"
ERROR_VERSION_CONFLICT = "E5002"
ERROR_CONSTRAINT_VIOLATION = "E5003"
ERROR_RECORD_NOT_FOUND = "E5004"

# Extended error messages
EXTENDED_ERROR_MESSAGES = {
    ERROR_DATABASE: ("Database operation failed", True),
    ERROR_VERSION_CONFLICT: ("Version conflict, please refresh", True),
    ERROR_CONSTRAINT_VIOLATION: ("Database constraint violated", False),
    ERROR_RECORD_NOT_FOUND: ("Record not found", False),
}


def create_extended_error(
    code: str,
    in_reply_to: Optional[str] = None,
    details: Optional[dict] = None,
    message_override: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create an error response with extended error codes.

    Supports both ErrorCode enum and string codes.
    """
    if isinstance(code, ErrorCode):
        return create_error_response(code, in_reply_to, details)

    message, recoverable = EXTENDED_ERROR_MESSAGES.get(
        code, ("Unknown error", False)
    )
    if message_override:
        message = message_override

    return {
        "type": "error",
        "message_id": generate_message_id(),
        "timestamp": create_timestamp(),
        "in_reply_to": in_reply_to,
        "payload": {
            "code": code,
            "message": message,
            "details": details,
            "recoverable": recoverable,
        }
    }


# =============================================================================
# RESPONSE BUILDERS
# =============================================================================

def build_session_state_response(
    session_manager: SessionManager,
    repository: Repository,
    websocket_id: str,
    in_reply_to: Optional[str] = None,
) -> dict[str, Any]:
    """
    Build a session_state response message.

    Includes current session data, all profiles, and configuration.
    """
    # Get all profiles
    profiles = repository.get_all_profiles()
    profile_list = [
        {
            "id": str(p.id),
            "name": p.name,
            "batting_hand": p.batting_hand,
        }
        for p in profiles
    ]

    # Get active session
    active_session = session_manager.get_active_session(websocket_id)
    session_data = active_session.to_session_data() if active_session else None

    # Get active profile
    active_profile_id = session_manager.get_active_profile(websocket_id)

    # Get field config and difficulty (from session or defaults)
    if active_session:
        field_config = active_session.field_config
        difficulty = active_session.difficulty
        boundary_distance = active_session.boundary_distance
    else:
        field_config = session_manager.default_field_config
        difficulty = session_manager.default_difficulty
        boundary_distance = 70.0

    return {
        "type": "session_state",
        "message_id": generate_message_id(),
        "timestamp": create_timestamp(),
        "in_reply_to": in_reply_to,
        "payload": {
            "session": session_data,
            "profiles": profile_list,
            "active_profile_id": str(active_profile_id) if active_profile_id else None,
            "difficulty": difficulty,
            "field_config": field_config,
            "boundary_distance": boundary_distance,
        }
    }


def build_shot_result_response(
    session_id: int,
    ball_number: int,
    simulation_result: dict,
    radar_data: Optional[dict] = None,
    in_reply_to: Optional[str] = None,
) -> dict[str, Any]:
    """Build a shot_result response message with SlimShotResult.

    Full SimulationResult is stored in database.
    WebSocket only sends 5 fields for efficient real-time display.
    Use REST API GET /api/sessions/:id/deliveries for full data.
    """
    # Extract only SlimShotResult fields (5 fields) for WebSocket
    slim_result = {
        "outcome": simulation_result["outcome"],
        "runs": simulation_result["runs"],
        "description": simulation_result.get("description", ""),
        "end_position": simulation_result["end_position"],
        "fielder_involved": simulation_result.get("fielder_involved"),
    }
    return {
        "type": "shot_result",
        "message_id": generate_message_id(),
        "timestamp": create_timestamp(),
        "in_reply_to": in_reply_to,
        "payload": {
            "session_id": str(session_id),
            "ball_number": ball_number,
            "simulation": slim_result,  # SlimShotResult, not full SimulationResult
            "radar_data": radar_data,
        }
    }


def build_wagon_wheel_update(
    session_id: int,
    shot_id: str,
    end_x: float,
    end_y: float,
    outcome: str,
    distance: float,
    in_reply_to: Optional[str] = None,
) -> dict[str, Any]:
    """Build a wagon_wheel_update response message."""
    return {
        "type": "wagon_wheel_update",
        "message_id": generate_message_id(),
        "timestamp": create_timestamp(),
        "in_reply_to": in_reply_to,
        "payload": {
            "session_id": str(session_id),
            "shot": {
                "id": shot_id,
                "end_x": end_x,
                "end_y": end_y,
                "outcome": outcome,
                "distance": distance,
            }
        }
    }


# =============================================================================
# HANDLERS CLASS
# =============================================================================

class MessageHandlers:
    """
    Handler implementations for all client message types.

    Each handler is an async method that:
    - Takes client_id and parsed message
    - Returns a response dict or None
    - May broadcast to session clients via connection_manager
    """

    def __init__(
        self,
        repository: Repository,
        session_manager: SessionManager,
        connection_manager: "ConnectionManager",
    ):
        self.repository = repository
        self.session_manager = session_manager
        self.connection_manager = connection_manager

        logger.info("MessageHandlers initialized")

    # =========================================================================
    # PROFILE HANDLERS
    # =========================================================================

    async def handle_create_profile(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Create a new player profile.

        Creates profile in DB and returns session_state with updated profiles list.
        """
        payload = message["payload"]
        message_id = message["message_id"]

        name = payload["name"]
        batting_hand = payload["batting_hand"]

        logger.info(f"Creating profile: name={name}, hand={batting_hand}")

        try:
            # Create in database
            profile = self.repository.create_profile(
                name=name,
                batting_hand=batting_hand,
            )

            logger.info(f"Created profile {profile.id}: {profile.name}")

            # Set as active profile
            self.session_manager.set_active_profile(client_id, profile.id)

            # Return session state with new profile
            return build_session_state_response(
                self.session_manager,
                self.repository,
                client_id,
                in_reply_to=message_id,
            )

        except RepositoryError as e:
            logger.error(f"Failed to create profile: {e}")
            return create_extended_error(
                e.code,
                in_reply_to=message_id,
                details=e.details,
            )

    async def handle_select_profile(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Select a player profile.

        Loads profile from DB and sets as active for this client.
        """
        payload = message["payload"]
        message_id = message["message_id"]

        profile_id_str = payload["profile_id"]

        try:
            profile_id = int(profile_id_str)
        except ValueError:
            return create_error_response(
                ErrorCode.INVALID_FIELD_VALUE,
                in_reply_to=message_id,
                details={"field": "profile_id", "value": profile_id_str},
            )

        logger.info(f"Selecting profile: {profile_id}")

        # Check profile exists
        profile = self.repository.get_profile(profile_id)
        if not profile:
            return create_error_response(
                ErrorCode.PROFILE_NOT_FOUND,
                in_reply_to=message_id,
                details={"profile_id": profile_id_str},
            )

        # Set as active
        self.session_manager.set_active_profile(client_id, profile.id)

        logger.info(f"Selected profile {profile.id}: {profile.name}")

        # Return session state
        return build_session_state_response(
            self.session_manager,
            self.repository,
            client_id,
            in_reply_to=message_id,
        )

    async def handle_update_profile(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update a player profile.

        Updates profile in DB and returns session_state.
        """
        payload = message["payload"]
        message_id = message["message_id"]

        profile_id_str = payload["profile_id"]
        name = payload.get("name")
        batting_hand = payload.get("batting_hand")

        try:
            profile_id = int(profile_id_str)
        except ValueError:
            return create_error_response(
                ErrorCode.INVALID_FIELD_VALUE,
                in_reply_to=message_id,
                details={"field": "profile_id", "value": profile_id_str},
            )

        logger.info(f"Updating profile: {profile_id}")

        # Get current profile for version
        profile = self.repository.get_profile(profile_id)
        if not profile:
            return create_error_response(
                ErrorCode.PROFILE_NOT_FOUND,
                in_reply_to=message_id,
                details={"profile_id": profile_id_str},
            )

        try:
            # Update in database
            updated = self.repository.update_profile(
                player_id=profile_id,
                version=profile.version,
                name=name,
                batting_hand=batting_hand,
            )

            logger.info(f"Updated profile {updated.id}: {updated.name}")

            # Return session state
            return build_session_state_response(
                self.session_manager,
                self.repository,
                client_id,
                in_reply_to=message_id,
            )

        except RecordNotFoundError:
            return create_error_response(
                ErrorCode.PROFILE_NOT_FOUND,
                in_reply_to=message_id,
                details={"profile_id": profile_id_str},
            )
        except RepositoryError as e:
            logger.error(f"Failed to update profile: {e}")
            return create_extended_error(
                e.code,
                in_reply_to=message_id,
                details=e.details,
            )

    # =========================================================================
    # SESSION HANDLERS
    # =========================================================================

    async def handle_start_session(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Start a new practice session.

        Creates session in DB and tracks in active_sessions.
        """
        payload = message["payload"]
        message_id = message["message_id"]

        profile_id_str = payload["profile_id"]
        field_config = payload.get("field_config")
        difficulty = payload.get("difficulty")
        notes = payload.get("notes")

        # Parse profile ID
        try:
            profile_id = int(profile_id_str)
        except ValueError:
            return create_error_response(
                ErrorCode.INVALID_FIELD_VALUE,
                in_reply_to=message_id,
                details={"field": "profile_id", "value": profile_id_str},
            )

        # Check if already has active session
        if self.session_manager.has_active_session(client_id):
            return create_error_response(
                ErrorCode.SESSION_ALREADY_ACTIVE,
                in_reply_to=message_id,
            )

        # Verify profile exists
        profile = self.repository.get_profile(profile_id)
        if not profile:
            return create_error_response(
                ErrorCode.PROFILE_NOT_FOUND,
                in_reply_to=message_id,
                details={"profile_id": profile_id_str},
            )

        logger.info(f"Starting session for profile {profile_id}")

        try:
            # Create session in database
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            db_session = self.repository.create_session(
                player_id=profile_id,
                date=date_str,
                field_config=field_config,
                difficulty=difficulty or "medium",
                notes=notes,
            )

            # Track in session manager
            active_session = self.session_manager.create_session(
                session_id=db_session.id,
                player_id=profile_id,
                websocket_id=client_id,
                field_config=field_config,
                difficulty=difficulty,
                boundary_distance=db_session.boundary_distance,
            )

            # Create active session tracker in DB
            self.repository.create_active_session(
                session_id=db_session.id,
                websocket_id=client_id,
            )

            # Join the session in connection manager
            await self.connection_manager.join_session(
                client_id,
                str(db_session.id),
            )

            # Set active profile
            self.session_manager.set_active_profile(client_id, profile_id)

            logger.info(f"Started session {db_session.id} for player {profile_id}")

            # Return session state
            return build_session_state_response(
                self.session_manager,
                self.repository,
                client_id,
                in_reply_to=message_id,
            )

        except RepositoryError as e:
            logger.error(f"Failed to create session: {e}")
            return create_extended_error(
                e.code,
                in_reply_to=message_id,
                details=e.details,
            )

    async def handle_end_session(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        End an active session.

        Marks session complete in DB and cleans up active_session tracking.
        """
        payload = message["payload"]
        message_id = message["message_id"]

        session_id_str = payload["session_id"]
        save_to_database = payload.get("save_to_database", True)

        # Parse session ID
        try:
            session_id = int(session_id_str)
        except ValueError:
            return create_error_response(
                ErrorCode.INVALID_FIELD_VALUE,
                in_reply_to=message_id,
                details={"field": "session_id", "value": session_id_str},
            )

        # Verify session exists and belongs to client
        active_session = self.session_manager.get_session_by_id(session_id)
        if not active_session:
            return create_error_response(
                ErrorCode.SESSION_NOT_FOUND,
                in_reply_to=message_id,
                details={"session_id": session_id_str},
            )

        if active_session.websocket_id != client_id:
            return create_error_response(
                ErrorCode.SESSION_NOT_FOUND,
                in_reply_to=message_id,
                details={"session_id": session_id_str, "reason": "not owner"},
            )

        logger.info(f"Ending session {session_id}")

        try:
            # Get DB session for version
            db_session = self.repository.get_session(session_id)
            if db_session:
                # Mark as completed in database
                self.repository.complete_session(session_id, db_session.version)

            # Remove active session tracker
            self.repository.delete_active_session(session_id)

            # End in session manager
            self.session_manager.end_session(session_id)

            # Leave session in connection manager
            await self.connection_manager.leave_session(client_id)

            # Cleanup session data
            self.connection_manager.cleanup_session(str(session_id))

            logger.info(f"Ended session {session_id}")

            # Return session state (now without active session)
            return build_session_state_response(
                self.session_manager,
                self.repository,
                client_id,
                in_reply_to=message_id,
            )

        except RepositoryError as e:
            logger.error(f"Failed to end session: {e}")
            return create_extended_error(
                e.code,
                in_reply_to=message_id,
                details=e.details,
            )

    # =========================================================================
    # FIELD CONFIGURATION HANDLERS
    # =========================================================================

    async def handle_set_field(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update field configuration for the active session.

        Updates session's field_config and broadcasts to all session clients.
        """
        payload = message["payload"]
        message_id = message["message_id"]

        fielders = payload["fielders"]
        boundary_distance = payload.get("boundary_distance", 70.0)

        # Convert fielders to internal format
        field_config = [
            {"x": f["x"], "y": f["y"], "name": f["name"]}
            for f in fielders
        ]

        # Get active session
        active_session = self.session_manager.get_active_session(client_id)

        if active_session:
            # Update session field config
            self.session_manager.update_field_config(
                active_session.session_id,
                field_config,
                boundary_distance,
            )

            # Update database session
            try:
                db_session = self.repository.get_session(active_session.session_id)
                if db_session:
                    self.repository.update_session(
                        session_id=active_session.session_id,
                        version=db_session.version,
                        field_config=field_config,
                        boundary_distance=boundary_distance,
                    )
            except RepositoryError as e:
                logger.warning(f"Failed to persist field config: {e}")

            logger.info(
                f"Updated field config for session {active_session.session_id}: "
                f"{len(field_config)} fielders, boundary={boundary_distance}m"
            )

            # Build session state response
            response = build_session_state_response(
                self.session_manager,
                self.repository,
                client_id,
                in_reply_to=message_id,
            )

            # Broadcast to all session clients
            await self.connection_manager.broadcast_to_session(
                str(active_session.session_id),
                response,
                exclude_client=client_id,  # Don't send twice to sender
            )

            return response

        else:
            # No active session - just acknowledge with session state
            logger.debug(f"Set field with no active session, client={client_id}")
            return build_session_state_response(
                self.session_manager,
                self.repository,
                client_id,
                in_reply_to=message_id,
            )

    async def handle_set_difficulty(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update difficulty level for the active session.
        """
        payload = message["payload"]
        message_id = message["message_id"]

        difficulty = payload["difficulty"]

        # Get active session
        active_session = self.session_manager.get_active_session(client_id)

        if active_session:
            # Update session difficulty
            self.session_manager.update_difficulty(
                active_session.session_id,
                difficulty,
            )

            # Update database session
            try:
                db_session = self.repository.get_session(active_session.session_id)
                if db_session:
                    self.repository.update_session(
                        session_id=active_session.session_id,
                        version=db_session.version,
                        difficulty=difficulty,
                    )
            except RepositoryError as e:
                logger.warning(f"Failed to persist difficulty: {e}")

            logger.info(
                f"Updated difficulty for session {active_session.session_id}: {difficulty}"
            )

        # Return session state
        return build_session_state_response(
            self.session_manager,
            self.repository,
            client_id,
            in_reply_to=message_id,
        )

    # =========================================================================
    # MANUAL INPUT HANDLER (MOST IMPORTANT!)
    # =========================================================================

    async def handle_manual_input(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """
        Handle manual ball input.

        Records exactly the outcome the user tapped (dot/1/2/3/4/6/W/wd/nb)
        with the corresponding runs. Trajectory/kinematic fields (exit speed,
        angles, landing, etc.) are left NULL: a manual tap carries no radar
        measurement, and fabricating one would store data indistinguishable
        from a real reading. The row is flagged is_manual_input=True.

        Because there is no trajectory, only a session_state update is sent
        (no shot_result / wagon_wheel_update). Radar- and simulator-driven
        deliveries populate kinematics and the wagon wheel via their own paths.
        """
        payload = message["payload"]
        message_id = message["message_id"]

        result = payload["result"]
        is_boundary = payload.get("is_boundary", False)

        # Check for active session
        active_session = self.session_manager.get_active_session(client_id)
        if not active_session:
            return create_error_response(
                ErrorCode.SESSION_NOT_ACTIVE,
                in_reply_to=message_id,
            )

        logger.info(
            f"Manual input for session {active_session.session_id}: "
            f"result={result}, is_boundary={is_boundary}"
        )

        # Determine runs from result. Manual input is recorded verbatim - we do
        # NOT run the fielding simulation or synthesise radar kinematics here.
        if result in ["1", "2", "3", "4", "6"]:
            runs = int(result)
            if result in ["4", "6"]:
                is_boundary = True
        else:
            # dot, W (wicket), wd (wide), nb (no-ball) all score 0 runs
            runs = 0

        # Get next ball number
        last_delivery = self.repository.get_last_delivery(active_session.session_id)
        ball_number = (last_delivery.ball_number + 1) if last_delivery else 1

        outcome = result
        description = f"Manual input: {result}"

        # Create delivery in database. Trajectory/kinematic columns are left NULL
        # because a manual tap has no measured trajectory.
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        try:
            delivery = self.repository.create_delivery(
                session_id=active_session.session_id,
                ball_number=ball_number,
                timestamp=timestamp,
                outcome=outcome,
                runs=runs,
                is_boundary=is_boundary,
                description=description,
                is_manual_input=True,
            )

            logger.info(
                f"Created delivery {delivery.id} for session {active_session.session_id}: "
                f"ball={ball_number}, outcome={outcome}, runs={runs}"
            )

        except RepositoryError as e:
            logger.error(f"Failed to create delivery: {e}")
            return create_extended_error(
                e.code,
                in_reply_to=message_id,
                details=e.details,
            )

        # Update in-memory session state
        active_session.add_delivery(outcome, runs, is_boundary)

        # Manual input has no trajectory, so only a session_state update is sent.
        session_state = build_session_state_response(
            self.session_manager,
            self.repository,
            client_id,
            in_reply_to=message_id,
        )

        # Broadcast to other clients in the session; sender gets the return value.
        await self.connection_manager.broadcast_to_session(
            str(active_session.session_id),
            session_state,
            exclude_client=client_id,
        )

        return session_state

    # =========================================================================
    # UNDO HANDLER
    # =========================================================================

    async def handle_undo(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Undo the last delivery.

        Deletes last delivery from DB and recalculates session state.
        """
        payload = message["payload"]
        message_id = message["message_id"]

        session_id_str = payload["session_id"]

        # Parse session ID
        try:
            session_id = int(session_id_str)
        except ValueError:
            return create_error_response(
                ErrorCode.INVALID_FIELD_VALUE,
                in_reply_to=message_id,
                details={"field": "session_id", "value": session_id_str},
            )

        # Verify session exists and belongs to client
        active_session = self.session_manager.get_session_by_id(session_id)
        if not active_session:
            return create_error_response(
                ErrorCode.SESSION_NOT_FOUND,
                in_reply_to=message_id,
                details={"session_id": session_id_str},
            )

        if active_session.websocket_id != client_id:
            return create_error_response(
                ErrorCode.SESSION_NOT_FOUND,
                in_reply_to=message_id,
                details={"session_id": session_id_str, "reason": "not owner"},
            )

        # Get last delivery
        last_delivery = self.repository.get_last_delivery(session_id)
        if not last_delivery:
            return create_error_response(
                ErrorCode.UNDO_HISTORY_EMPTY,
                in_reply_to=message_id,
            )

        logger.info(
            f"Undoing delivery {last_delivery.id} from session {session_id}: "
            f"outcome={last_delivery.outcome}, runs={last_delivery.runs}"
        )

        try:
            # Delete from database
            deleted = self.repository.delete_last_delivery(session_id)
            if not deleted:
                return create_error_response(
                    ErrorCode.UNDO_HISTORY_EMPTY,
                    in_reply_to=message_id,
                )

            # Update in-memory session state
            active_session.undo_delivery(
                last_delivery.outcome,
                last_delivery.runs,
                last_delivery.is_boundary,
            )

            logger.info(f"Undid delivery {last_delivery.id}")

            # Build session state
            response = build_session_state_response(
                self.session_manager,
                self.repository,
                client_id,
                in_reply_to=message_id,
            )

            # Broadcast to session clients
            await self.connection_manager.broadcast_to_session(
                str(session_id),
                response,
                exclude_client=client_id,
            )

            return response

        except RepositoryError as e:
            logger.error(f"Failed to undo delivery: {e}")
            return create_extended_error(
                e.code,
                in_reply_to=message_id,
                details=e.details,
            )

    # =========================================================================
    # PING HANDLER
    # =========================================================================

    async def handle_ping(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle ping message, respond with pong."""
        return {
            "type": "pong",
            "message_id": generate_message_id(),
            "timestamp": create_timestamp(),
            "in_reply_to": message["message_id"],
        }

    # =========================================================================
    # RADAR RECORDING HANDLERS
    # =========================================================================

    async def handle_start_recording(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Start radar data recording.

        Begins capturing raw radar frames to disk. Auto-stops after
        max_duration seconds (default: the 15s short-clip limit).
        """
        payload = message.get("payload", {})
        message_id = message.get("message_id")

        session_type = payload.get("session_type", "both")
        max_duration = payload.get("max_duration")  # seconds; None = short-clip default

        if session_type not in SESSION_TYPES:
            return create_error_response(
                ErrorCode.INVALID_FIELD_VALUE,
                in_reply_to=message_id,
                details={"field": "session_type", "value": session_type},
            )

        recorder = get_recorder()

        if recorder.is_recording:
            return create_extended_error(
                "E6001",
                in_reply_to=message_id,
                message_override="Already recording",
            )

        try:
            # No progress callbacks here: clients poll get_recording_status
            # (recorder callbacks are sync and cannot await a websocket send).
            session = recorder.start_recording(session_type, max_duration_seconds=max_duration)

            logger.info(
                f"Started recording: type={session_type}, "
                f"max_duration={session.max_duration_seconds:.0f}s, "
                f"mock={session.is_mock}"
            )

            return {
                "type": "recording_started",
                "message_id": generate_message_id(),
                "timestamp": create_timestamp(),
                "in_reply_to": message_id,
                "payload": {
                    "session_type": session_type,
                    "start_time": session.start_time,
                    "max_duration": session.max_duration_seconds,
                    "mock": session.is_mock,
                    "counts": recorder.get_recording_counts(),
                },
            }

        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            return create_extended_error(
                "E6002",
                in_reply_to=message_id,
                message_override=f"Failed to start recording: {str(e)}",
            )

    async def handle_stop_recording(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Stop radar data recording.

        Stops capture and saves recorded frames to file.
        """
        message_id = message.get("message_id")

        recorder = get_recorder()

        if not recorder.is_recording:
            return create_extended_error(
                "E6003",
                in_reply_to=message_id,
                message_override="Not currently recording",
            )

        try:
            session = recorder.stop_recording()

            if session:
                logger.info(
                    f"Stopped recording: type={session.session_type}, "
                    f"frames={session.frame_count}, duration={session.duration_seconds:.1f}s"
                )

                return {
                    "type": "recording_stopped",
                    "message_id": generate_message_id(),
                    "timestamp": create_timestamp(),
                    "in_reply_to": message_id,
                    "payload": {
                        "session_type": session.session_type,
                        "duration_seconds": session.duration_seconds,
                        "frame_count": session.frame_count,
                        "annotation_count": session.annotation_count,
                        "mock": session.is_mock,
                        "file_path": session.file_path,
                        "counts": recorder.get_recording_counts(),
                    },
                }
            else:
                return create_extended_error(
                    "E6004",
                    in_reply_to=message_id,
                    message_override="Recording stop returned no session",
                )

        except Exception as e:
            logger.error(f"Failed to stop recording: {e}")
            return create_extended_error(
                "E6005",
                in_reply_to=message_id,
                message_override=f"Failed to stop recording: {str(e)}",
            )

    async def handle_get_recording_status(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Get current recording status and counts.
        """
        message_id = message.get("message_id")

        recorder = get_recorder()
        current = recorder.current_session

        return {
            "type": "recording_status",
            "message_id": generate_message_id(),
            "timestamp": create_timestamp(),
            "in_reply_to": message_id,
            "payload": {
                "is_recording": recorder.is_recording,
                "current_session_type": current.session_type if current else None,
                "current_start_time": current.start_time if current else None,
                "max_duration": current.max_duration_seconds if current else None,
                "frame_count": current.frame_count if current else 0,
                "annotation_count": current.annotation_count if current else 0,
                "mock": current.is_mock if current else False,
                "counts": recorder.get_recording_counts(),
            },
        }

    async def handle_add_annotation(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Mark an event during data-gathering recording (e.g. a hit ball plus the
        direction it went). The payload is stored verbatim as ground truth,
        timestamped against the recording clock, e.g.:
            {"label": "4", "direction_deg": 35.0, "zone": "cover", "shot_type": "drive"}
        """
        payload = message.get("payload", {})
        message_id = message.get("message_id")

        recorder = get_recorder()
        if not recorder.is_recording:
            return create_extended_error(
                "E6003",
                in_reply_to=message_id,
                message_override="Not currently recording",
            )

        annotation = recorder.add_annotation(payload)
        current = recorder.current_session

        return {
            "type": "annotation_added",
            "message_id": generate_message_id(),
            "timestamp": create_timestamp(),
            "in_reply_to": message_id,
            "payload": {
                "annotation": annotation,
                "annotation_count": current.annotation_count if current else 0,
            },
        }

    # =========================================================================
    # RADAR STREAMING HANDLERS
    # =========================================================================

    async def handle_start_radar_stream(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Start streaming radar data to this client.
        """
        message_id = message.get("message_id")

        streamer = get_streamer()

        # Capture the current event loop for thread-safe scheduling
        import asyncio
        main_loop = asyncio.get_running_loop()

        # Create callback that sends frames to this client
        async def send_frame(frame_data: dict):
            msg = {
                "type": "radar_frame",
                "message_id": generate_message_id(),
                "timestamp": create_timestamp(),
                "payload": frame_data,
            }
            await self.connection_manager.send_to_client(client_id, msg)

        # Thread-safe wrapper using run_coroutine_threadsafe
        def frame_callback(frame_data: dict):
            try:
                asyncio.run_coroutine_threadsafe(send_frame(frame_data), main_loop)
            except Exception as e:
                logger.error(f"Frame send error: {e}")

        # Store callback reference for cleanup
        if not hasattr(self, '_stream_callbacks'):
            self._stream_callbacks = {}
        self._stream_callbacks[client_id] = frame_callback

        # Add callback and start if not running
        streamer.add_callback(frame_callback)
        if not streamer.is_streaming:
            streamer.start()

        logger.info(f"Started radar stream for client {client_id[:8]}")

        return {
            "type": "radar_stream_started",
            "message_id": generate_message_id(),
            "timestamp": create_timestamp(),
            "in_reply_to": message_id,
            "payload": {
                "status": "streaming",
            },
        }

    async def handle_stop_radar_stream(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Stop streaming radar data to this client.
        """
        message_id = message.get("message_id")

        streamer = get_streamer()

        # Remove this client's callback
        if hasattr(self, '_stream_callbacks') and client_id in self._stream_callbacks:
            callback = self._stream_callbacks.pop(client_id)
            streamer.remove_callback(callback)

            # Stop streamer if no more callbacks
            if not self._stream_callbacks and streamer.is_streaming:
                streamer.stop()

        logger.info(f"Stopped radar stream for client {client_id[:8]}")

        return {
            "type": "radar_stream_stopped",
            "message_id": generate_message_id(),
            "timestamp": create_timestamp(),
            "in_reply_to": message_id,
            "payload": {
                "status": "stopped",
            },
        }

    # =========================================================================
    # SIMULATE SHOT HANDLER (for shot simulator UI)
    # =========================================================================

    async def handle_simulate_shot(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Handle simulate_shot message - run simulation and return full result.
        This is for the shot simulator UI, not for actual game play.
        """
        payload = message.get("payload", {})
        message_id = message.get("message_id")

        # Extract parameters
        exit_speed = payload.get("exit_speed", 0)
        horizontal_angle = payload.get("horizontal_angle", 0)
        vertical_angle = payload.get("vertical_angle", 0)
        field_config = payload.get("field_config", [])
        boundary_distance = payload.get("boundary_distance", 70)
        difficulty = payload.get("difficulty", "medium")

        # Calculate trajectory data for the simulation
        from engine.game_engine import _calculate_trajectory

        trajectory = _calculate_trajectory(exit_speed, horizontal_angle, vertical_angle)

        # Run simulation
        result = simulate_delivery(
            exit_speed=exit_speed,
            horizontal_angle=horizontal_angle,
            vertical_angle=vertical_angle,
            landing_x=trajectory.landing_x,
            landing_y=trajectory.landing_y,
            projected_distance=trajectory.projected_distance,
            max_height=trajectory.max_height,
            field_config=field_config,
            boundary_distance=boundary_distance,
            difficulty=difficulty,
        )

        logger.info(
            f"Simulate shot: speed={exit_speed}, angle={horizontal_angle}, "
            f"result={result.get('outcome')}, runs={result.get('runs')}"
        )

        # Build full simulation result with trajectory as dict
        trajectory_dict = {
            "projected_distance": trajectory.projected_distance,
            "aerial_distance": trajectory.aerial_distance,
            "rolling_distance": trajectory.rolling_distance,
            "max_height": trajectory.max_height,
            "landing_x": trajectory.landing_x,
            "landing_y": trajectory.landing_y,
            "final_x": trajectory.final_x,
            "final_y": trajectory.final_y,
            "time_of_flight": trajectory.time_of_flight,
            "horizontal_speed": trajectory.horizontal_speed,
            "vertical_speed": trajectory.vertical_speed,
            "direction_x": trajectory.direction_x,
            "direction_y": trajectory.direction_y,
        }
        simulation_result = {
            **result,
            "trajectory": trajectory_dict,
        }

        return {
            "type": "simulate_result",
            "message_id": generate_message_id(),
            "timestamp": create_timestamp(),
            "in_reply_to": message_id,
            "payload": {
                "simulation": simulation_result,
            },
        }


# =============================================================================
# HANDLER REGISTRATION
# =============================================================================

def register_handlers(
    server: Any,
    repository: Repository,
    session_manager: SessionManager,
) -> MessageHandlers:
    """
    Register all message handlers with the server.

    Args:
        server: The CricketWebSocketServer instance
        repository: Database repository
        session_manager: Session state manager

    Returns:
        The MessageHandlers instance
    """
    handlers = MessageHandlers(
        repository=repository,
        session_manager=session_manager,
        connection_manager=server.connection_manager,
    )

    # Register all handlers
    server.message_router.register_handler("create_profile", handlers.handle_create_profile)
    server.message_router.register_handler("select_profile", handlers.handle_select_profile)
    server.message_router.register_handler("update_profile", handlers.handle_update_profile)
    server.message_router.register_handler("start_session", handlers.handle_start_session)
    server.message_router.register_handler("end_session", handlers.handle_end_session)
    server.message_router.register_handler("set_field", handlers.handle_set_field)
    server.message_router.register_handler("set_difficulty", handlers.handle_set_difficulty)
    server.message_router.register_handler("manual_input", handlers.handle_manual_input)
    server.message_router.register_handler("undo", handlers.handle_undo)
    server.message_router.register_handler("simulate_shot", handlers.handle_simulate_shot)
    server.message_router.register_handler("ping", handlers.handle_ping)

    # Recording handlers
    server.message_router.register_handler("start_recording", handlers.handle_start_recording)
    server.message_router.register_handler("stop_recording", handlers.handle_stop_recording)
    server.message_router.register_handler("get_recording_status", handlers.handle_get_recording_status)
    server.message_router.register_handler("add_annotation", handlers.handle_add_annotation)

    # Streaming handlers
    server.message_router.register_handler("start_radar_stream", handlers.handle_start_radar_stream)
    server.message_router.register_handler("stop_radar_stream", handlers.handle_stop_radar_stream)

    logger.info("Registered all message handlers")

    return handlers
