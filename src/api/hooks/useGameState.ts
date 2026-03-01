/**
 * useGameState Hook
 *
 * React hook for game state from the server.
 * Subscribes to WebSocket messages and maintains synchronized state.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import type {
  SessionData,
  Profile,
  Difficulty,
  FielderConfig,
  SlimShotResult,
  WagonWheelShot,
  ConnectionState,
  WebSocketConfig,
  ConnectionStatus,
} from '../types';
import { CricketWebSocket } from '../websocket';
import { generateMessageId, createTimestamp } from '../types';
import { getServerUrl } from '../config';

/**
 * Game state from the server
 */
export interface GameState {
  /** Current active session, or null if no session */
  session: SessionData | null;
  /** All available player profiles */
  profiles: Profile[];
  /** Currently selected profile ID */
  activeProfileId: string | null;
  /** Current difficulty setting */
  difficulty: Difficulty;
  /** Current field configuration */
  fieldConfig: FielderConfig[];
  /** Boundary distance in metres */
  boundaryDistance: number;
  /** Wagon wheel shots for current session */
  wagonWheelShots: WagonWheelShot[];
  /** Last shot result received */
  lastShotResult: SlimShotResult | null;
}

/**
 * Return type for the useGameState hook
 */
export interface UseGameStateResult {
  /** Current game state from server (null until first session_state received) */
  gameState: GameState | null;
  /** Whether we're waiting for initial state from server */
  isLoading: boolean;
  /** Last error from game operations */
  error: string | null;
  /** Connection status */
  connectionStatus: ConnectionStatus;
  /** Whether connected to server */
  isConnected: boolean;
  /** The WebSocket client instance */
  client: CricketWebSocket | null;
  /** Connect to server */
  connect: () => void;
  /** Disconnect from server */
  disconnect: () => void;
}

// Default WebSocket config
const DEFAULT_WS_CONFIG: WebSocketConfig = {
  url: getServerUrl(),
  initialReconnectDelay: 1000,
  maxReconnectDelay: 30000,
  maxReconnectAttempts: 10,
  connectionTimeout: 10000,
  heartbeatInterval: 30000,
};

/**
 * Hook for game state synchronized with the server.
 *
 * Usage:
 * ```tsx
 * function ScoreBoard() {
 *   const { gameState, isLoading, error, connectionStatus } = useGameState();
 *
 *   if (!connectionStatus.isConnected) return <Connecting />;
 *   if (isLoading) return <Loading />;
 *   if (!gameState) return <NoSession />;
 *
 *   return (
 *     <div>
 *       <h1>{gameState.session?.runs}-{gameState.session?.wickets}</h1>
 *     </div>
 *   );
 * }
 * ```
 *
 * @param config - Optional WebSocket configuration override
 */
export function useGameState(config?: Partial<WebSocketConfig>): UseGameStateResult {
  // Merge config with defaults
  const wsConfig = { ...DEFAULT_WS_CONFIG, ...config };

  // WebSocket client ref
  const clientRef = useRef<CricketWebSocket | null>(null);

  // Connection status state
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>({
    state: 'disconnected',
    isConnected: false,
    reconnectAttempts: 0,
    lastError: null,
    lastConnectedAt: null,
  });

  // Game state
  const [gameState, setGameState] = useState<GameState | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Track if we've received initial state
  const hasReceivedInitialState = useRef(false);

  // Initialize WebSocket client
  useEffect(() => {
    const client = new CricketWebSocket(wsConfig);
    clientRef.current = client;

    // Subscribe to connection state changes
    const unsubscribeConnection = client.onConnectionStateChange((state: ConnectionState) => {
      setConnectionStatus({
        state,
        isConnected: state === 'connected',
        reconnectAttempts: client.attempts,
        lastError: client.error,
        lastConnectedAt: client.lastConnected,
      });

      // On reconnect, request full state
      if (state === 'connected' && hasReceivedInitialState.current) {
        // Send a ping to request state refresh
        // The server should send session_state on new connections
        client.send({
          type: 'ping',
          message_id: generateMessageId(),
          timestamp: createTimestamp(),
        });
      }
    });

    // Subscribe to session_state messages
    const unsubscribeSessionState = client.on('session_state', (message) => {
      const payload = message.payload;
      setGameState((prev) => ({
        session: payload.session,
        profiles: payload.profiles,
        activeProfileId: payload.active_profile_id,
        difficulty: payload.difficulty,
        fieldConfig: payload.field_config,
        boundaryDistance: payload.boundary_distance,
        wagonWheelShots: prev?.wagonWheelShots || [],
        lastShotResult: prev?.lastShotResult || null,
      }));
      setIsLoading(false);
      hasReceivedInitialState.current = true;
      setError(null);
    });

    // Subscribe to shot_result messages
    const unsubscribeShotResult = client.on('shot_result', (message) => {
      const payload = message.payload;
      setGameState((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          lastShotResult: payload.simulation,
        };
      });
    });

    // Subscribe to wagon_wheel_update messages
    const unsubscribeWagonWheel = client.on('wagon_wheel_update', (message) => {
      const payload = message.payload;
      setGameState((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          wagonWheelShots: [...prev.wagonWheelShots, payload.shot],
        };
      });
    });

    // Subscribe to error messages
    const unsubscribeError = client.on('error', (message) => {
      setError(message.payload.message);
    });

    // Connect automatically
    client.connect();

    // Cleanup on unmount
    return () => {
      unsubscribeConnection();
      unsubscribeSessionState();
      unsubscribeShotResult();
      unsubscribeWagonWheel();
      unsubscribeError();
      client.disconnect();
      clientRef.current = null;
    };
  }, [wsConfig.url]);

  // Connect function
  const connect = useCallback(() => {
    clientRef.current?.connect();
  }, []);

  // Disconnect function
  const disconnect = useCallback(() => {
    clientRef.current?.disconnect();
  }, []);

  return {
    gameState,
    isLoading,
    error,
    connectionStatus,
    isConnected: connectionStatus.isConnected,
    client: clientRef.current,
    connect,
    disconnect,
  };
}
