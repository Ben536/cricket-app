/**
 * useConnection Hook
 *
 * React hook for managing WebSocket connection status.
 * Provides reactive connection state for UI components.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import type { ConnectionState, WebSocketConfig, ConnectionStatus } from '../types';
import { CricketWebSocket } from '../websocket';

/**
 * Return type for the useConnection hook
 */
export interface UseConnectionResult {
  /** Current connection status */
  status: ConnectionStatus;
  /** Connect to the WebSocket server */
  connect: () => void;
  /** Disconnect from the WebSocket server */
  disconnect: () => void;
  /** Whether the connection is fully established */
  isConnected: boolean;
  /** The WebSocket client instance (for advanced usage) */
  client: CricketWebSocket | null;
}

/**
 * Hook for managing WebSocket connection lifecycle.
 *
 * Usage:
 * ```tsx
 * function ConnectionIndicator() {
 *   const { status, connect, disconnect, isConnected } = useConnection({
 *     url: 'ws://raspberrypi.local:8080'
 *   });
 *
 *   return (
 *     <div>
 *       <span>Status: {status.state}</span>
 *       {!isConnected && <button onClick={connect}>Connect</button>}
 *       {isConnected && <button onClick={disconnect}>Disconnect</button>}
 *     </div>
 *   );
 * }
 * ```
 *
 * @param config - WebSocket configuration
 * @param autoConnect - Whether to connect automatically on mount (default: true)
 * @returns Connection status and control functions
 */
export function useConnection(
  config: WebSocketConfig,
  autoConnect: boolean = true
): UseConnectionResult {
  // Store client in a ref to maintain stable reference
  const clientRef = useRef<CricketWebSocket | null>(null);

  // Connection status state
  const [status, setStatus] = useState<ConnectionStatus>({
    state: 'disconnected',
    isConnected: false,
    reconnectAttempts: 0,
    lastError: null,
    lastConnectedAt: null,
  });

  // Initialize client on mount
  useEffect(() => {
    const client = new CricketWebSocket(config);
    clientRef.current = client;

    // Subscribe to connection state changes
    const unsubscribe = client.onConnectionStateChange((state: ConnectionState) => {
      setStatus({
        state,
        isConnected: state === 'connected',
        reconnectAttempts: client.attempts,
        lastError: client.error,
        lastConnectedAt: client.lastConnected,
      });
    });

    // Auto-connect if enabled
    if (autoConnect) {
      client.connect();
    }

    // Cleanup on unmount
    return () => {
      unsubscribe();
      client.disconnect();
      clientRef.current = null;
    };
  }, [config.url]); // Only re-create if URL changes

  // Connect function
  const connect = useCallback(() => {
    clientRef.current?.connect();
  }, []);

  // Disconnect function
  const disconnect = useCallback(() => {
    clientRef.current?.disconnect();
  }, []);

  return {
    status,
    connect,
    disconnect,
    isConnected: status.isConnected,
    client: clientRef.current,
  };
}

/**
 * Hook for accessing an existing WebSocket client from context.
 * This is a placeholder for Phase 2 when we implement WebSocketProvider.
 *
 * Usage:
 * ```tsx
 * function MyComponent() {
 *   const client = useWebSocketClient();
 *   // Use client to send messages
 * }
 * ```
 */
export function useWebSocketClient(): CricketWebSocket | null {
  // Phase 2: This will use React Context to get the client
  // For now, return null as we haven't implemented the provider
  console.warn('[useWebSocketClient] Not implemented yet - use useConnection instead');
  return null;
}
