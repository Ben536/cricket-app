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

        Two modes:
        1. type="runs" (simple): Just add runs to session, send session_state
        2. type="simulate": Call game engine, store delivery in DB,
           send shot_result + wagon_wheel_update + session_state

        The payload.result determines the type:
        - "dot", "1", "2", "3": Manual runs (no simulation)
        - "4", "6": Can be either manual boundary or simulated
        - "W", "wd", "nb": Special results (manual)

        For simulation, we need radar data or synthetic trajectory data.
        Since this is manual input, we simulate based on the result.
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

        # Determine runs from result
        if result == "dot":
            runs = 0
        elif result in ["1", "2", "3", "4", "6"]:
            runs = int(result)
            if result in ["4", "6"]:
                is_boundary = True
        elif result == "W":
            runs = 0
        else:
            runs = 0  # Wides, no-balls, etc.

        # Get next ball number
        last_delivery = self.repository.get_last_delivery(active_session.session_id)
        ball_number = (last_delivery.ball_number + 1) if last_delivery else 1

        # Determine if we should simulate
        # For boundaries (4, 6), we can simulate a shot
        # For other results, we just record manually
        should_simulate = is_boundary and result in ["4", "6"]

        if should_simulate:
            # Generate synthetic trajectory for simulation
            # These are reasonable values for the given result
            if result == "6":
                exit_speed = 120.0 + (ball_number % 20)  # 120-140 km/h
                vertical_angle = 35.0 + (ball_number % 10)  # 35-45 degrees
                horizontal_angle = (ball_number * 15) % 180 - 90  # -90 to 90
                projected_distance = 75.0 + (ball_number % 15)
                max_height = 15.0 + (ball_number % 5)
            else:  # 4
                exit_speed = 90.0 + (ball_number % 30)  # 90-120 km/h
                vertical_angle = 5.0 + (ball_number % 15)  # 5-20 degrees
                horizontal_angle = (ball_number * 20) % 180 - 90
                projected_distance = 70.0 + (ball_number % 10)
                max_height = 2.0 + (ball_number % 3)

            # Calculate landing coordinates
            import math
            h_rad = math.radians(horizontal_angle)
            landing_x = -projected_distance * math.sin(h_rad)
            landing_y = projected_distance * math.cos(h_rad)

            # Run simulation
            try:
                sim_result = simulate_delivery(
                    exit_speed=exit_speed,
                    horizontal_angle=horizontal_angle,
                    vertical_angle=vertical_angle,
                    landing_x=landing_x,
                    landing_y=landing_y,
                    projected_distance=projected_distance,
                    max_height=max_height,
                    field_config=active_session.field_config,
                    boundary_distance=active_session.boundary_distance,
                    difficulty=active_session.difficulty,
                )

                # Use simulation result
                outcome = sim_result["outcome"]
                runs = sim_result["runs"]
                is_boundary = sim_result["is_boundary"]
                description = sim_result.get("description", "")
                end_position = sim_result.get("end_position", {"x": landing_x, "y": landing_y})

            except Exception as e:
                logger.error(f"Simulation failed: {e}")
                # Fall back to manual recording
                sim_result = None
                outcome = result
                description = f"Manual input: {result}"
                end_position = {"x": 0, "y": 0}

        else:
            # Pure manual input - no simulation
            sim_result = None
            outcome = result
            description = f"Manual input: {result}"
            end_position = {"x": 0, "y": 0}
            exit_speed = None
            horizontal_angle = None
            vertical_angle = None
            landing_x = None
            landing_y = None
            projected_distance = None
            max_height = None

        # Create delivery in database
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        try:
            delivery = self.repository.create_delivery(
                session_id=active_session.session_id,
                ball_number=ball_number,
                timestamp=timestamp,
                outcome=outcome,
                runs=runs,
                exit_speed=exit_speed if should_simulate else None,
                horizontal_angle=horizontal_angle if should_simulate else None,
                vertical_angle=vertical_angle if should_simulate else None,
                landing_x=landing_x if should_simulate else None,
                landing_y=landing_y if should_simulate else None,
                projected_distance=projected_distance if should_simulate else None,
                max_height=max_height if should_simulate else None,
                fielder_position=sim_result.get("fielder_position") if sim_result else None,
                fielding_position=sim_result.get("fielding_position") if sim_result else None,
                end_position=end_position,
                is_boundary=is_boundary,
                is_aerial=sim_result.get("is_aerial", False) if sim_result else False,
                description=description,
                catch_analysis=sim_result.get("catch_analysis") if sim_result else None,
                fielding_time=sim_result.get("fielding_time") if sim_result else None,
                collection_difficulty=sim_result.get("collection_difficulty") if sim_result else None,
                alignment_score=sim_result.get("alignment_score") if sim_result else None,
                priority_score=sim_result.get("priority_score") if sim_result else None,
                fielder_arrival_time=sim_result.get("fielder_arrival_time") if sim_result else None,
                ball_arrival_time=sim_result.get("ball_arrival_time") if sim_result else None,
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

        # Build responses
        responses = []

        # 1. Shot result (if simulated)
        if sim_result:
            shot_result = build_shot_result_response(
                session_id=active_session.session_id,
                ball_number=ball_number,
                simulation_result=sim_result,
                in_reply_to=message_id,
            )
            responses.append(("shot_result", shot_result))

            # 2. Wagon wheel update
            wagon_wheel = build_wagon_wheel_update(
                session_id=active_session.session_id,
                shot_id=str(delivery.id),
                end_x=end_position.get("x", 0),
                end_y=end_position.get("y", 0),
                outcome=outcome,
                distance=projected_distance or 0,
            )
            responses.append(("wagon_wheel", wagon_wheel))

        # 3. Session state (always)
        session_state = build_session_state_response(
            self.session_manager,
            self.repository,
            client_id,
            in_reply_to=message_id,
        )
        responses.append(("session_state", session_state))

        # Broadcast all responses to session clients
        for msg_type, msg in responses:
            await self.connection_manager.broadcast_to_session(
                str(active_session.session_id),
                msg,
                exclude_client=client_id,  # Sender gets direct response
            )

        # Send all to sender
        # For the sender, we need to return one response directly
        # and send others via the connection manager
        if len(responses) == 1:
            return responses[0][1]
        else:
            # Send first N-1 via connection manager, return last
            for msg_type, msg in responses[:-1]:
                await self.connection_manager.send_to_client(client_id, msg)
            return responses[-1][1]

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
    server.message_router.register_handler("ping", handlers.handle_ping)

    logger.info("Registered all message handlers")

    return handlers
