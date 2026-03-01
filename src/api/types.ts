/**
 * Cricket App Client API Types
 *
 * Re-exports all types from contracts/api_types.ts and adds client-specific utility types.
 */

// Re-export all types from the contract
export * from '../../contracts/api_types';

// Import types we need for client-specific utilities
import type {
  ConnectionState,
  ServerMessage,
  ClientMessage,
  ErrorPayload,
} from '../../contracts/api_types';

// =============================================================================
// CLIENT-SPECIFIC UTILITY TYPES
// =============================================================================

/**
 * Configuration for the WebSocket client
 */
export interface WebSocketConfig {
  /** WebSocket server URL (e.g., ws://raspberrypi.local:8080) */
  url: string;
  /** Initial reconnect delay in milliseconds (default: 1000) */
  initialReconnectDelay?: number;
  /** Maximum reconnect delay in milliseconds (default: 30000) */
  maxReconnectDelay?: number;
  /** Maximum number of reconnection attempts before giving up (default: 5) */
  maxReconnectAttempts?: number;
  /** Connection timeout in milliseconds (default: 10000) */
  connectionTimeout?: number;
  /** Heartbeat interval in milliseconds (default: 30000) */
  heartbeatInterval?: number;
}

/**
 * Connection status information for UI display
 */
export interface ConnectionStatus {
  /** Current connection state */
  state: ConnectionState;
  /** Whether the connection is fully established */
  isConnected: boolean;
  /** Number of reconnection attempts made */
  reconnectAttempts: number;
  /** Last error that occurred, if any */
  lastError: ErrorPayload | null;
  /** Time of last successful connection */
  lastConnectedAt: Date | null;
}

/**
 * Callback function types for WebSocket events
 */
export type MessageHandler<T extends ServerMessage = ServerMessage> = (message: T) => void;
export type ErrorHandler = (error: ErrorPayload) => void;
export type ConnectionStateHandler = (state: ConnectionState) => void;

/**
 * Queued message for offline support
 */
export interface QueuedMessage {
  message: ClientMessage;
  timestamp: Date;
  retryCount: number;
}

/**
 * Event types emitted by the WebSocket client
 */
export type WebSocketEventType =
  | 'message'
  | 'error'
  | 'connectionStateChange'
  | 'open'
  | 'close';

/**
 * Extracts the payload type from a server message type
 */
export type ExtractPayload<T extends ServerMessage> = T extends { payload: infer P } ? P : never;

/**
 * Type guard result for narrowing server message types
 */
export type ServerMessageByType<T extends ServerMessage['type']> = Extract<
  ServerMessage,
  { type: T }
>;
