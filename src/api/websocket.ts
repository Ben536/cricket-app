/**
 * Cricket App WebSocket Client
 *
 * Handles WebSocket connection to the Python game engine on Raspberry Pi.
 * Features:
 * - Auto-reconnect with exponential backoff
 * - Message queue for offline support
 * - Heartbeat/ping-pong handling
 * - Event emitter pattern for message callbacks
 * - Connection state tracking
 */

import type {
  ConnectionState,
  ClientMessage,
  ServerMessage,
  ErrorPayload,
  PingMessage,
} from './types';

import {
  generateMessageId,
  createTimestamp,
} from '../../contracts/api_types';

import type {
  WebSocketConfig,
  QueuedMessage,
  MessageHandler,
  ConnectionStateHandler,
} from './types';

// =============================================================================
// DEFAULT CONFIGURATION
// =============================================================================

const DEFAULT_CONFIG: Required<WebSocketConfig> = {
  url: 'ws://localhost:8080',
  initialReconnectDelay: 1000,
  maxReconnectDelay: 30000,
  maxReconnectAttempts: 5,
  connectionTimeout: 10000,
  heartbeatInterval: 30000,
};

// =============================================================================
// WEBSOCKET CLIENT CLASS
// =============================================================================

/**
 * WebSocket client for communication with the Cricket App game engine.
 *
 * Usage:
 * ```typescript
 * const client = new CricketWebSocket({ url: 'ws://raspberrypi.local:8080' });
 *
 * client.on('message', (msg) => console.log('Received:', msg));
 * client.on('connectionStateChange', (state) => console.log('State:', state));
 *
 * client.connect();
 * client.send({ type: 'ping', message_id: '...', timestamp: '...' });
 * ```
 */
export class CricketWebSocket {
  private config: Required<WebSocketConfig>;
  private socket: WebSocket | null = null;
  private connectionState: ConnectionState = 'disconnected';
  private reconnectAttempts = 0;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private connectionTimeout: ReturnType<typeof setTimeout> | null = null;
  private heartbeatInterval: ReturnType<typeof setInterval> | null = null;
  private messageQueue: QueuedMessage[] = [];
  private lastConnectedAt: Date | null = null;
  private lastError: ErrorPayload | null = null;

  // Event listeners
  private messageHandlers: Set<MessageHandler> = new Set();
  private connectionStateHandlers: Set<ConnectionStateHandler> = new Set();

  constructor(config: WebSocketConfig) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  // ===========================================================================
  // PUBLIC API
  // ===========================================================================

  /**
   * Get current connection state
   */
  get state(): ConnectionState {
    return this.connectionState;
  }

  /**
   * Check if currently connected
   */
  get isConnected(): boolean {
    return this.connectionState === 'connected';
  }

  /**
   * Get number of reconnection attempts
   */
  get attempts(): number {
    return this.reconnectAttempts;
  }

  /**
   * Get last error that occurred
   */
  get error(): ErrorPayload | null {
    return this.lastError;
  }

  /**
   * Get time of last successful connection
   */
  get lastConnected(): Date | null {
    return this.lastConnectedAt;
  }

  /**
   * Get number of messages queued for sending
   */
  get queuedMessageCount(): number {
    return this.messageQueue.length;
  }

  /**
   * Connect to the WebSocket server.
   * Transitions from 'disconnected' to 'connecting'.
   */
  connect(): void {
    if (this.connectionState === 'connected' || this.connectionState === 'connecting') {
      console.warn('[CricketWebSocket] Already connected or connecting');
      return;
    }

    this.setConnectionState('connecting');
    this.createSocket();
  }

  /**
   * Disconnect from the WebSocket server.
   * Clears all timers and transitions to 'disconnected'.
   */
  disconnect(): void {
    this.clearTimers();
    this.reconnectAttempts = 0;

    if (this.socket) {
      // Remove handlers before closing to prevent reconnection
      this.socket.onclose = null;
      this.socket.onerror = null;
      this.socket.onmessage = null;
      this.socket.onopen = null;
      this.socket.close();
      this.socket = null;
    }

    this.setConnectionState('disconnected');
  }

