/**
 * Type Guard and Message Creation Tests
 *
 * Tests for type guards (isClientMessage, isServerMessage) and
 * message creation utilities.
 */

import { describe, it, expect } from "vitest";

import {
  // Types
  type ClientMessage,
  type WebSocketMessage,
  type SetFieldMessage,
  type SetDifficultyMessage,
  type SelectProfileMessage,
  type CreateProfileMessage,
  type ManualInputMessage,
  type StartSessionMessage,
  type EndSessionMessage,
  type UndoMessage,
  type UpdateProfileMessage,
  type PingMessage,
  type ShotResultMessage,
  type SessionStateMessage,
  type WagonWheelUpdateMessage,
  type BallTrackingMessage,
  type ConnectionStatusMessage,
  type ErrorMessage,
  type PongMessage,
  type FielderConfig,
  type Position,
  type SimulationResult,
  type Profile,
  // Enums
  type Difficulty,
  type BattingHand,
  type BallResult,
  type ShotOutcome,
  type ConnectionState,
  type SessionState,
  type TrackingState,
  // Utilities
  isClientMessage,
  isServerMessage,
  generateMessageId,
  createTimestamp,
} from "../../../contracts/api_types";

// =============================================================================
// TYPE GUARD TESTS
// =============================================================================

describe("isClientMessage", () => {
  const createBaseClientMessage = () => ({
    message_id: generateMessageId(),
    timestamp: createTimestamp(),
  });

  it("should return true for set_field message", () => {
    const message: SetFieldMessage = {
      ...createBaseClientMessage(),
      type: "set_field",
      payload: {
        fielders: [{ x: 50, y: 50, name: "cover" }],
        boundary_distance: 70,
      },
    };
    expect(isClientMessage(message)).toBe(true);
  });

  it("should return true for set_difficulty message", () => {
    const message: SetDifficultyMessage = {
      ...createBaseClientMessage(),
      type: "set_difficulty",
      payload: { difficulty: "medium" },
    };
    expect(isClientMessage(message)).toBe(true);
  });

  it("should return true for select_profile message", () => {
    const message: SelectProfileMessage = {
      ...createBaseClientMessage(),
      type: "select_profile",
      payload: { profile_id: "profile-1" },
    };
    expect(isClientMessage(message)).toBe(true);
  });

  it("should return true for create_profile message", () => {
    const message: CreateProfileMessage = {
      ...createBaseClientMessage(),
      type: "create_profile",
      payload: { name: "Test Player", batting_hand: "right" },
    };
    expect(isClientMessage(message)).toBe(true);
  });

  it("should return true for update_profile message", () => {
    const message: UpdateProfileMessage = {
      ...createBaseClientMessage(),
      type: "update_profile",
      payload: { profile_id: "p1", name: "New Name" },
    };
    expect(isClientMessage(message)).toBe(true);
  });

  it("should return true for manual_input message", () => {
    const message: ManualInputMessage = {
      ...createBaseClientMessage(),
      type: "manual_input",
      payload: { result: "4", is_boundary: true },
    };
    expect(isClientMessage(message)).toBe(true);
  });

  it("should return true for start_session message", () => {
    const message: StartSessionMessage = {
      ...createBaseClientMessage(),
      type: "start_session",
      payload: { profile_id: "profile-1" },
    };
    expect(isClientMessage(message)).toBe(true);
  });

  it("should return true for end_session message", () => {
    const message: EndSessionMessage = {
      ...createBaseClientMessage(),
      type: "end_session",
      payload: { session_id: "session-1" },
    };
    expect(isClientMessage(message)).toBe(true);
  });

  it("should return true for undo message", () => {
    const message: UndoMessage = {
      ...createBaseClientMessage(),
      type: "undo",
      payload: { session_id: "session-1" },
    };
    expect(isClientMessage(message)).toBe(true);
  });

  it("should return true for ping message", () => {
    const message: PingMessage = {
      ...createBaseClientMessage(),
      type: "ping",
    };
    expect(isClientMessage(message)).toBe(true);
  });

  it("should return false for server messages", () => {
    const message: PongMessage = {
      message_id: generateMessageId(),
      timestamp: createTimestamp(),
      type: "pong",
      in_reply_to: "ping-id",
    };
    expect(isClientMessage(message as WebSocketMessage)).toBe(false);
  });
});

