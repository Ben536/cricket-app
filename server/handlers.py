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

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional, TYPE_CHECKING

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

# Recording state errors (E61xx - E60xx is reserved for radar hardware
# errors in contracts/error_codes.md and must not be reused)
ERROR_ALREADY_RECORDING = "E6101"
ERROR_RECORDING_START_FAILED = "E6102"
ERROR_NOT_RECORDING = "E6103"
ERROR_RECORDING_NO_SESSION = "E6104"
ERROR_RECORDING_STOP_FAILED = "E6105"
ERROR_RECORDING_LIST_FAILED = "E6106"
ERROR_RECORDING_READ_FAILED = "E6107"

# Extended error messages
EXTENDED_ERROR_MESSAGES = {
    ERROR_DATABASE: ("Database operation failed", True),
    ERROR_VERSION_CONFLICT: ("Version conflict, please refresh", True),
    ERROR_CONSTRAINT_VIOLATION: ("Database constraint violated", False),
    ERROR_RECORD_NOT_FOUND: ("Record not found", False),
    ERROR_ALREADY_RECORDING: ("Already recording", True),
    ERROR_RECORDING_START_FAILED: ("Failed to start recording", True),
    ERROR_NOT_RECORDING: ("Not currently recording", True),
    ERROR_RECORDING_NO_SESSION: ("Recording stop returned no session", True),
    ERROR_RECORDING_STOP_FAILED: ("Failed to stop recording", True),
    ERROR_RECORDING_LIST_FAILED: ("Failed to list recordings", True),
    ERROR_RECORDING_READ_FAILED: ("Failed to read recording", True),
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

async def build_session_state_response(
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
    profiles = await asyncio.to_thread(repository.get_all_profiles)
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


# NOTE: build_shot_result_response and build_wagon_wheel_update are the
# contract-defined messages for RADAR-DRIVEN deliveries (shot_result,
# wagon_wheel_update). Nothing emits them yet - the ball-detection pipeline
# (radar/detector.py) is what will wire them up. Manual input deliberately
# does NOT use them (a manual tap has no trajectory).
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
# RADAR STREAM FAN-OUT
# =============================================================================

# Frames buffered per streaming client. At 20Hz this is 250ms of lag before
# the oldest frame is dropped for that client. The live view wants the
# freshest frame, not a backlog; a slow phone gets fewer frames, never a
# growing pile of tasks.
STREAM_QUEUE_MAX = 5
STREAM_DROP_LOG_EVERY = 100


@dataclass
class _StreamSubscription:
    """One client's live radar stream: the streamer callback (runs on the
    radar dispatch thread), the bounded queue it feeds, and the loop task
    that drains the queue into the socket."""
    callback: Callable[[dict], None]
    queue: asyncio.Queue
    task: asyncio.Task
    dropped: int = 0


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

        # client_id -> live radar stream subscription
        self._streams: dict[str, _StreamSubscription] = {}

        logger.info("MessageHandlers initialized")

    # =========================================================================
    # DISCONNECT LIFECYCLE
    # =========================================================================

    async def cleanup_client(self, client_id: str) -> None:
        """
        Release everything a disconnecting client holds. Called from the
        connection handler's finally block - a phone wandering out of WiFi
        range mid-session is the NORMAL case at the nets, not an edge case.

        Without this: sessions/profiles leak in memory, active_sessions rows
        leak in the DB, and radar stream callbacks keep firing at frame rate
        into a dead connection forever (holding the serial port open).
        """
        # 1. Radar stream: deregister this client's callback, cancel its
        #    drain task; stop the streamer (and release the port) when no
        #    subscribers remain.
        if await self._release_stream(client_id):
            logger.info(f"Deregistered radar stream for disconnected client {client_id[:8]}")

        # 2. Session: persist completion and drop all trackers. The client_id
        #    is per-connection and there is no resume path, so an orphaned
        #    session could never be continued - completing it keeps the DB
        #    consistent and the data queryable.
        active = self.session_manager.get_active_session(client_id)
        if active:
            session_id = active.session_id
            try:
                db_session = await asyncio.to_thread(self.repository.get_session, session_id)
                if db_session and not db_session.is_completed:
                    await asyncio.to_thread(
                        self.repository.complete_session, session_id, db_session.version
                    )
                await asyncio.to_thread(self.repository.delete_active_session, session_id)
            except Exception as e:
                logger.error(f"Disconnect cleanup DB error for session {session_id}: {e}")
            await self.connection_manager.leave_session(client_id)
            self.connection_manager.cleanup_session(str(session_id))
            logger.info(f"Auto-completed session {session_id} for disconnected client {client_id[:8]}")

        # 3. In-memory client state (session map + active profile)
        self.session_manager.cleanup_client(client_id)

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
            profile = await asyncio.to_thread(self.repository.create_profile, 
                name=name,
                batting_hand=batting_hand,
            )

            logger.info(f"Created profile {profile.id}: {profile.name}")

            # Set as active profile
            self.session_manager.set_active_profile(client_id, profile.id)

            # Return session state with new profile
            return await build_session_state_response(
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
        profile = await asyncio.to_thread(self.repository.get_profile, profile_id)
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
        return await build_session_state_response(
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
        profile = await asyncio.to_thread(self.repository.get_profile, profile_id)
        if not profile:
            return create_error_response(
                ErrorCode.PROFILE_NOT_FOUND,
                in_reply_to=message_id,
                details={"profile_id": profile_id_str},
            )

        try:
            # Update in database
            updated = await asyncio.to_thread(self.repository.update_profile, 
                player_id=profile_id,
                version=profile.version,
                name=name,
                batting_hand=batting_hand,
            )

            logger.info(f"Updated profile {updated.id}: {updated.name}")

            # Return session state
            return await build_session_state_response(
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
        profile = await asyncio.to_thread(self.repository.get_profile, profile_id)
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
            db_session = await asyncio.to_thread(self.repository.create_session, 
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
            await asyncio.to_thread(self.repository.create_active_session, 
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
            return await build_session_state_response(
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
            db_session = await asyncio.to_thread(self.repository.get_session, session_id)
            if db_session:
                # Mark as completed in database
                await asyncio.to_thread(self.repository.complete_session, session_id, db_session.version)

            # Remove active session tracker
            await asyncio.to_thread(self.repository.delete_active_session, session_id)

            # End in session manager
            self.session_manager.end_session(session_id)

            # Leave session in connection manager
            await self.connection_manager.leave_session(client_id)

            # Cleanup session data
            self.connection_manager.cleanup_session(str(session_id))

            logger.info(f"Ended session {session_id}")

            # Return session state (now without active session)
            return await build_session_state_response(
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

        # Convert fielders to internal format. `name` is optional on the wire
        # (the router accepts a fielder without one); default it the way the
        # engine does rather than KeyError on a valid message.
        field_config = [
            {"x": f["x"], "y": f["y"], "name": f.get("name") or f"fielder_{i}"}
            for i, f in enumerate(fielders)
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
                db_session = await asyncio.to_thread(self.repository.get_session, active_session.session_id)
                if db_session:
                    await asyncio.to_thread(self.repository.update_session, 
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
            response = await build_session_state_response(
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
            return await build_session_state_response(
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
                db_session = await asyncio.to_thread(self.repository.get_session, active_session.session_id)
                if db_session:
                    await asyncio.to_thread(self.repository.update_session, 
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
        return await build_session_state_response(
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

        outcome = result
        description = f"Manual input: {result}"

        # Create delivery in database. Trajectory/kinematic columns are left NULL
        # because a manual tap has no measured trajectory. ball_number=None ->
        # assigned atomically inside the INSERT (read-then-insert raced with
        # other clients in the same session).
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        try:
            delivery = await asyncio.to_thread(self.repository.create_delivery,
                session_id=active_session.session_id,
                ball_number=None,
                timestamp=timestamp,
                outcome=outcome,
                runs=runs,
                is_boundary=is_boundary,
                description=description,
                is_manual_input=True,
            )

            logger.info(
                f"Created delivery {delivery.id} for session {active_session.session_id}: "
                f"ball={delivery.ball_number}, outcome={outcome}, runs={runs}"
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
        session_state = await build_session_state_response(
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
        last_delivery = await asyncio.to_thread(self.repository.get_last_delivery, session_id)
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
            deleted = await asyncio.to_thread(self.repository.delete_last_delivery, session_id)
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
            response = await build_session_state_response(
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

        if not isinstance(session_type, str) or session_type not in SESSION_TYPES:
            return create_error_response(
                ErrorCode.INVALID_FIELD_VALUE,
                in_reply_to=message_id,
                details={"field": "session_type", "value": str(session_type)[:100]},
            )

        recorder = get_recorder()

        if recorder.is_recording:
            return create_extended_error(
                ERROR_ALREADY_RECORDING,
                in_reply_to=message_id,
                message_override="Already recording",
            )

        try:
            # No progress callbacks here: clients poll get_recording_status
            # (recorder callbacks are sync and cannot await a websocket send).
            # start_recording opens the serial port (blocking) - off the loop.
            # The `is_recording` check above is only a fast path: two clients
            # can both pass it before either reaches this await. The recorder
            # itself makes the check-and-start atomic and raises for the loser.
            session = await asyncio.to_thread(
                recorder.start_recording, session_type, max_duration_seconds=max_duration
            )

            logger.info(
                f"Started recording: type={session_type}, "
                f"max_duration={session.max_duration_seconds:.0f}s, "
                f"mock={session.is_mock}"
            )

            counts = await asyncio.to_thread(recorder.get_recording_counts)
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
                    "counts": counts,
                },
            }

        except ValueError as e:
            # The recorder's own refusals: lost a start race, or not enough
            # disk for the requested duration. Both are user-facing messages.
            lost_race = "already recording" in str(e).lower()
            logger.warning(f"Recording not started: {e}")
            return create_extended_error(
                ERROR_ALREADY_RECORDING if lost_race else ERROR_RECORDING_START_FAILED,
                in_reply_to=message_id,
                message_override=str(e) if lost_race else f"Failed to start recording: {e}",
            )
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            return create_extended_error(
                ERROR_RECORDING_START_FAILED,
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
                ERROR_NOT_RECORDING,
                in_reply_to=message_id,
                message_override="Not currently recording",
            )

        try:
            # stop_recording joins the recording thread (up to 2s) - off the loop
            session = await asyncio.to_thread(recorder.stop_recording)

            if session:
                logger.info(
                    f"Stopped recording: type={session.session_type}, "
                    f"frames={session.frame_count}, duration={session.duration_seconds:.1f}s"
                )

                counts = await asyncio.to_thread(recorder.get_recording_counts)
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
                        # Set when the recording stopped ITSELF because a
                        # write failed (card full, I/O error) - the file is
                        # truncated at that point and the UI must say so.
                        "error": session.error,
                        "counts": counts,
                    },
                }
            else:
                return create_extended_error(
                    ERROR_RECORDING_NO_SESSION,
                    in_reply_to=message_id,
                    message_override="Recording stop returned no session",
                )

        except Exception as e:
            logger.error(f"Failed to stop recording: {e}")
            return create_extended_error(
                ERROR_RECORDING_STOP_FAILED,
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
        last = recorder.last_session

        # Directory glob - polled every 2s by the UI, keep it off the loop
        counts = await asyncio.to_thread(recorder.get_recording_counts)
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
                # Why the LAST recording stopped, if it stopped itself on a
                # write failure. A polling UI that sees is_recording flip to
                # false reads this to tell "hit max duration" from "the card
                # is full and the file is truncated".
                "last_error": last.error if last else None,
                "counts": counts,
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
                ERROR_NOT_RECORDING,
                in_reply_to=message_id,
                message_override="Not currently recording",
            )

        annotation = await asyncio.to_thread(recorder.add_annotation, payload)
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

    async def handle_list_recordings(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        List the recordings on the device, newest first.

        Summaries only (type, time, duration, frame/mark counts, mock and
        incomplete flags) - never frame data. A full scan of a crashed file
        can be slow on the SD card, so this runs off the event loop.
        """
        payload = message.get("payload", {})
        message_id = message.get("message_id")
        session_type = payload.get("session_type")  # optional filter

        if session_type is not None and session_type not in SESSION_TYPES:
            return create_error_response(
                ErrorCode.INVALID_FIELD_VALUE,
                in_reply_to=message_id,
                details={"field": "session_type", "value": str(session_type)[:100]},
            )

        recorder = get_recorder()
        try:
            recordings = await asyncio.to_thread(recorder.list_recordings, session_type)
        except Exception as e:
            logger.error(f"Failed to list recordings: {e}")
            return create_extended_error(
                ERROR_RECORDING_LIST_FAILED,
                in_reply_to=message_id,
                message_override=f"Failed to list recordings: {e}",
            )

        # Newest first: filenames are timestamps, and list_recordings already
        # sorts within a type - re-sort across types.
        recordings.sort(key=lambda r: str(r.get("start_time") or r.get("file")), reverse=True)

        return {
            "type": "recordings_list",
            "message_id": generate_message_id(),
            "timestamp": create_timestamp(),
            "in_reply_to": message_id,
            "payload": {
                "recordings": recordings,
                "counts": await asyncio.to_thread(recorder.get_recording_counts),
            },
        }

    async def handle_get_recording(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Read one recording's labels back: meta, every annotation, totals.

        Frames are never returned - a gathering session holds ~1.25GB of them
        and the phone only wants the marks (to redraw the session's wagon
        wheel). The path is resolved inside recordings/ or refused.
        """
        payload = message.get("payload", {})
        message_id = message.get("message_id")
        file_path = payload.get("file")

        recorder = get_recorder()
        try:
            detail = await asyncio.to_thread(recorder.read_annotations, file_path)
        except ValueError as e:
            return create_error_response(
                ErrorCode.INVALID_FIELD_VALUE,
                in_reply_to=message_id,
                details={"field": "file", "reason": str(e)[:200]},
            )
        except Exception as e:
            logger.error(f"Failed to read recording {file_path}: {e}")
            return create_extended_error(
                ERROR_RECORDING_READ_FAILED,
                in_reply_to=message_id,
                message_override=f"Failed to read recording: {e}",
            )

        return {
            "type": "recording_detail",
            "message_id": generate_message_id(),
            "timestamp": create_timestamp(),
            "in_reply_to": message_id,
            "payload": detail,
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

        Frames arrive on the radar dispatch thread and are handed to the
        event loop through a BOUNDED per-client queue drained by one
        long-lived task. The previous design scheduled one
        run_coroutine_threadsafe() per frame and discarded the Future: no
        bound, no backpressure, and any exception was stored in the dropped
        Future and never seen. Measured then: pending sends grew 5 -> 18 in
        3s against a slow socket and 18 stayed parked after unsubscribing -
        ~72,000 tasks per hour per stuck client on a 1GB Pi.
        """
        message_id = message.get("message_id")

        streamer = get_streamer()
        main_loop = asyncio.get_running_loop()

        # A repeat start from the same client must not leave the old closure
        # registered in the streamer (duplicate frames, unremovable callback).
        await self._release_stream(client_id)

        frame_queue: asyncio.Queue = asyncio.Queue(maxsize=STREAM_QUEUE_MAX)
        holder: dict[str, _StreamSubscription] = {}

        def enqueue(frame_data: dict) -> None:
            """Runs ON the event loop. Drop the OLDEST frame when full: the
            live view wants the freshest frame, not a backlog."""
            if frame_queue.full():
                try:
                    frame_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                sub = holder.get("sub")
                if sub is not None:
                    sub.dropped += 1
                    if sub.dropped % STREAM_DROP_LOG_EVERY == 1:
                        logger.warning(
                            f"Radar stream to {client_id[:8]} is slow - dropped oldest frame "
                            f"[{sub.dropped} dropped so far]"
                        )
            try:
                frame_queue.put_nowait(frame_data)
            except asyncio.QueueFull:
                pass

        def frame_callback(frame_data: dict) -> None:
            """Runs on the radar dispatch thread. Must not block."""
            try:
                main_loop.call_soon_threadsafe(enqueue, frame_data)
            except RuntimeError:
                # Loop is closed (server shutting down) - nothing to deliver to
                pass

        task = main_loop.create_task(
            self._drain_stream(client_id, frame_queue), name=f"radar-stream-{client_id[:8]}"
        )
        sub = _StreamSubscription(callback=frame_callback, queue=frame_queue, task=task)
        holder["sub"] = sub
        self._streams[client_id] = sub

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

    async def _drain_stream(self, client_id: str, frame_queue: asyncio.Queue) -> None:
        """Deliver queued frames to one client until cancelled or the client
        becomes unreachable. Exceptions are logged here, not lost."""
        try:
            while True:
                frame_data = await frame_queue.get()
                msg = {
                    "type": "radar_frame",
                    "message_id": generate_message_id(),
                    "timestamp": create_timestamp(),
                    "payload": frame_data,
                }
                if not await self.connection_manager.send_to_client(client_id, msg):
                    logger.info(f"Radar stream to {client_id[:8]} ended: client unreachable")
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"Radar stream task for {client_id[:8]} crashed: {e}")
        finally:
            # Exited on our own (not via _release_stream): deregister so the
            # dispatch thread stops queuing frames for a dead client.
            sub = self._streams.get(client_id)
            if sub is not None and sub.task is asyncio.current_task():
                self._streams.pop(client_id, None)
                streamer = get_streamer()
                streamer.remove_callback(sub.callback)
                if not self._streams and streamer.is_streaming:
                    await asyncio.to_thread(streamer.stop)

    async def _release_stream(self, client_id: str) -> bool:
        """Tear down a client's stream subscription. Returns True if one existed."""
        sub = self._streams.pop(client_id, None)
        if sub is None:
            return False
        streamer = get_streamer()
        streamer.remove_callback(sub.callback)
        if sub.task is not asyncio.current_task():
            sub.task.cancel()
            try:
                await sub.task
            except asyncio.CancelledError:
                # Swallow the drain task's cancellation only. If WE are being
                # cancelled (the awaiting task), the same exception surfaces
                # here and must propagate to the caller.
                if not sub.task.cancelled():
                    raise
            except Exception:
                pass
        if not self._streams and streamer.is_streaming:
            # Joins the reader thread - off the loop
            await asyncio.to_thread(streamer.stop)
        if sub.dropped:
            logger.info(f"Radar stream for {client_id[:8]} dropped {sub.dropped} frame(s) to a slow socket")
        return True

    async def handle_stop_radar_stream(
        self,
        client_id: str,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Stop streaming radar data to this client.
        """
        message_id = message.get("message_id")

        sub = self._streams.get(client_id)
        dropped = sub.dropped if sub else 0
        await self._release_stream(client_id)

        logger.info(f"Stopped radar stream for client {client_id[:8]}")

        return {
            "type": "radar_stream_stopped",
            "message_id": generate_message_id(),
            "timestamp": create_timestamp(),
            "in_reply_to": message_id,
            "payload": {
                "status": "stopped",
                "frames_dropped": dropped,
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

        # Optional client-supplied PRNG seed: the client sends the same seed
        # to its local engine fallback, so the outcome is identical whether
        # this server answered in time or not.
        seed = payload.get("seed")
        try:
            seed = int(seed) & 0xFFFFFFFF if seed is not None else None
        except (TypeError, ValueError):
            seed = None

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
            seed=seed,
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
    server.message_router.register_handler("list_recordings", handlers.handle_list_recordings)
    server.message_router.register_handler("get_recording", handlers.handle_get_recording)

    # Streaming handlers
    server.message_router.register_handler("start_radar_stream", handlers.handle_start_radar_stream)
    server.message_router.register_handler("stop_radar_stream", handlers.handle_stop_radar_stream)

    logger.info("Registered all message handlers")

    return handlers