  /**
   * Send a message to the server.
   * If disconnected, queues the message for later delivery.
   *
   * @param message - The client message to send
   * @returns true if sent immediately, false if queued
   */
  send(message: ClientMessage): boolean {
    if (this.connectionState === 'connected' && this.socket?.readyState === WebSocket.OPEN) {
      try {
        this.socket.send(JSON.stringify(message));
        return true;
      } catch (error) {
        console.error('[CricketWebSocket] Send error:', error);
        this.queueMessage(message);
        return false;
      }
    }

    // Queue message for later if not connected
    if (this.connectionState !== 'disconnected') {
      this.queueMessage(message);
    }
    return false;
  }

  /**
   * Subscribe to all incoming server messages.
   *
   * @param handler - Callback function for messages
   * @returns Unsubscribe function
   */
  onMessage(handler: MessageHandler): () => void {
    this.messageHandlers.add(handler);
    return () => this.messageHandlers.delete(handler);
  }

  /**
   * Subscribe to connection state changes.
   *
   * @param handler - Callback function for state changes
   * @returns Unsubscribe function
   */
  onConnectionStateChange(handler: ConnectionStateHandler): () => void {
    this.connectionStateHandlers.add(handler);
    return () => this.connectionStateHandlers.delete(handler);
  }

  /**
   * Convenience method: Subscribe to messages of a specific type.
   *
   * @param type - The message type to filter for
   * @param handler - Callback function for matching messages
   * @returns Unsubscribe function
   */
  on<T extends ServerMessage['type']>(
    type: T,
    handler: (message: Extract<ServerMessage, { type: T }>) => void
  ): () => void {
    const wrappedHandler: MessageHandler = (message) => {
      if (message.type === type) {
        handler(message as Extract<ServerMessage, { type: T }>);
      }
    };
    this.messageHandlers.add(wrappedHandler);
    return () => this.messageHandlers.delete(wrappedHandler);
  }

  /**
   * Clear the message queue (e.g., when user wants to discard pending changes).
   */
  clearQueue(): void {
    this.messageQueue = [];
  }

  // ===========================================================================
  // PRIVATE METHODS
  // ===========================================================================

  private createSocket(): void {
    try {
      this.socket = new WebSocket(this.config.url);
      this.setupSocketHandlers();
      this.startConnectionTimeout();
    } catch (error) {
      console.error('[CricketWebSocket] Failed to create socket:', error);
      this.handleConnectionFailure();
    }
  }

  private setupSocketHandlers(): void {
    if (!this.socket) return;

    this.socket.onopen = () => {
      this.clearConnectionTimeout();
      this.reconnectAttempts = 0;
      this.lastConnectedAt = new Date();
      this.lastError = null;
      this.setConnectionState('connected');
      this.startHeartbeat();
      this.flushMessageQueue();
    };

    this.socket.onclose = (event) => {
      console.log('[CricketWebSocket] Connection closed:', event.code, event.reason);
      this.clearTimers();

      // If we were connected, try to reconnect
      if (this.connectionState === 'connected') {
        this.setConnectionState('reconnecting');
        this.scheduleReconnect();
      } else if (this.connectionState === 'connecting') {
        this.handleConnectionFailure();
      }
    };

    this.socket.onerror = (event) => {
      console.error('[CricketWebSocket] Socket error:', event);
      // onclose will be called after onerror, so we handle reconnection there
    };

    this.socket.onmessage = (event) => {
      this.handleMessage(event.data);
    };
  }

  private handleMessage(data: string): void {
    try {
      const message = JSON.parse(data) as ServerMessage;

      // Handle pong messages internally (heartbeat response)
      if (message.type === 'pong') {
        // Heartbeat acknowledged, nothing more to do
        return;
      }

      // Handle error messages
      if (message.type === 'error') {
        this.lastError = message.payload;
      }

      // Emit to all handlers
      this.messageHandlers.forEach((handler) => {
        try {
          handler(message);
        } catch (error) {
          console.error('[CricketWebSocket] Message handler error:', error);
        }
      });
    } catch (error) {
      console.error('[CricketWebSocket] Failed to parse message:', error);
    }
  }