describe("isServerMessage", () => {
  const createBaseServerMessage = () => ({
    message_id: generateMessageId(),
    timestamp: createTimestamp(),
    in_reply_to: null,
  });

  const createSampleSimulation = (): SimulationResult => ({
    outcome: "4" as ShotOutcome,
    runs: 4,
    is_boundary: true,
    is_aerial: false,
    fielder_involved: null,
    fielder_position: null,
    fielding_position: null,
    end_position: { x: 60, y: 70 },
    description: "Cover drive for four",
    trajectory: null,
    catch_analysis: null,
    fielding_time: null,
    collection_difficulty: null,
    alignment_score: null,
    priority_score: null,
    fielder_arrival_time: null,
    ball_arrival_time: null,
  });

  it("should return true for shot_result message", () => {
    const message: ShotResultMessage = {
      ...createBaseServerMessage(),
      type: "shot_result",
      payload: {
        session_id: "session-1",
        ball_number: 1,
        radar_data: null,
        simulation: createSampleSimulation(),
      },
    };
    expect(isServerMessage(message)).toBe(true);
  });

  it("should return true for session_state message", () => {
    const message: SessionStateMessage = {
      ...createBaseServerMessage(),
      type: "session_state",
      payload: {
        session: null,
        profiles: [],
        active_profile_id: null,
        difficulty: "medium",
        field_config: [],
        boundary_distance: 70,
      },
    };
    expect(isServerMessage(message)).toBe(true);
  });

  it("should return true for wagon_wheel_update message", () => {
    const message: WagonWheelUpdateMessage = {
      ...createBaseServerMessage(),
      type: "wagon_wheel_update",
      payload: {
        session_id: "session-1",
        shot: {
          id: "shot-1",
          end_x: 75,
          end_y: 80,
          outcome: "4",
          distance: 62,
        },
      },
    };
    expect(isServerMessage(message)).toBe(true);
  });

  it("should return true for ball_tracking message", () => {
    const message: BallTrackingMessage = {
      ...createBaseServerMessage(),
      type: "ball_tracking",
      payload: {
        tracking_state: "tracking",
        current_position: { x: 10, y: 15 },
        current_speed: 95,
        frames_captured: 20,
      },
    };
    expect(isServerMessage(message)).toBe(true);
  });

  it("should return true for connection_status message", () => {
    const message: ConnectionStatusMessage = {
      ...createBaseServerMessage(),
      type: "connection_status",
      payload: {
        connection_state: "connected",
        session_state: "none",
        radar_connected: true,
        server_version: "1.0.0",
        radar_status: "Ready",
        uptime_seconds: 3600,
      },
    };
    expect(isServerMessage(message)).toBe(true);
  });

  it("should return true for error message", () => {
    const message: ErrorMessage = {
      ...createBaseServerMessage(),
      type: "error",
      in_reply_to: "original-message-id",
      payload: {
        code: "E4002",
        message: "No active session",
        details: null,
        recoverable: false,
      },
    };
    expect(isServerMessage(message)).toBe(true);
  });

  it("should return true for pong message", () => {
    const message: PongMessage = {
      message_id: generateMessageId(),
      timestamp: createTimestamp(),
      type: "pong",
      in_reply_to: "ping-id",
    };
    expect(isServerMessage(message)).toBe(true);
  });

  it("should return false for client messages", () => {
    const message: PingMessage = {
      message_id: generateMessageId(),
      timestamp: createTimestamp(),
      type: "ping",
    };
    expect(isServerMessage(message as WebSocketMessage)).toBe(false);
  });
});

// =============================================================================
// UTILITY FUNCTION TESTS
// =============================================================================

