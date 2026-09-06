"""
Message Router for Cricket App WebSocket Server

Routes incoming messages to appropriate handlers based on message type.
Validates message schema before routing.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional
from enum import Enum

# Define essential types locally to avoid import issues with complex dataclass inheritance
import uuid


class Difficulty(str, Enum):
    """Difficulty levels from api_types."""
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class BallResult(str, Enum):
    """Ball result types from api_types."""
    DOT = "dot"
    ONE = "1"
    TWO = "2"
    THREE = "3"
    FOUR = "4"
    SIX = "6"
    WICKET = "W"
    WIDE = "wd"
    NO_BALL = "nb"


class BattingHand(str, Enum):
    """Batting hand from api_types."""
    RIGHT = "right"
    LEFT = "left"


def generate_message_id() -> str:
    """Generate a UUID v4 for message IDs."""
    return str(uuid.uuid4())


def create_timestamp() -> str:
    """Create an ISO 8601 timestamp."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

logger = logging.getLogger(__name__)


# Type alias for message handlers
MessageHandler = Callable[
    [str, dict[str, Any]],  # client_id, message
    Coroutine[Any, Any, Optional[dict[str, Any]]]  # returns optional response
]


# Error code definitions from error_codes.md
class ErrorCode(str, Enum):
    # Connection errors (1xxx)
    CONNECTION_TIMEOUT = "E1001"
    CONNECTION_REFUSED = "E1002"
    CONNECTION_LOST = "E1003"
    PROTOCOL_ERROR = "E1004"
    SERVER_UNAVAILABLE = "E1005"
    HEARTBEAT_TIMEOUT = "E1006"

    # Validation errors (3xxx)
    INVALID_MESSAGE_FORMAT = "E3001"
    INVALID_MESSAGE_TYPE = "E3002"
    MISSING_REQUIRED_FIELD = "E3003"
    INVALID_FIELD_VALUE = "E3004"
    FIELD_OUT_OF_RANGE = "E3005"
    INVALID_UUID = "E3006"
    INVALID_TIMESTAMP = "E3007"
    PAYLOAD_TOO_LARGE = "E3008"

    # State errors (4xxx)
    INVALID_STATE_TRANSITION = "E4001"
    SESSION_NOT_ACTIVE = "E4002"
    SESSION_ALREADY_ACTIVE = "E4003"
    SESSION_NOT_FOUND = "E4004"
    PROFILE_NOT_FOUND = "E4005"
    UNDO_HISTORY_EMPTY = "E4006"
    SESSION_ENDED = "E4007"


# Error messages mapping
ERROR_MESSAGES: dict[str, tuple[str, bool]] = {
    # Code: (message, recoverable)
    ErrorCode.INVALID_MESSAGE_FORMAT: ("Invalid message format", False),
    ErrorCode.INVALID_MESSAGE_TYPE: ("Unknown message type", False),
    ErrorCode.MISSING_REQUIRED_FIELD: ("Missing required field", False),
    ErrorCode.INVALID_FIELD_VALUE: ("Invalid value for field", False),
    ErrorCode.FIELD_OUT_OF_RANGE: ("Field out of valid range", False),
    ErrorCode.INVALID_UUID: ("Invalid UUID format", False),
    ErrorCode.INVALID_TIMESTAMP: ("Invalid timestamp format", False),
    ErrorCode.PAYLOAD_TOO_LARGE: ("Message payload exceeds maximum size", False),
    ErrorCode.INVALID_STATE_TRANSITION: ("Invalid state transition", False),
    ErrorCode.SESSION_NOT_ACTIVE: ("No active session", False),
    ErrorCode.SESSION_ALREADY_ACTIVE: ("Session already active", False),
    ErrorCode.SESSION_NOT_FOUND: ("Session not found", False),
    ErrorCode.PROFILE_NOT_FOUND: ("Profile not found", False),
    ErrorCode.UNDO_HISTORY_EMPTY: ("No actions to undo", False),
    ErrorCode.SESSION_ENDED: ("Session has already ended", False),
}


# Valid client message types
VALID_CLIENT_TYPES = {
    "set_field",
    "set_difficulty",
    "select_profile",
    "create_profile",
    "update_profile",
    "manual_input",
    "start_session",
    "end_session",
    "undo",
    "ping",
    "simulate_shot",
    # Recording
    "start_recording",
    "stop_recording",
    "get_recording_status",
    "add_annotation",
    "list_recordings",
    "get_recording",
    # Streaming
    "start_radar_stream",
    "stop_radar_stream",
}


