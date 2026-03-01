"""
Cricket App API Types

Python dataclasses that EXACTLY mirror the TypeScript types in api_types.ts.
THESE TYPES ARE LAW - all implementations must conform exactly.

Version: 1.0.0
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional, Union


# =============================================================================
# ENUMS (Literal types in TypeScript)
# =============================================================================

class ShotOutcome(str, Enum):
    DOT = "dot"
    ONE = "1"
    TWO = "2"
    THREE = "3"
    FOUR = "4"
    SIX = "6"
    CAUGHT = "caught"
    DROPPED = "dropped"
    MISFIELD = "misfield"


class BallResult(str, Enum):
    DOT = "dot"
    ONE = "1"
    TWO = "2"
    THREE = "3"
    FOUR = "4"
    SIX = "6"
    WICKET = "W"
    WIDE = "wd"
    NO_BALL = "nb"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class BattingHand(str, Enum):
    RIGHT = "right"
    LEFT = "left"


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class SessionState(str, Enum):
    NONE = "none"
    ACTIVE = "active"
    ENDED = "ended"


class TrackingState(str, Enum):
    IDLE = "idle"
    DETECTING = "detecting"
    TRACKING = "tracking"
    COMPLETED = "completed"


class CatchType(str, Enum):
    REGULATION = "regulation"
    HARD = "hard"
    SPECTACULAR = "spectacular"


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass(frozen=True)
class Position:
    """Position in field coordinates (metres from batter)."""
    x: float  # metres from batter
    y: float  # metres from batter (positive = toward bowler)


@dataclass(frozen=True)
class FielderConfig:
    """Fielder position configuration.

    Note: id is optional - UI can generate for drag tracking,
    but the game engine doesn't require it.
    """
    x: float  # screen X position (0-100%)
    y: float  # screen Y position (0-100%)
    name: str  # zone name e.g. 'cover', 'mid-off'
    id: Optional[str] = None


@dataclass(frozen=True)
class TrajectoryData:
    """Complete ball trajectory data."""
    projected_distance: float  # total distance in metres (aerial + rolling)
    aerial_distance: float     # distance traveled through air
    rolling_distance: float    # distance ball rolls after landing
    max_height: float          # peak height in metres
    landing_x: float           # X landing coordinate
    landing_y: float           # Y landing coordinate
    final_x: float             # X where ball stops
    final_y: float             # Y where ball stops
    time_of_flight: float      # seconds in air
    horizontal_speed: float    # m/s along ground
    vertical_speed: float      # m/s initial vertical
    direction_x: float         # unit vector X component
    direction_y: float         # unit vector Y component


@dataclass(frozen=True)
class CatchAnalysis:
    """Detailed catch difficulty breakdown.

    Note: Python uses snake_case internally, but JSON wire format uses camelCase
    to match TypeScript conventions. Serialization layer must convert:
    - can_catch -> canCatch
    - catch_type -> catchType
    - reaction_time -> reactionTime
    etc.
    """
    can_catch: bool
    difficulty: float          # 0-1, higher = harder
    catch_type: Optional[CatchType]
    reaction_time: float       # seconds available to react
    movement_required: float   # metres fielder must move
    movement_possible: float   # metres fielder can move given time
    ball_speed_at_fielder: float  # km/h
    height_at_intercept: float    # metres
    time_to_intercept: float      # seconds until ball reaches intercept point
    fielder_arrival_time: float   # seconds for fielder to reach intercept point
    arrived_before_landing: bool  # did fielder get there before ball landed?


@dataclass
class SimulationResult:
    """Complete simulation outcome."""
    outcome: ShotOutcome
    runs: int  # 0-6
    is_boundary: bool
    is_aerial: bool
    end_position: Position
    description: str
    fielder_involved: Optional[str] = None
    fielder_position: Optional[Position] = None  # where fielder started
    fielding_position: Optional[Position] = None  # where fielder collected ball
    trajectory: Optional[TrajectoryData] = None
    catch_analysis: Optional[CatchAnalysis] = None
    fielding_time: Optional[float] = None  # total time from bat to stumps
    collection_difficulty: Optional[float] = None  # 0-1, how rushed was fielder
    alignment_score: Optional[float] = None  # 0-1, how direct ball path was
    priority_score: Optional[float] = None  # combined weighted score
    fielder_arrival_time: Optional[float] = None
    ball_arrival_time: Optional[float] = None


@dataclass(frozen=True)
class SlimShotResult:
    """Minimal shot data for real-time WebSocket display.
    Full SimulationResult stored in database - use REST API GET /api/sessions/:id/deliveries for details."""
    outcome: ShotOutcome
    runs: int
    description: str
    end_position: Position  # For wagon wheel
    fielder_involved: Optional[str]


@dataclass(frozen=True)
class RadarTrackingData:
    """Data captured from radar during ball tracking."""
    timestamp: str  # ISO 8601
    exit_speed: float  # km/h
    horizontal_angle: float  # degrees from straight (-180 to 180)
    vertical_angle: float  # degrees from horizontal (0 to 90)
    projected_distance: float
    max_height: float
    landing_x: float
    landing_y: float
    frames_captured: int
    detection_confidence: float  # 0-1


@dataclass
class Over:
    """A single over of deliveries."""
    balls: list[BallResult]
    runs: int


@dataclass
class SessionData:
    """Complete session data."""
    id: str
    player_id: str
    date: str  # ISO 8601
    runs: int
    balls: int
    fours: int
    sixes: int
    wickets: int
    is_out: bool
    overs: list[Over]
    strike_rate: float
    field_config: list[FielderConfig]  # sessions always have a field config


@dataclass(frozen=True)
class Profile:
    """Player profile."""
    id: str
    name: str
    batting_hand: BattingHand


@dataclass(frozen=True)
class ErrorPayload:
    """Error information."""
    code: str  # error code from error_codes.md
    message: str
    recoverable: bool
    details: Optional[dict] = None


@dataclass(frozen=True)
class WagonWheelShot:
    """A shot displayed on the wagon wheel."""
    id: str
    end_x: float  # screen X % (0-100)
    end_y: float  # screen Y % (0-100)
    outcome: BallResult
    distance: float  # metres


# =============================================================================
# CLIENT -> SERVER MESSAGES
# =============================================================================

@dataclass
class BaseClientMessage:
    """Base class for client messages."""
    message_id: str  # UUID v4
    timestamp: str   # ISO 8601


@dataclass
class SetFieldPayload:
    fielders: list[FielderConfig]  # 1-11 fielders
    boundary_distance: float = 70.0  # 50-100


@dataclass
class SetFieldMessage(BaseClientMessage):
    payload: SetFieldPayload
    type: Literal["set_field"] = "set_field"


@dataclass
class SetDifficultyPayload:
    difficulty: Difficulty


@dataclass
class SetDifficultyMessage(BaseClientMessage):
    payload: SetDifficultyPayload
    type: Literal["set_difficulty"] = "set_difficulty"


@dataclass
class SelectProfilePayload:
    profile_id: str


@dataclass
class SelectProfileMessage(BaseClientMessage):
    payload: SelectProfilePayload
    type: Literal["select_profile"] = "select_profile"


@dataclass
class CreateProfilePayload:
    name: str  # 1-100 chars
    batting_hand: BattingHand


@dataclass
class CreateProfileMessage(BaseClientMessage):
    payload: CreateProfilePayload
    type: Literal["create_profile"] = "create_profile"


@dataclass
class ManualInputPayload:
    result: BallResult
    is_boundary: bool = False


@dataclass
class ManualInputMessage(BaseClientMessage):
    payload: ManualInputPayload
    type: Literal["manual_input"] = "manual_input"


@dataclass
class StartSessionPayload:
    profile_id: str
    field_config: Optional[list[FielderConfig]] = None
    difficulty: Optional[Difficulty] = None
    notes: Optional[str] = None  # max 500 chars


@dataclass
class StartSessionMessage(BaseClientMessage):
    payload: StartSessionPayload
    type: Literal["start_session"] = "start_session"


@dataclass
class EndSessionPayload:
    session_id: str
    save_to_database: bool = True


@dataclass
class EndSessionMessage(BaseClientMessage):
    payload: EndSessionPayload
    type: Literal["end_session"] = "end_session"


@dataclass
class UndoPayload:
    session_id: str


@dataclass
class UndoMessage(BaseClientMessage):
    payload: UndoPayload
    type: Literal["undo"] = "undo"


@dataclass
class UpdateProfilePayload:
    profile_id: str
    name: Optional[str] = None  # 1-100 chars
    batting_hand: Optional[BattingHand] = None


@dataclass
class UpdateProfileMessage(BaseClientMessage):
    payload: UpdateProfilePayload
    type: Literal["update_profile"] = "update_profile"


@dataclass
class PingMessage(BaseClientMessage):
    """Heartbeat ping from client to keep connection alive."""
    type: Literal["ping"] = "ping"


ClientMessage = Union[
    SetFieldMessage,
    SetDifficultyMessage,
    SelectProfileMessage,
    CreateProfileMessage,
    UpdateProfileMessage,
    ManualInputMessage,
    StartSessionMessage,
    EndSessionMessage,
    UndoMessage,
    PingMessage,
]


# =============================================================================
# SERVER -> CLIENT MESSAGES
# =============================================================================

@dataclass
class BaseServerMessage:
    """Base class for server messages."""
    message_id: str  # UUID v4
    timestamp: str   # ISO 8601
    in_reply_to: Optional[str] = None  # UUID of client message this replies to


@dataclass
class ShotResultPayload:
    """Shot result payload for WebSocket. Uses SlimShotResult for efficient transfer."""
    session_id: str
    ball_number: int
    simulation: SlimShotResult  # Slim payload for WebSocket. Full data via REST API.
    radar_data: Optional[RadarTrackingData] = None


@dataclass
class ShotResultMessage(BaseServerMessage):
    payload: ShotResultPayload
    type: Literal["shot_result"] = "shot_result"


@dataclass
class SessionStatePayload:
    session: Optional[SessionData]
    profiles: list[Profile]
    difficulty: Difficulty
    field_config: list[FielderConfig]
    boundary_distance: float
    active_profile_id: Optional[str] = None


@dataclass
class SessionStateMessage(BaseServerMessage):
    payload: SessionStatePayload
    type: Literal["session_state"] = "session_state"


@dataclass
class WagonWheelUpdatePayload:
    session_id: str
    shot: WagonWheelShot


@dataclass
class WagonWheelUpdateMessage(BaseServerMessage):
    payload: WagonWheelUpdatePayload
    type: Literal["wagon_wheel_update"] = "wagon_wheel_update"


@dataclass
class BallTrackingPayload:
    tracking_state: TrackingState
    current_position: Optional[Position] = None
    current_speed: Optional[float] = None
    frames_captured: int = 0


@dataclass
class BallTrackingMessage(BaseServerMessage):
    payload: BallTrackingPayload
    type: Literal["ball_tracking"] = "ball_tracking"


@dataclass
class ConnectionStatusPayload:
    connection_state: ConnectionState
    session_state: SessionState
    radar_connected: bool
    server_version: str
    radar_status: Optional[str] = None
    uptime_seconds: Optional[float] = None


@dataclass
class ConnectionStatusMessage(BaseServerMessage):
    payload: ConnectionStatusPayload
    type: Literal["connection_status"] = "connection_status"


@dataclass
class ErrorMessage(BaseServerMessage):
    payload: ErrorPayload
    type: Literal["error"] = "error"


@dataclass
class PongMessage(BaseServerMessage):
    """Heartbeat pong response from server."""
    in_reply_to: str  # Required for pong - UUID of the ping message
    type: Literal["pong"] = "pong"


ServerMessage = Union[
    ShotResultMessage,
    SessionStateMessage,
    WagonWheelUpdateMessage,
    BallTrackingMessage,
    ConnectionStatusMessage,
    ErrorMessage,
    PongMessage,
]

WebSocketMessage = Union[ClientMessage, ServerMessage]


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def generate_message_id() -> str:
    """Generate a UUID v4 for message IDs."""
    return str(uuid.uuid4())


def create_timestamp() -> str:
    """Create an ISO 8601 timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def is_client_message(msg: WebSocketMessage) -> bool:
    """Check if a message is a client message."""
    client_types = {
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
    }
    return msg.type in client_types


def is_server_message(msg: WebSocketMessage) -> bool:
    """Check if a message is a server message."""
    server_types = {
        "shot_result",
        "session_state",
        "wagon_wheel_update",
        "ball_tracking",
        "connection_status",
        "error",
        "pong",
    }
    return msg.type in server_types