describe("generateMessageId", () => {
  it("should generate a valid UUID v4 format string", () => {
    const id = generateMessageId();

    // UUID v4 format: xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
    const uuidRegex =
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
    expect(id).toMatch(uuidRegex);
  });

  it("should generate unique IDs", () => {
    const ids = new Set<string>();
    for (let i = 0; i < 100; i++) {
      ids.add(generateMessageId());
    }
    expect(ids.size).toBe(100);
  });

  it("should generate lowercase hex characters", () => {
    const id = generateMessageId();
    expect(id).toBe(id.toLowerCase());
  });
});

describe("createTimestamp", () => {
  it("should return a valid ISO 8601 timestamp", () => {
    const ts = createTimestamp();

    // Should be parseable by Date
    const date = new Date(ts);
    expect(date.toString()).not.toBe("Invalid Date");
  });

  it("should return timestamps in UTC (ends with Z or +00:00)", () => {
    const ts = createTimestamp();

    // Our implementation uses toISOString() which ends with Z
    expect(ts).toMatch(/Z$/);
  });

  it("should return current time (within tolerance)", () => {
    const before = Date.now();
    const ts = createTimestamp();
    const after = Date.now();

    const tsMs = new Date(ts).getTime();

    expect(tsMs).toBeGreaterThanOrEqual(before);
    expect(tsMs).toBeLessThanOrEqual(after);
  });
});

// =============================================================================
// MESSAGE TYPE DISCRIMINATION TESTS
// =============================================================================

describe("Message Type Discrimination", () => {
  it("should discriminate client messages by type field", () => {
    const messages: ClientMessage[] = [
      {
        type: "set_field",
        message_id: "1",
        timestamp: "t",
        payload: { fielders: [{ x: 0, y: 0, name: "a" }] },
      },
      {
        type: "set_difficulty",
        message_id: "2",
        timestamp: "t",
        payload: { difficulty: "easy" },
      },
      {
        type: "ping",
        message_id: "3",
        timestamp: "t",
      },
    ];

    for (const msg of messages) {
      switch (msg.type) {
        case "set_field":
          // TypeScript should narrow this to SetFieldMessage
          expect(msg.payload.fielders).toBeDefined();
          break;
        case "set_difficulty":
          // TypeScript should narrow this to SetDifficultyMessage
          expect(msg.payload.difficulty).toBeDefined();
          break;
        case "ping":
          // TypeScript should narrow this to PingMessage
          expect("payload" in msg).toBe(false);
          break;
      }
    }
  });

  it("should discriminate server messages by type field", () => {
    const errorMsg: ErrorMessage = {
      type: "error",
      message_id: "1",
      timestamp: "t",
      in_reply_to: "orig",
      payload: {
        code: "E1001",
        message: "Error",
        details: null,
        recoverable: true,
      },
    };

    const pongMsg: PongMessage = {
      type: "pong",
      message_id: "2",
      timestamp: "t",
      in_reply_to: "ping-1",
    };

    // Type discrimination in conditional
    if (errorMsg.type === "error") {
      expect(errorMsg.payload.code).toBe("E1001");
    }

    if (pongMsg.type === "pong") {
      expect(pongMsg.in_reply_to).toBe("ping-1");
    }
  });
});

// =============================================================================
// TYPE STRUCTURE TESTS
// =============================================================================

describe("FielderConfig Type", () => {
  it("should allow optional id field", () => {
    const withId: FielderConfig = {
      id: "fielder-1",
      x: 50,
      y: 50,
      name: "cover",
    };

    const withoutId: FielderConfig = {
      x: 50,
      y: 50,
      name: "cover",
    };

    expect(withId.id).toBe("fielder-1");
    expect(withoutId.id).toBeUndefined();
  });

  it("should require x, y, and name fields", () => {
    const config: FielderConfig = {
      x: 25.5,
      y: 75.0,
      name: "mid-off",
    };

    expect(config.x).toBe(25.5);
    expect(config.y).toBe(75.0);
    expect(config.name).toBe("mid-off");
  });
});

describe("Position Type", () => {
  it("should have x and y coordinates", () => {
    const pos: Position = { x: 10.5, y: -20.3 };
    expect(pos.x).toBe(10.5);
    expect(pos.y).toBe(-20.3);
  });
});

