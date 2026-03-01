/**
 * Cricket App API Types
 *
 * TypeScript interfaces for WebSocket message payloads.
 * THESE TYPES ARE LAW - all implementations must conform exactly.
 *
 * @version 1.0.0
 */

// =============================================================================
// PRIMITIVE TYPES
// =============================================================================

export type ShotOutcome =
  | "dot"
  | "1"
  | "2"
  | "3"
  | "4"
  | "6"
  | "caught"
  | "dropped"
  | "misfield";

export type BallResult =
  | "dot"
  | "1"
  | "2"
  | "3"
  | "4"
  | "6"
  | "W"
  | "wd"
  | "nb";

export type Difficulty = "easy" | "medium" | "hard";

export type BattingHand = "right" | "left";

export type ConnectionState =
  | "disconnected"
  | "connecting"
  | "connected"
  | "reconnecting";

export type SessionState = "none" | "active" | "ended";

export type TrackingState = "idle" | "detecting" | "tracking" | "completed";

export type CatchType = "regulation" | "hard" | "spectacular";

// =============================================================================
// DATA STRUCTURES
// =============================================================================

export interface Position {
  x: number; // metres from batter
  y: number; // metres from batter (positive = toward bowler)
}

export interface FielderConfig {
  id?: string; // optional - UI can generate for drag tracking, game engine doesn't require
  x: number; // screen X position (0-100%)
  y: number; // screen Y position (0-100%)
  name: string; // zone name e.g. 'cover', 'mid-off'
}

export interface TrajectoryData {
  projected_distance: number; // total distance in metres (aerial + rolling)
  aerial_distance: number; // distance traveled through air
  rolling_distance: number; // distance ball rolls after landing
  max_height: number; // peak height in metres
  landing_x: number; // X landing coordinate
  landing_y: number; // Y landing coordinate
  final_x: number; // X where ball stops
  final_y: number; // Y where ball stops
  time_of_flight: number; // seconds in air
  horizontal_speed: number; // m/s along ground
  vertical_speed: number; // m/s initial vertical
  direction_x: number; // unit vector X component
  direction_y: number; // unit vector Y component
}

export interface CatchAnalysis {
  canCatch: boolean;
  difficulty: number; // 0-1, higher = harder
  catchType: CatchType | null;
  reactionTime: number; // seconds available to react
  movementRequired: number; // metres fielder must move
  movementPossible: number; // metres fielder can move given time
  ballSpeedAtFielder: number; // km/h
  heightAtIntercept: number; // metres
  timeToIntercept: number; // seconds until ball reaches intercept point
  fielderArrivalTime: number; // seconds for fielder to reach intercept point
  arrivedBeforeLanding: boolean; // did fielder get there before ball landed?
}

export interface SimulationResult {
  outcome: ShotOutcome;
  runs: number; // 0-6
  is_boundary: boolean;
  is_aerial: boolean;
  fielder_involved: string | null;
  fielder_position: Position | null; // where fielder started
  fielding_position: Position | null; // where fielder collected ball
  end_position: Position; // where ball ended up
  description: string;
  trajectory: TrajectoryData | null;
  catch_analysis: CatchAnalysis | null;
  fielding_time: number | null; // total time from bat to stumps
  collection_difficulty: number | null; // 0-1, how rushed was fielder
  alignment_score: number | null; // 0-1, how direct ball path was
  priority_score: number | null; // combined weighted score
  fielder_arrival_time: number | null;
  ball_arrival_time: number | null;
}

// Slim payload for real-time WebSocket display (5 fields only).
// Full SimulationResult is stored in database. Use REST API GET /api/sessions/:id/deliveries for detailed data.
export interface SlimShotResult {
  outcome: ShotOutcome;
  runs: number;
  description: string;
  end_position: Position; // For wagon wheel
  fielder_involved: string | null;
}

export interface RadarTrackingData {
  timestamp: string; // ISO 8601
  exit_speed: number; // km/h
  horizontal_angle: number; // degrees from straight (-180 to 180)
  vertical_angle: number; // degrees from horizontal (0 to 90)
  projected_distance: number;
  max_height: number;
  landing_x: number;
  landing_y: number;
  frames_captured: number;
  detection_confidence: number; // 0-1
}

export interface Over {
  balls: BallResult[];
  runs: number;
}

export interface SessionData {
  id: string;
  player_id: string;
  date: string; // ISO 8601
  runs: number;
  balls: number;
  fours: number;
  sixes: number;
  wickets: number;
  is_out: boolean;
  overs: Over[];
  strike_rate: number;
  field_config: FielderConfig[]; // sessions always have a field config
}

export interface Profile {
  id: string;
  name: string;
  batting_hand: BattingHand;
}

export interface ErrorPayload {
  code: string; // error code from error_codes.md
  message: string;
  details: Record<string, unknown> | null;
  recoverable: boolean;
}

// =============================================================================
// WAGON WHEEL
// =============================================================================

export interface WagonWheelShot {
  id: string;
  end_x: number; // screen X % (0-100)
  end_y: number; // screen Y % (0-100)
  outcome: BallResult;
  distance: number; // metres
}

// =============================================================================
// CLIENT -> SERVER MESSAGES
// =============================================================================

interface BaseClientMessage {
  message_id: string; // UUID v4
  timestamp: string; // ISO 8601
}

export interface SetFieldMessage extends BaseClientMessage {
  type: "set_field";
  payload: {
    fielders: FielderConfig[]; // 1-11 fielders
    boundary_distance?: number; // 50-100, default 70
  };
}