# Required fields for each message type
MESSAGE_REQUIRED_FIELDS: dict[str, list[str]] = {
    "set_field": ["payload.fielders"],
    "set_difficulty": ["payload.difficulty"],
    "select_profile": ["payload.profile_id"],
    "create_profile": ["payload.name", "payload.batting_hand"],
    "update_profile": ["payload.profile_id"],
    "manual_input": ["payload.result"],
    "start_session": ["payload.profile_id"],
    "end_session": ["payload.session_id"],
    "undo": ["payload.session_id"],
    "ping": [],
    "simulate_shot": ["payload.exit_speed", "payload.horizontal_angle", "payload.vertical_angle", "payload.field_config"],
    # Recording
    "start_recording": ["payload.session_type"],
    "stop_recording": [],
    "get_recording_status": [],
    "list_recordings": [],
    "get_recording": ["payload.file"],
    # Streaming
    "start_radar_stream": [],
    "stop_radar_stream": [],
}


def create_error_response(
    code: ErrorCode,
    in_reply_to: Optional[str] = None,
    details: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Create a standardized error response message.

    Args:
        code: The error code
        in_reply_to: Optional message ID this is responding to
        details: Optional additional error details

    Returns:
        Error message dictionary ready for JSON serialization
    """
    message, recoverable = ERROR_MESSAGES.get(
        code, ("Unknown error", False)
    )

    return {
        "type": "error",
        "message_id": generate_message_id(),
        "timestamp": create_timestamp(),
        "in_reply_to": in_reply_to,
        "payload": {
            "code": code.value if isinstance(code, ErrorCode) else code,
            "message": message,
            "details": details,
            "recoverable": recoverable,
        }
    }


@dataclass
class ValidationResult:
    """Result of message validation."""
    valid: bool
    error: Optional[dict[str, Any]] = None
    parsed_message: Optional[dict[str, Any]] = None


# Upper bound on fielders in one field. The UI sends 10; a custom field
# could carry a few more. Anything beyond this is not a field.
MAX_FIELDERS = 50
# The engine's own documented input ranges (engine/game_engine.py,
# CLAUDE.md). The engine clamps out-of-range values itself; the router
# only rejects values that are not numbers at all.
SIMULATE_DIFFICULTIES = ("easy", "medium", "hard")


def _is_number(value: Any) -> bool:
    """A finite int/float. bool is an int subclass and is rejected: JSON
    `true` must not silently become exit_speed=1. An int too large for a
    float makes math.isfinite raise; it is not a usable number either."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _is_fielder(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and _is_number(value.get("x"))
        and _is_number(value.get("y"))
        and (value.get("name") is None or isinstance(value.get("name"), str))
    )


def _is_id(value: Any) -> bool:
    """profile_id / session_id: a string or an int (not bool, not None)."""
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