  private setConnectionState(state: ConnectionState): void {
    if (this.connectionState === state) return;

    const previousState = this.connectionState;
    this.connectionState = state;

    console.log(`[CricketWebSocket] State: ${previousState} -> ${state}`);

    this.connectionStateHandlers.forEach((handler) => {
      try {
        handler(state);
      } catch (error) {
        console.error('[CricketWebSocket] State handler error:', error);
      }
    });
  }

  private handleConnectionFailure(): void {
    if (this.reconnectAttempts < this.config.maxReconnectAttempts) {
      this.setConnectionState('reconnecting');
      this.scheduleReconnect();
    } else {
      console.error('[CricketWebSocket] Max reconnection attempts exceeded');
      this.lastError = {
        code: 'E1005',
        message: 'Server is unavailable after multiple reconnection attempts',
        details: { attempts: this.reconnectAttempts },
        recoverable: true,
      };
      this.setConnectionState('disconnected');
    }
  }

  private scheduleReconnect(): void {
    const delay = this.calculateReconnectDelay();
    console.log(`[CricketWebSocket] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts + 1})`);

    this.reconnectTimeout = setTimeout(() => {
      this.reconnectAttempts++;
      this.setConnectionState('connecting');
      this.createSocket();
    }, delay);
  }

  private calculateReconnectDelay(): number {
    // Exponential backoff: 1s, 2s, 4s, 8s, capped at maxReconnectDelay
    const delay = this.config.initialReconnectDelay * Math.pow(2, this.reconnectAttempts);
    return Math.min(delay, this.config.maxReconnectDelay);
  }

  private startConnectionTimeout(): void {
    this.connectionTimeout = setTimeout(() => {
      console.warn('[CricketWebSocket] Connection timeout');
      if (this.socket) {
        this.socket.close();
      }
      this.lastError = {
        code: 'E1001',
        message: 'Connection timed out',
        details: null,
        recoverable: true,
      };
      this.handleConnectionFailure();
    }, this.config.connectionTimeout);
  }

  private clearConnectionTimeout(): void {
    if (this.connectionTimeout) {
      clearTimeout(this.connectionTimeout);
      this.connectionTimeout = null;
    }
  }

  private startHeartbeat(): void {
    this.heartbeatInterval = setInterval(() => {
      if (this.connectionState === 'connected') {
        const pingMessage: PingMessage = {
          type: 'ping',
          message_id: generateMessageId(),
          timestamp: createTimestamp(),
        };
        this.send(pingMessage);
      }
    }, this.config.heartbeatInterval);
  }

  private clearTimers(): void {
    this.clearConnectionTimeout();

    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }

    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
  }

  private queueMessage(message: ClientMessage): void {
    this.messageQueue.push({
      message,
      timestamp: new Date(),
      retryCount: 0,
    });
    console.log(`[CricketWebSocket] Message queued (${this.messageQueue.length} pending)`);
  }

  private flushMessageQueue(): void {
    if (this.messageQueue.length === 0) return;

    console.log(`[CricketWebSocket] Flushing ${this.messageQueue.length} queued messages`);

    const queue = [...this.messageQueue];
    this.messageQueue = [];

    for (const queued of queue) {
      // Update timestamp to current time before sending
      const message = {
        ...queued.message,
        timestamp: createTimestamp(),
      };

      if (!this.send(message)) {
        // If send fails, message will be re-queued
        break;
      }
    }
  }
}

// =============================================================================
// SINGLETON INSTANCE (optional, for simple usage)
// =============================================================================

let defaultInstance: CricketWebSocket | null = null;

/**
 * Get or create the default WebSocket client instance.
 * Useful for simple apps that only need one connection.
 *
 * @param config - Configuration (only used on first call)
 * @returns The singleton WebSocket client
 */
export function getWebSocket(config?: WebSocketConfig): CricketWebSocket {
  if (!defaultInstance) {
    if (!config) {
      throw new Error('WebSocket config required for first initialization');
    }
    defaultInstance = new CricketWebSocket(config);
  }
  return defaultInstance;
}

/**
 * Reset the singleton instance (useful for testing or reconfiguration).
 */
export function resetWebSocket(): void {
  if (defaultInstance) {
    defaultInstance.disconnect();
    defaultInstance = null;
  }
}