export interface SetDifficultyMessage extends BaseClientMessage {
  type: "set_difficulty";
  payload: {
    difficulty: Difficulty;
  };
}

export interface SelectProfileMessage extends BaseClientMessage {
  type: "select_profile";
  payload: {
    profile_id: string;
  };
}

export interface CreateProfileMessage extends BaseClientMessage {
  type: "create_profile";
  payload: {
    name: string; // 1-100 chars
    batting_hand: BattingHand;
  };
}

export interface ManualInputMessage extends BaseClientMessage {
  type: "manual_input";
  payload: {
    result: BallResult;
    is_boundary?: boolean;
  };
}

export interface StartSessionMessage extends BaseClientMessage {
  type: "start_session";
  payload: {
    profile_id: string;
    field_config?: FielderConfig[];
    difficulty?: Difficulty;
    notes?: string; // max 500 chars
  };
}

export interface EndSessionMessage extends BaseClientMessage {
  type: "end_session";
  payload: {
    session_id: string;
    save_to_database?: boolean; // default true
  };
}

export interface UndoMessage extends BaseClientMessage {
  type: "undo";
  payload: {
    session_id: string;
  };
}

export interface UpdateProfileMessage extends BaseClientMessage {
  type: "update_profile";
  payload: {
    profile_id: string;
    name?: string; // 1-100 chars
    batting_hand?: BattingHand;
  };
}

export interface PingMessage extends BaseClientMessage {
  type: "ping";
}

export interface SimulateShotMessage extends BaseClientMessage {
  type: "simulate_shot";
  payload: {
    exit_speed: number; // km/h
    horizontal_angle: number; // degrees (0-360)
    vertical_angle: number; // degrees (0-90)
    field_config: FielderConfig[];
    boundary_distance?: number; // default 70
    difficulty?: Difficulty; // default 'medium'
  };
}

export type ClientMessage =
  | SetFieldMessage
  | SetDifficultyMessage
  | SelectProfileMessage
  | CreateProfileMessage
  | UpdateProfileMessage
  | ManualInputMessage
  | StartSessionMessage
  | EndSessionMessage
  | UndoMessage
  | SimulateShotMessage
  | PingMessage;

// =============================================================================
// SERVER -> CLIENT MESSAGES
// =============================================================================

interface BaseServerMessage {
  message_id: string; // UUID v4
  timestamp: string; // ISO 8601
  in_reply_to?: string | null; // UUID of client message this replies to
}

export interface ShotResultMessage extends BaseServerMessage {
  type: "shot_result";
  payload: {
    session_id: string;
    ball_number: number;
    radar_data: RadarTrackingData | null;
    simulation: SlimShotResult; // Slim payload for WebSocket. Full data via REST API.
  };
}

export interface SessionStateMessage extends BaseServerMessage {
  type: "session_state";
  payload: {
    session: SessionData | null;
    profiles: Profile[];
    active_profile_id: string | null;
    difficulty: Difficulty;
    field_config: FielderConfig[];
    boundary_distance: number;
  };
}

export interface WagonWheelUpdateMessage extends BaseServerMessage {
  type: "wagon_wheel_update";
  payload: {
    session_id: string;
    shot: WagonWheelShot;
  };
}

export interface BallTrackingMessage extends BaseServerMessage {
  type: "ball_tracking";
  payload: {
    tracking_state: TrackingState;
    current_position?: Position | null;
    current_speed?: number | null;
    frames_captured?: number;
  };
}

export interface ConnectionStatusMessage extends BaseServerMessage {
  type: "connection_status";
  payload: {
    connection_state: ConnectionState;
    session_state: SessionState;
    radar_connected: boolean;
    radar_status?: string | null;
    server_version: string;
    uptime_seconds?: number;
  };
}

export interface ErrorMessage extends BaseServerMessage {
  type: "error";
  payload: ErrorPayload;
}

export interface PongMessage extends BaseServerMessage {
  type: "pong";
  in_reply_to: string; // UUID of the ping message (required for pong)
}

export interface SimulateResultMessage extends BaseServerMessage {
  type: "simulate_result";
  payload: {
    simulation: SimulationResult;
  };
}

export type ServerMessage =
  | ShotResultMessage
  | SessionStateMessage
  | WagonWheelUpdateMessage
  | BallTrackingMessage
  | ConnectionStatusMessage
  | ErrorMessage
  | SimulateResultMessage
  | PongMessage;

// =============================================================================
// UTILITY TYPES
// =============================================================================

/** Union of all message types for runtime type guards */
export type WebSocketMessage = ClientMessage | ServerMessage;

/** Extract message type from a message */
export type MessageType<T extends WebSocketMessage> = T["type"];

/** Type guard helpers */
export function isClientMessage(msg: WebSocketMessage): msg is ClientMessage {
  const clientTypes = [
    "set_field",
    "set_difficulty",
    "select_profile",
    "create_profile",
    "update_profile",
    "manual_input",
    "start_session",
    "end_session",
    "undo",
    "simulate_shot",
    "ping",
  ];
  return clientTypes.includes(msg.type);
}

export function isServerMessage(msg: WebSocketMessage): msg is ServerMessage {
  const serverTypes = [
    "shot_result",
    "session_state",
    "wagon_wheel_update",
    "ball_tracking",
    "connection_status",
    "error",
    "simulate_result",
    "pong",
  ];
  return serverTypes.includes(msg.type);
}

/** Generate a UUID v4 */
export function generateMessageId(): string {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/** Create ISO 8601 timestamp */
export function createTimestamp(): string {
  return new Date().toISOString();
}