describe("Profile Type", () => {
  it("should have required fields", () => {
    const profile: Profile = {
      id: "profile-1",
      name: "Test Player",
      batting_hand: "left",
    };

    expect(profile.id).toBe("profile-1");
    expect(profile.name).toBe("Test Player");
    expect(profile.batting_hand).toBe("left");
  });
});

describe("SimulationResult Type", () => {
  it("should have required fields", () => {
    const result: SimulationResult = {
      outcome: "4",
      runs: 4,
      is_boundary: true,
      is_aerial: false,
      fielder_involved: null,
      fielder_position: null,
      fielding_position: null,
      end_position: { x: 65, y: 0 },
      description: "Cover drive to boundary",
      trajectory: null,
      catch_analysis: null,
      fielding_time: null,
      collection_difficulty: null,
      alignment_score: null,
      priority_score: null,
      fielder_arrival_time: null,
      ball_arrival_time: null,
    };

    expect(result.outcome).toBe("4");
    expect(result.runs).toBe(4);
    expect(result.is_boundary).toBe(true);
    expect(result.end_position.x).toBe(65);
  });

  it("should allow optional fields to be set", () => {
    const result: SimulationResult = {
      outcome: "caught",
      runs: 0,
      is_boundary: false,
      is_aerial: true,
      fielder_involved: "cover",
      fielder_position: { x: 20, y: 60 },
      fielding_position: { x: 22, y: 58 },
      end_position: { x: 22, y: 58 },
      description: "Caught at cover",
      trajectory: {
        projected_distance: 45,
        aerial_distance: 40,
        rolling_distance: 5,
        max_height: 15,
        landing_x: 22,
        landing_y: 58,
        final_x: 22,
        final_y: 58,
        time_of_flight: 2.5,
        horizontal_speed: 18,
        vertical_speed: 12,
        direction_x: 0.6,
        direction_y: 0.8,
      },
      catch_analysis: {
        canCatch: true,
        difficulty: 0.3,
        catchType: "regulation",
        reactionTime: 1.2,
        movementRequired: 2,
        movementPossible: 4,
        ballSpeedAtFielder: 45,
        heightAtIntercept: 1.5,
        timeToIntercept: 2.5,
        fielderArrivalTime: 2.0,
        arrivedBeforeLanding: true,
      },
      fielding_time: 2.5,
      collection_difficulty: 0.2,
      alignment_score: 0.9,
      priority_score: 0.85,
      fielder_arrival_time: 2.0,
      ball_arrival_time: 2.5,
    };

    expect(result.fielder_involved).toBe("cover");
    expect(result.trajectory?.max_height).toBe(15);
    expect(result.catch_analysis?.canCatch).toBe(true);
  });
});

// =============================================================================
// ENUM VALUE TESTS
// =============================================================================

describe("Enum Values", () => {
  it("should accept valid Difficulty values", () => {
    const difficulties: Difficulty[] = ["easy", "medium", "hard"];
    expect(difficulties).toHaveLength(3);
  });

  it("should accept valid BattingHand values", () => {
    const hands: BattingHand[] = ["left", "right"];
    expect(hands).toHaveLength(2);
  });

  it("should accept valid BallResult values", () => {
    const results: BallResult[] = [
      "dot",
      "1",
      "2",
      "3",
      "4",
      "6",
      "W",
      "wd",
      "nb",
    ];
    expect(results).toHaveLength(9);
  });

  it("should accept valid ShotOutcome values", () => {
    const outcomes: ShotOutcome[] = [
      "dot",
      "1",
      "2",
      "3",
      "4",
      "6",
      "caught",
      "dropped",
      "misfield",
    ];
    expect(outcomes).toHaveLength(9);
  });

  it("should accept valid ConnectionState values", () => {
    const states: ConnectionState[] = [
      "disconnected",
      "connecting",
      "connected",
      "reconnecting",
    ];
    expect(states).toHaveLength(4);
  });

  it("should accept valid SessionState values", () => {
    const states: SessionState[] = ["none", "active", "ended"];
    expect(states).toHaveLength(3);
  });

  it("should accept valid TrackingState values", () => {
    const states: TrackingState[] = [
      "idle",
      "detecting",
      "tracking",
      "completed",
    ];
    expect(states).toHaveLength(4);
  });
});