class MessageRouter:
    """
    Routes incoming WebSocket messages to appropriate handlers.

    Responsibilities:
    - Parse incoming JSON messages
    - Validate message schema
    - Route to registered handlers by type
    - Return errors for invalid messages
    - Log all messages for debugging
    """

    def __init__(self) -> None:
        # Map message type -> handler function
        self._handlers: dict[str, MessageHandler] = {}

        # Maximum payload size (64KB as per error_codes.md)
        self._max_payload_size = 64 * 1024

        logger.info("MessageRouter initialized")

    def register_handler(
        self,
        message_type: str,
        handler: MessageHandler,
    ) -> None:
        """
        Register a handler for a message type.

        Args:
            message_type: The type field value to handle
            handler: Async function to handle the message
        """
        if message_type in self._handlers:
            logger.warning(f"Overwriting handler for type: {message_type}")

        self._handlers[message_type] = handler
        logger.debug(f"Registered handler for: {message_type}")

    def unregister_handler(self, message_type: str) -> bool:
        """
        Remove a handler for a message type.

        Args:
            message_type: The type to remove

        Returns:
            True if handler was removed, False if not found
        """
        if message_type in self._handlers:
            del self._handlers[message_type]
            return True
        return False

    def validate_message(self, raw_message: str) -> ValidationResult:
        """
        Validate an incoming message.

        Args:
            raw_message: The raw JSON string

        Returns:
            ValidationResult with success/failure and parsed data or error
        """
        # Check payload size
        if len(raw_message) > self._max_payload_size:
            logger.warning(f"Message too large: {len(raw_message)} bytes")
            return ValidationResult(
                valid=False,
                error=create_error_response(ErrorCode.PAYLOAD_TOO_LARGE),
            )

        # Parse JSON
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON: {e}")
            return ValidationResult(
                valid=False,
                error=create_error_response(
                    ErrorCode.INVALID_MESSAGE_FORMAT,
                    details={"parse_error": str(e)},
                ),
            )

        # Must be an object
        if not isinstance(message, dict):
            logger.warning(f"Message is not an object: {type(message)}")
            return ValidationResult(
                valid=False,
                error=create_error_response(ErrorCode.INVALID_MESSAGE_FORMAT),
            )

        # Extract message_id for error responses
        message_id = message.get("message_id")

        # Check required base fields
        if "type" not in message:
            return ValidationResult(
                valid=False,
                error=create_error_response(
                    ErrorCode.MISSING_REQUIRED_FIELD,
                    in_reply_to=message_id,
                    details={"field": "type"},
                ),
            )

        msg_type = message.get("type")

        # Validate message type (a list/dict here would make the membership
        # test itself raise TypeError)
        if not isinstance(msg_type, str) or msg_type not in VALID_CLIENT_TYPES:
            logger.warning(f"Unknown message type: {msg_type}")
            return ValidationResult(
                valid=False,
                error=create_error_response(
                    ErrorCode.INVALID_MESSAGE_TYPE,
                    in_reply_to=message_id,
                    details={"type": _describe(msg_type)},
                ),
            )

        # Check message_id (required for all messages)
        if "message_id" not in message:
            return ValidationResult(
                valid=False,
                error=create_error_response(
                    ErrorCode.MISSING_REQUIRED_FIELD,
                    details={"field": "message_id"},
                ),
            )

        # Validate UUID format (basic check)
        message_id = message["message_id"]
        if not self._is_valid_uuid(message_id):
            return ValidationResult(
                valid=False,
                error=create_error_response(
                    ErrorCode.INVALID_UUID,
                    in_reply_to=message_id,
                    details={"field": "message_id"},
                ),
            )

        # Check timestamp (required for all messages)
        if "timestamp" not in message:
            return ValidationResult(
                valid=False,
                error=create_error_response(
                    ErrorCode.MISSING_REQUIRED_FIELD,
                    in_reply_to=message_id,
                    details={"field": "timestamp"},
                ),
            )

        # Validate timestamp format (basic ISO 8601 check)
        timestamp = message["timestamp"]
        if not self._is_valid_timestamp(timestamp):
            return ValidationResult(
                valid=False,
                error=create_error_response(
                    ErrorCode.INVALID_TIMESTAMP,
                    in_reply_to=message_id,
                    details={"field": "timestamp"},
                ),
            )

        # The payload, when present, must be an object. Every check below and
        # every handler does `payload.get(...)`; a JSON array or string here
        # raised AttributeError straight through the connection handler.
        if "payload" in message and not isinstance(message["payload"], dict):
            return ValidationResult(
                valid=False,
                error=create_error_response(
                    ErrorCode.INVALID_MESSAGE_FORMAT,
                    in_reply_to=message_id,
                    details={"field": "payload", "reason": "must be an object"},
                ),
            )

        # Check type-specific required fields
        required_fields = MESSAGE_REQUIRED_FIELDS.get(msg_type, [])
        for field_path in required_fields:
            if not self._has_field(message, field_path):
                return ValidationResult(
                    valid=False,
                    error=create_error_response(
                        ErrorCode.MISSING_REQUIRED_FIELD,
                        in_reply_to=message_id,
                        details={"field": field_path},
                    ),
                )

        # Type-specific validation
        validation_error = self._validate_type_specific(message, message_id)
        if validation_error:
            return ValidationResult(valid=False, error=validation_error)

        logger.debug(f"Message validated: type={msg_type}, id={message_id[:8]}")

        return ValidationResult(valid=True, parsed_message=message)

    def _validate_type_specific(
        self,
        message: dict[str, Any],
        message_id: str,
    ) -> Optional[dict[str, Any]]:
        """Validate type-specific fields.

        Every comparison here is preceded by a type check. These used to do
        bare `len()` / `<` on untrusted values, so `boundary_distance: null`
        or `fielders: 3` raised TypeError out of the router, through the
        connection handler's catch-all, into a `finally` that marked the
        session complete - a malformed payload force-ended the innings.
        """
        msg_type = message["type"]
        payload = message.get("payload", {})

        def invalid(field: str, value: Any) -> dict[str, Any]:
            return create_error_response(
                ErrorCode.INVALID_FIELD_VALUE,
                in_reply_to=message_id,
                details={"field": field, "value": _describe(value)},
            )

        def out_of_range(field: str, lo: Any, hi: Any) -> dict[str, Any]:
            return create_error_response(
                ErrorCode.FIELD_OUT_OF_RANGE,
                in_reply_to=message_id,
                details={"field": field, "min": lo, "max": hi},
            )

        if msg_type == "set_difficulty":
            difficulty = payload.get("difficulty")
            if difficulty not in SIMULATE_DIFFICULTIES:
                return invalid("difficulty", difficulty)

        elif msg_type == "manual_input":
            result = payload.get("result")
            valid_results = ["dot", "1", "2", "3", "4", "6", "W", "wd", "nb"]
            if result not in valid_results:
                return invalid("result", result)

        elif msg_type in ("create_profile", "update_profile"):
            batting_hand = payload.get("batting_hand")
            if msg_type == "create_profile" or batting_hand is not None:
                if batting_hand not in ["right", "left"]:
                    return invalid("batting_hand", batting_hand)

            name = payload.get("name")
            if msg_type == "create_profile" or name is not None:
                if not isinstance(name, str):
                    return invalid("name", name)
                if not name.strip() or len(name) > 100:
                    return out_of_range("name", 1, 100)

            if msg_type == "update_profile" and not _is_id(payload.get("profile_id")):
                return invalid("profile_id", payload.get("profile_id"))

        elif msg_type in ("select_profile", "start_session"):
            if not _is_id(payload.get("profile_id")):
                return invalid("profile_id", payload.get("profile_id"))
            if msg_type == "start_session":
                fc = payload.get("field_config")
                if fc is not None and not (isinstance(fc, list) and len(fc) <= MAX_FIELDERS and all(_is_fielder(f) for f in fc)):
                    return invalid("field_config", fc)
                diff = payload.get("difficulty")
                if diff is not None and diff not in SIMULATE_DIFFICULTIES:
                    return invalid("difficulty", diff)

        elif msg_type in ("end_session", "undo"):
            if not _is_id(payload.get("session_id")):
                return invalid("session_id", payload.get("session_id"))

        elif msg_type == "set_field":
            fielders = payload.get("fielders")
            if not isinstance(fielders, list) or not all(_is_fielder(f) for f in fielders):
                return invalid("fielders", fielders)
            if not fielders or len(fielders) > 11:
                return out_of_range("fielders", 1, 11)

            boundary = payload.get("boundary_distance", 70)
            if not _is_number(boundary):
                return invalid("boundary_distance", boundary)
            if boundary < 50 or boundary > 100:
                return out_of_range("boundary_distance", 50, 100)

        elif msg_type == "simulate_shot":
            # The live path. The engine clamps ranges itself (speed 0-200,
            # elevation 0-90, angle normalised) and documents that; what it
            # cannot survive is a non-number, which used to reach
            # _calculate_trajectory as a str/None/bool and raise (or, for a
            # bool, silently simulate exit_speed=1).
            for field in ("exit_speed", "horizontal_angle", "vertical_angle"):
                if not _is_number(payload.get(field)):
                    return invalid(field, payload.get(field))
            fc = payload.get("field_config")
            if not isinstance(fc, list) or len(fc) > MAX_FIELDERS or not all(_is_fielder(f) for f in fc):
                return invalid("field_config", fc)
            boundary = payload.get("boundary_distance")
            if boundary is not None and (not _is_number(boundary) or boundary <= 0):
                return invalid("boundary_distance", boundary)
            diff = payload.get("difficulty")
            if diff is not None and diff not in SIMULATE_DIFFICULTIES:
                return invalid("difficulty", diff)
            seed = payload.get("seed")
            if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
                return invalid("seed", seed)

        elif msg_type == "get_recording":
            # The path is resolved inside recordings/ by the recorder; here
            # just insist it is a plausible string.
            f = payload.get("file")
            if not isinstance(f, str) or not f.strip() or len(f) > 500 or "\x00" in f:
                return invalid("file", f)

        elif msg_type == "list_recordings":
            st = payload.get("session_type")
            if st is not None and not isinstance(st, str):
                return invalid("session_type", st)

        elif msg_type == "add_annotation":
            # Stored verbatim as ground truth; keep it a small flat object.
            if len(payload) > 32:
                return out_of_range("payload", 0, 32)

        elif msg_type == "start_recording":
            from radar.recorder import SESSION_TYPES
            session_type = payload.get("session_type")
            if not isinstance(session_type, str) or session_type not in SESSION_TYPES:
                return invalid("session_type", session_type)

            # Finite number only: NaN silently became a 1-second recording
            # (max(1, min(nan, 7200)) is 1), 10**400 raised OverflowError.
            max_duration = payload.get("max_duration")
            if max_duration is not None and not _is_number(max_duration):
                return invalid("max_duration", max_duration)

        return None

    def _has_field(self, message: dict[str, Any], field_path: str) -> bool:
        """Check if a nested field exists (e.g., 'payload.profile_id')."""
        parts = field_path.split(".")
        current = message

        for part in parts:
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]

        return current is not None

    def _is_valid_uuid(self, value: str) -> bool:
        """Basic UUID format validation."""
        if not isinstance(value, str):
            return False

        # UUID v4 format: xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
        parts = value.split("-")
        if len(parts) != 5:
            return False

        expected_lengths = [8, 4, 4, 4, 12]
        for part, expected_len in zip(parts, expected_lengths):
            if len(part) != expected_len:
                return False
            try:
                int(part, 16)
            except ValueError:
                return False

        return True

    def _is_valid_timestamp(self, value: str) -> bool:
        """Basic ISO 8601 timestamp validation."""
        if not isinstance(value, str):
            return False

        # Try parsing common ISO 8601 formats
        try:
            # Handle 'Z' suffix
            if value.endswith("Z"):
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                datetime.fromisoformat(value)
            return True
        except ValueError:
            return False

    async def route(
        self,
        client_id: str,
        raw_message: str,
    ) -> Optional[dict[str, Any]]:
        """
        Route a message to its handler.

        Args:
            client_id: The client that sent the message
            raw_message: The raw JSON string

        Returns:
            Response message to send back, or None
        """
        # Log incoming message
        logger.debug(f"Received from {client_id}: {raw_message[:200]}...")

        # Validate. Validation itself must never escape: an exception here
        # used to propagate out of the connection's `async for`, close the
        # socket with code 1000 and auto-complete the session. A message we
        # cannot parse is answered with an error frame, like any other.
        try:
            result = self.validate_message(raw_message)
        except Exception as e:  # defensive: validation is over untrusted input
            logger.exception(f"Validation crashed on message from {client_id}: {e}")
            return create_error_response(
                ErrorCode.INVALID_MESSAGE_FORMAT,
                details={"validation_error": type(e).__name__},
            )

        if not result.valid:
            logger.warning(
                f"Invalid message from {client_id}: "
                f"{result.error.get('payload', {}).get('code')}"
            )
            return result.error

        message = result.parsed_message
        msg_type = message["type"]

        # Log validated message
        logger.info(
            f"[{client_id[:8]}] -> {msg_type} "
            f"(id={message['message_id'][:8]})"
        )

        # Find handler
        handler = self._handlers.get(msg_type)

        if not handler:
            logger.warning(f"No handler registered for: {msg_type}")
            return create_error_response(
                ErrorCode.INVALID_MESSAGE_TYPE,
                in_reply_to=message.get("message_id"),
                details={"type": msg_type, "reason": "no handler registered"},
            )

        # Call handler
        try:
            response = await handler(client_id, message)

            if response:
                logger.info(
                    f"[{client_id[:8]}] <- {response.get('type')} "
                    f"(id={response.get('message_id', 'N/A')[:8]})"
                )

            return response

        except Exception as e:
            logger.exception(f"Handler error for {msg_type}: {e}")
            return create_error_response(
                ErrorCode.INVALID_MESSAGE_FORMAT,
                in_reply_to=message.get("message_id"),
                details={"handler_error": str(e)},
            )

    def get_registered_types(self) -> list[str]:
        """Get list of registered message types."""
        return list(self._handlers.keys())


def _describe(value: Any) -> Any:
    """A JSON-safe, bounded description of a rejected value for the error
    frame - never echo a large or unserialisable payload back. Non-finite
    floats become strings: json.dumps would emit a bare NaN, which the
    browser's JSON.parse rejects, so the client would never see the error."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, int):
        return value if abs(value) < 10 ** 15 else f"{str(value)[:20]}..."
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return value if len(value) <= 100 else value[:100] + "..."
    return f"<{type(value).__name__}>"
