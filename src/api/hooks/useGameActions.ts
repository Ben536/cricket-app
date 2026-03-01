/**
 * useGameActions Hook
 *
 * React hook for sending game commands to the server.
 * Provides typed functions to send WebSocket messages.
 */

import { useCallback, useState } from 'react';
import type {
  Difficulty,
  FielderConfig,
  BallResult,
  BattingHand,
} from '../types';
import { generateMessageId, createTimestamp } from '../types';
import type { CricketWebSocket } from '../websocket';

/**
 * Return type for the useGameActions hook
 */
export interface UseGameActionsResult {
  /** Update field configuration */
  setField: (fielders: FielderConfig[], boundaryDistance?: number) => void;
  /** Update difficulty setting */
  setDifficulty: (difficulty: Difficulty) => void;
  /** Select a player profile */
  selectProfile: (profileId: string) => void;
  /** Create a new player profile */
  createProfile: (name: string, battingHand: BattingHand) => void;
  /** Update an existing player profile */
  updateProfile: (profileId: string, name?: string, battingHand?: BattingHand) => void;
  /** Start a new session */
  startSession: (profileId: string, fieldConfig?: FielderConfig[], difficulty?: Difficulty, notes?: string) => void;
  /** End the current session */
  endSession: (sessionId: string, saveToDatabase?: boolean) => void;
  /** Record a manual ball input */
  manualInput: (result: BallResult, isBoundary?: boolean) => void;
  /** Undo last ball */
  undo: (sessionId: string) => void;
  /** Whether any action is in progress */
  isPending: boolean;
}

/**
 * Hook for sending game commands to the server.
 *
 * Usage:
 * ```tsx
 * function GameControls() {
 *   const { client } = useGameState();
 *   const actions = useGameActions(client);
 *
 *   return (
 *     <button onClick={() => actions.manualInput('4', true)}>
 *       FOUR!
 *     </button>
 *   );
 * }
 * ```
 *
 * @param client - The WebSocket client instance from useGameState
 */
export function useGameActions(client: CricketWebSocket | null): UseGameActionsResult {
  const [isPending, setIsPending] = useState(false);

  const setField = useCallback(
    (fielders: FielderConfig[], boundaryDistance?: number) => {
      if (!client) {
        console.warn('[useGameActions] No WebSocket client available');
        return;
      }
      client.send({
        type: 'set_field',
        message_id: generateMessageId(),
        timestamp: createTimestamp(),
        payload: {
          fielders,
          boundary_distance: boundaryDistance,
        },
      });
    },
    [client]
  );

  const setDifficulty = useCallback(
    (difficulty: Difficulty) => {
      if (!client) {
        console.warn('[useGameActions] No WebSocket client available');
        return;
      }
      client.send({
        type: 'set_difficulty',
        message_id: generateMessageId(),
        timestamp: createTimestamp(),
        payload: {
          difficulty,
        },
      });
    },
    [client]
  );

  const selectProfile = useCallback(
    (profileId: string) => {
      if (!client) {
        console.warn('[useGameActions] No WebSocket client available');
        return;
      }
      client.send({
        type: 'select_profile',
        message_id: generateMessageId(),
        timestamp: createTimestamp(),
        payload: {
          profile_id: profileId,
        },
      });
    },
    [client]
  );

  const createProfile = useCallback(
    (name: string, battingHand: BattingHand) => {
      if (!client) {
        console.warn('[useGameActions] No WebSocket client available');
        return;
      }
      client.send({
        type: 'create_profile',
        message_id: generateMessageId(),
        timestamp: createTimestamp(),
        payload: {
          name,
          batting_hand: battingHand,
        },
      });
    },
    [client]
  );

  const updateProfile = useCallback(
    (profileId: string, name?: string, battingHand?: BattingHand) => {
      if (!client) {
        console.warn('[useGameActions] No WebSocket client available');
        return;
      }
      client.send({
        type: 'update_profile',
        message_id: generateMessageId(),
        timestamp: createTimestamp(),
        payload: {
          profile_id: profileId,
          name,
          batting_hand: battingHand,
        },
      });
    },
    [client]
  );

  const startSession = useCallback(
    (profileId: string, fieldConfig?: FielderConfig[], difficulty?: Difficulty, notes?: string) => {
      if (!client) {
        console.warn('[useGameActions] No WebSocket client available');
        return;
      }
      setIsPending(true);
      client.send({
        type: 'start_session',
        message_id: generateMessageId(),
        timestamp: createTimestamp(),
        payload: {
          profile_id: profileId,
          field_config: fieldConfig,
          difficulty,
          notes,
        },
      });
      // Reset pending state after a short delay (server will send session_state)
      setTimeout(() => setIsPending(false), 500);
    },
    [client]
  );

  const endSession = useCallback(
    (sessionId: string, saveToDatabase: boolean = true) => {
      if (!client) {
        console.warn('[useGameActions] No WebSocket client available');
        return;
      }
      setIsPending(true);
      client.send({
        type: 'end_session',
        message_id: generateMessageId(),
        timestamp: createTimestamp(),
        payload: {
          session_id: sessionId,
          save_to_database: saveToDatabase,
        },
      });
      setTimeout(() => setIsPending(false), 500);
    },
    [client]
  );

  const manualInput = useCallback(
    (result: BallResult, isBoundary?: boolean) => {
      if (!client) {
        console.warn('[useGameActions] No WebSocket client available');
        return;
      }
      client.send({
        type: 'manual_input',
        message_id: generateMessageId(),
        timestamp: createTimestamp(),
        payload: {
          result,
          is_boundary: isBoundary,
        },
      });
    },
    [client]
  );

  const undo = useCallback(
    (sessionId: string) => {
      if (!client) {
        console.warn('[useGameActions] No WebSocket client available');
        return;
      }
      client.send({
        type: 'undo',
        message_id: generateMessageId(),
        timestamp: createTimestamp(),
        payload: {
          session_id: sessionId,
        },
      });
    },
    [client]
  );

  return {
    setField,
    setDifficulty,
    selectProfile,
    createProfile,
    updateProfile,
    startSession,
    endSession,
    manualInput,
    undo,
    isPending,
  };
}