// =============================================================================
// MESSAGE PAYLOAD TESTS
// =============================================================================

describe("Client Message Payloads", () => {
  it("SetFieldMessage should have fielders array", () => {
    const msg: SetFieldMessage = {
      type: "set_field",
      message_id: generateMessageId(),
      timestamp: createTimestamp(),
      payload: {
        fielders: [
          { x: 50, y: 50, name: "cover" },
          { x: 30, y: 30, name: "slip" },
        ],
        boundary_distance: 75,
      },
    };

    expect(msg.payload.fielders).toHaveLength(2);
    expect(msg.payload.boundary_distance).toBe(75);
  });

  it("ManualInputMessage should have result and optional is_boundary", () => {
    const withBoundary: ManualInputMessage = {
      type: "manual_input",
      message_id: generateMessageId(),
      timestamp: createTimestamp(),
      payload: {
        result: "4",
        is_boundary: true,
      },
    };

    const withoutBoundary: ManualInputMessage = {
      type: "manual_input",
      message_id: generateMessageId(),
      timestamp: createTimestamp(),
      payload: {
        result: "dot",
      },
    };

    expect(withBoundary.payload.is_boundary).toBe(true);
    expect(withoutBoundary.payload.is_boundary).toBeUndefined();
  });

  it("StartSessionMessage should have required and optional fields", () => {
    const minimal: StartSessionMessage = {
      type: "start_session",
      message_id: generateMessageId(),
      timestamp: createTimestamp(),
      payload: {
        profile_id: "p1",
      },
    };

    const full: StartSessionMessage = {
      type: "start_session",
      message_id: generateMessageId(),
      timestamp: createTimestamp(),
      payload: {
        profile_id: "p1",
        field_config: [{ x: 50, y: 50, name: "cover" }],
        difficulty: "hard",
        notes: "Practice session",
      },
    };

    expect(minimal.payload.profile_id).toBe("p1");
    expect(minimal.payload.field_config).toBeUndefined();
    expect(full.payload.field_config).toHaveLength(1);
    expect(full.payload.notes).toBe("Practice session");
  });

  it("PingMessage should have no payload", () => {
    const msg: PingMessage = {
      type: "ping",
      message_id: generateMessageId(),
      timestamp: createTimestamp(),
    };

    expect("payload" in msg).toBe(false);
  });
});

describe("Server Message Payloads", () => {
  it("SessionStateMessage should have all required fields", () => {
    const msg: SessionStateMessage = {
      type: "session_state",
      message_id: generateMessageId(),
      timestamp: createTimestamp(),
      in_reply_to: null,
      payload: {
        session: null,
        profiles: [
          { id: "p1", name: "Player 1", batting_hand: "right" },
        ],
        active_profile_id: null,
        difficulty: "medium",
        field_config: [],
        boundary_distance: 70,
      },
    };

    expect(msg.payload.profiles).toHaveLength(1);
    expect(msg.payload.difficulty).toBe("medium");
    expect(msg.payload.boundary_distance).toBe(70);
  });

  it("ErrorMessage should have error payload", () => {
    const msg: ErrorMessage = {
      type: "error",
      message_id: generateMessageId(),
      timestamp: createTimestamp(),
      in_reply_to: "original-msg-id",
      payload: {
        code: "E4002",
        message: "No active session",
        details: { attempted_action: "manual_input" },
        recoverable: false,
      },
    };

    expect(msg.payload.code).toBe("E4002");
    expect(msg.payload.recoverable).toBe(false);
    expect(msg.payload.details).toEqual({ attempted_action: "manual_input" });
  });

  it("PongMessage should require in_reply_to", () => {
    const msg: PongMessage = {
      type: "pong",
      message_id: generateMessageId(),
      timestamp: createTimestamp(),
      in_reply_to: "ping-123",
    };

    expect(msg.in_reply_to).toBe("ping-123");
  });
});
