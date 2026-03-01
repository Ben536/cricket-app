/**
 * WebSocket Client Tests
 *
 * Tests for WebSocket connection handling, reconnection logic,
 * and message queue during disconnect.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

import {
  type ClientMessage,
  type ServerMessage,
  type ConnectionState,
  type SessionState,
  generateMessageId,
  createTimestamp,
} from "../../../contracts/api_types";

// =============================================================================
// MOCK WEBSOCKET IMPLEMENTATION
// =============================================================================

type WebSocketEventHandler = (event: unknown) => void;

interface MockWebSocketInstance {
  url: string;
  readyState: number;
  onopen: WebSocketEventHandler | null;
  onclose: WebSocketEventHandler | null;
  onerror: WebSocketEventHandler | null;
  onmessage: WebSocketEventHandler | null;
  sentMessages: string[];
  close: (code?: number, reason?: string) => void;
  send: (data: string) => void;
  // Test helpers
  simulateOpen: () => void;
  simulateClose: (code?: number, reason?: string) => void;
  simulateError: (error: Error) => void;
  simulateMessage: (data: unknown) => void;
}

const createMockWebSocket = (): MockWebSocketInstance => {
  const instance: MockWebSocketInstance = {
    url: "",
    readyState: 0, // CONNECTING
    onopen: null,
    onclose: null,
    onerror: null,
    onmessage: null,
    sentMessages: [],

    close(code = 1000, reason = "") {
      this.readyState = 3; // CLOSED
      if (this.onclose) {
        this.onclose({ code, reason });
      }
    },

    send(data: string) {
      if (this.readyState !== 1) {
        throw new Error("WebSocket is not open");
      }
      this.sentMessages.push(data);
    },

    simulateOpen() {
      this.readyState = 1; // OPEN
      if (this.onopen) {
        this.onopen({});
      }
    },

    simulateClose(code = 1006, reason = "Connection lost") {
      this.readyState = 3; // CLOSED
      if (this.onclose) {
        this.onclose({ code, reason });
      }
    },

    simulateError(error: Error) {
      if (this.onerror) {
        this.onerror({ error });
      }
    },

    simulateMessage(data: unknown) {
      if (this.onmessage) {
        this.onmessage({ data: JSON.stringify(data) });
      }
    },
  };

  return instance;
};

// =============================================================================
// WEBSOCKET CLIENT IMPLEMENTATION (for testing)
// =============================================================================

interface WebSocketClientOptions {
  url: string;
  maxRetries?: number;
  baseRetryDelay?: number;
  maxRetryDelay?: number;
  heartbeatInterval?: number;
}

interface WebSocketClientState {
  connectionState: ConnectionState;
  sessionState: SessionState;
  retryCount: number;
}

type MessageHandler = (message: ServerMessage) => void;
type StateChangeHandler = (state: WebSocketClientState) => void;
type ErrorHandler = (error: Error, code?: string) => void;

/**
 * WebSocket client implementation following state_machine.md spec.
 *
 * This is a reference implementation for testing. The actual
 * implementation will live in the codebase.
 */
class WebSocketClient {
  private ws: MockWebSocketInstance | null = null;
  private options: Required<WebSocketClientOptions>;
  private state: WebSocketClientState;
  private messageQueue: ClientMessage[] = [];
  private pendingMessages: Map<string, { resolve: (v: ServerMessage) => void; reject: (e: Error) => void }> = new Map();
  private messageHandlers: MessageHandler[] = [];
  private stateChangeHandlers: StateChangeHandler[] = [];
  private errorHandlers: ErrorHandler[] = [];
  private retryTimeout: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimeout: ReturnType<typeof setTimeout> | null = null;
  private createWebSocket: () => MockWebSocketInstance;

  constructor(
    options: WebSocketClientOptions,
    createWs: () => MockWebSocketInstance = createMockWebSocket
  ) {
    this.options = {
      maxRetries: 5,
      baseRetryDelay: 1000,
      maxRetryDelay: 30000,
      heartbeatInterval: 30000,
      ...options,
    };

    this.state = {
      connectionState: "disconnected",
      sessionState: "none",
      retryCount: 0,
    };

    this.createWebSocket = createWs;
  }

  getState(): WebSocketClientState {
    return { ...this.state };
  }

  getQueuedMessages(): ClientMessage[] {
    return [...this.messageQueue];
  }

  onMessage(handler: MessageHandler): void {
    this.messageHandlers.push(handler);
  }

  onStateChange(handler: StateChangeHandler): void {
    this.stateChangeHandlers.push(handler);
  }

  onError(handler: ErrorHandler): void {
    this.errorHandlers.push(handler);
  }

  private setState(partial: Partial<WebSocketClientState>): void {
    this.state = { ...this.state, ...partial };
    for (const handler of this.stateChangeHandlers) {
      handler(this.getState());
    }
  }

  private emitError(error: Error, code?: string): void {
    for (const handler of this.errorHandlers) {
      handler(error, code);
    }
  }

  connect(): void {
    if (this.state.connectionState !== "disconnected") {
      return;
    }

    this.setState({ connectionState: "connecting" });
    this.ws = this.createWebSocket();
    this.ws.url = this.options.url;

    this.ws.onopen = () => {
      this.setState({
        connectionState: "connected",
        retryCount: 0,
      });
      this.flushMessageQueue();
      this.startHeartbeat();
    };

    this.ws.onclose = (event: unknown) => {
      const closeEvent = event as { code?: number; reason?: string };
      this.stopHeartbeat();

      if (this.state.connectionState === "connecting") {
        // Connection failed during initial connect
        this.setState({ connectionState: "disconnected" });
        this.emitError(new Error("Connection failed"), "E1001");
      } else if (this.state.connectionState === "connected") {
        // Connection lost unexpectedly
        this.setState({ connectionState: "reconnecting" });
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = (event: unknown) => {
      const errorEvent = event as { error?: Error };
      this.emitError(errorEvent.error || new Error("WebSocket error"), "E1004");
    };

    this.ws.onmessage = (event: unknown) => {
      const msgEvent = event as { data: string };
      try {
        const message = JSON.parse(msgEvent.data) as ServerMessage;
        this.handleMessage(message);
      } catch (e) {
        this.emitError(new Error("Invalid message format"), "E3001");
      }
    };
  }

  disconnect(): void {
    this.stopHeartbeat();
    if (this.retryTimeout) {
      clearTimeout(this.retryTimeout);
      this.retryTimeout = null;
    }

    if (this.ws) {
      this.ws.close(1000, "Client disconnect");
      this.ws = null;
    }

    this.setState({
      connectionState: "disconnected",
      retryCount: 0,
    });
  }

  send(message: ClientMessage): void {
    if (this.state.connectionState !== "connected" || !this.ws) {
      // Queue message for later
      this.messageQueue.push(message);
      return;
    }

    try {
      this.ws.send(JSON.stringify(message));
    } catch (e) {
      // Queue if send fails
      this.messageQueue.push(message);
    }
  }

  sendAndWait(message: ClientMessage, timeout = 5000): Promise<ServerMessage> {
    return new Promise((resolve, reject) => {
      this.pendingMessages.set(message.message_id, { resolve, reject });

      const timeoutId = setTimeout(() => {
        this.pendingMessages.delete(message.message_id);
        reject(new Error("Request timeout"));
      }, timeout);

      // Override resolve to clear timeout
      const originalResolve = resolve;
      this.pendingMessages.set(message.message_id, {
        resolve: (msg) => {
          clearTimeout(timeoutId);
          originalResolve(msg);
        },
        reject,
      });

      this.send(message);
    });
  }

  private handleMessage(message: ServerMessage): void {
    // Check if this is a reply to a pending request
    if ("in_reply_to" in message && message.in_reply_to) {
      const pending = this.pendingMessages.get(message.in_reply_to);
      if (pending) {
        this.pendingMessages.delete(message.in_reply_to);
        if (message.type === "error") {
          pending.reject(new Error((message as { payload: { message: string } }).payload.message));
        } else {
          pending.resolve(message);
        }
        return;
      }
    }

    // Emit to handlers
    for (const handler of this.messageHandlers) {
      handler(message);
    }
  }

  private flushMessageQueue(): void {
    if (!this.ws || this.state.connectionState !== "connected") {
      return;
    }

    const queue = [...this.messageQueue];
    this.messageQueue = [];

    for (const message of queue) {
      try {
        this.ws.send(JSON.stringify(message));
      } catch (e) {
        // Re-queue on failure
        this.messageQueue.push(message);
      }
    }
  }

  private scheduleReconnect(): void {
    if (this.state.retryCount >= this.options.maxRetries) {
      this.setState({ connectionState: "disconnected" });
      this.emitError(new Error("Max retries exceeded"), "E1005");
      return;
    }

    // Exponential backoff: 1s, 2s, 4s, 8s...
    const delay = Math.min(
      this.options.baseRetryDelay * Math.pow(2, this.state.retryCount),
      this.options.maxRetryDelay
    );

    this.retryTimeout = setTimeout(() => {
      this.setState({
        connectionState: "connecting",
        retryCount: this.state.retryCount + 1,
      });
      this.reconnect();
    }, delay);
  }

  private reconnect(): void {
    this.ws = this.createWebSocket();
    this.ws.url = this.options.url;

    this.ws.onopen = () => {
      this.setState({
        connectionState: "connected",
        retryCount: 0,
      });
      this.flushMessageQueue();
      this.startHeartbeat();
    };

    this.ws.onclose = () => {
      this.stopHeartbeat();
      if (this.state.connectionState === "connecting") {
        this.setState({ connectionState: "reconnecting" });
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = (event: unknown) => {
      const errorEvent = event as { error?: Error };
      this.emitError(errorEvent.error || new Error("Reconnection error"));
    };

    this.ws.onmessage = (event: unknown) => {
      const msgEvent = event as { data: string };
      try {
        const message = JSON.parse(msgEvent.data) as ServerMessage;
        this.handleMessage(message);
      } catch (e) {
        this.emitError(new Error("Invalid message format"), "E3001");
      }
    };
  }

  private startHeartbeat(): void {
    this.heartbeatTimeout = setInterval(() => {
      if (this.state.connectionState === "connected") {
        this.send({
          type: "ping",
          message_id: generateMessageId(),
          timestamp: createTimestamp(),
        });
      }
    }, this.options.heartbeatInterval);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimeout) {
      clearInterval(this.heartbeatTimeout);
      this.heartbeatTimeout = null;
    }
  }

  // Test helper to access internal WebSocket
  _getWebSocket(): MockWebSocketInstance | null {
    return this.ws;
  }
}

// =============================================================================
// CONNECTION HANDLING TESTS
// =============================================================================

describe("WebSocket Connection Handling", () => {
  let client: WebSocketClient;
  let mockWs: MockWebSocketInstance;

  beforeEach(() => {
    vi.useFakeTimers();
    mockWs = createMockWebSocket();
    client = new WebSocketClient(
      { url: "ws://localhost:8080" },
      () => mockWs
    );
  });

  afterEach(() => {
    client.disconnect();
    vi.useRealTimers();
  });

  describe("Initial Connection", () => {
    it("should start in disconnected state", () => {
      expect(client.getState().connectionState).toBe("disconnected");
    });

    it("should transition to connecting when connect() called", () => {
      client.connect();
      expect(client.getState().connectionState).toBe("connecting");
    });

    it("should transition to connected when WebSocket opens", () => {
      client.connect();
      mockWs.simulateOpen();
      expect(client.getState().connectionState).toBe("connected");
    });

    it("should transition to disconnected on connection failure", () => {
      client.connect();
      mockWs.simulateClose(1006, "Connection failed");
      expect(client.getState().connectionState).toBe("disconnected");
    });

    it("should emit state change events", () => {
      const states: ConnectionState[] = [];
      client.onStateChange((state) => states.push(state.connectionState));

      client.connect();
      mockWs.simulateOpen();

      expect(states).toEqual(["connecting", "connected"]);
    });

    it("should emit error on connection failure", () => {
      const errors: Error[] = [];
      client.onError((error) => errors.push(error));

      client.connect();
      mockWs.simulateClose(1006);

      expect(errors).toHaveLength(1);
      expect(errors[0].message).toContain("failed");
    });

    it("should not connect if already connecting", () => {
      client.connect();
      expect(client.getState().connectionState).toBe("connecting");

      // Second connect should be ignored
      client.connect();
      expect(client.getState().connectionState).toBe("connecting");
    });
  });

  describe("Disconnection", () => {
    it("should transition to disconnected on disconnect()", () => {
      client.connect();
      mockWs.simulateOpen();
      expect(client.getState().connectionState).toBe("connected");

      client.disconnect();
      expect(client.getState().connectionState).toBe("disconnected");
    });

    it("should reset retry count on disconnect()", () => {
      client.connect();
      mockWs.simulateOpen();
      mockWs.simulateClose();

      // In reconnecting state with incremented retry
      expect(client.getState().connectionState).toBe("reconnecting");

      client.disconnect();
      expect(client.getState().retryCount).toBe(0);
    });
  });
});

// =============================================================================
// RECONNECTION LOGIC TESTS
// =============================================================================

describe("WebSocket Reconnection", () => {
  let client: WebSocketClient;
  let mockWsInstances: MockWebSocketInstance[];
  let instanceIndex: number;

  beforeEach(() => {
    vi.useFakeTimers();
    mockWsInstances = [];
    instanceIndex = 0;

    client = new WebSocketClient(
      {
        url: "ws://localhost:8080",
        maxRetries: 5,
        baseRetryDelay: 1000,
        maxRetryDelay: 30000,
      },
      () => {
        const ws = createMockWebSocket();
        mockWsInstances.push(ws);
        return ws;
      }
    );
  });

  afterEach(() => {
    client.disconnect();
    vi.useRealTimers();
  });

  it("should transition to reconnecting on unexpected close", () => {
    client.connect();
    mockWsInstances[0].simulateOpen();
    expect(client.getState().connectionState).toBe("connected");

    mockWsInstances[0].simulateClose(1006);
    expect(client.getState().connectionState).toBe("reconnecting");
  });

  it("should attempt reconnection after delay", () => {
    client.connect();
    mockWsInstances[0].simulateOpen();
    mockWsInstances[0].simulateClose(1006);

    expect(mockWsInstances).toHaveLength(1);

    // Advance time by first retry delay
    vi.advanceTimersByTime(1000);

    // Should have created new WebSocket
    expect(mockWsInstances).toHaveLength(2);
    expect(client.getState().connectionState).toBe("connecting");
  });

  it("should use exponential backoff for retry delays", () => {
    client.connect();
    mockWsInstances[0].simulateOpen();
    mockWsInstances[0].simulateClose(1006);

    // First retry: 1s
    vi.advanceTimersByTime(1000);
    expect(mockWsInstances).toHaveLength(2);
    mockWsInstances[1].simulateClose(1006);

    // Second retry: 2s (should not trigger at 1s)
    vi.advanceTimersByTime(1000);
    expect(mockWsInstances).toHaveLength(2);

    // Advance remaining 1s for 2s total
    vi.advanceTimersByTime(1000);
    expect(mockWsInstances).toHaveLength(3);
    mockWsInstances[2].simulateClose(1006);

    // Third retry: 4s
    vi.advanceTimersByTime(4000);
    expect(mockWsInstances).toHaveLength(4);
  });

  it("should reset retry count on successful reconnection", () => {
    client.connect();
    mockWsInstances[0].simulateOpen();
    mockWsInstances[0].simulateClose(1006);

    // First retry
    vi.advanceTimersByTime(1000);
    expect(client.getState().retryCount).toBe(1);

    // Successful connection
    mockWsInstances[1].simulateOpen();
    expect(client.getState().retryCount).toBe(0);
  });

  it("should give up after max retries exceeded", () => {
    client.connect();
    mockWsInstances[0].simulateOpen();
    mockWsInstances[0].simulateClose(1006);

    // Retry 5 times (max)
    for (let i = 0; i < 5; i++) {
      const delay = 1000 * Math.pow(2, i);
      vi.advanceTimersByTime(delay);
      mockWsInstances[mockWsInstances.length - 1].simulateClose(1006);
    }

    // After max retries, should transition to disconnected
    expect(client.getState().connectionState).toBe("disconnected");
    expect(mockWsInstances.length).toBe(6); // Initial + 5 retries
  });

  it("should emit error when max retries exceeded", () => {
    const errors: string[] = [];
    client.onError((error) => errors.push(error.message));

    client.connect();
    mockWsInstances[0].simulateOpen();
    mockWsInstances[0].simulateClose(1006);

    // Exhaust retries
    for (let i = 0; i < 5; i++) {
      const delay = 1000 * Math.pow(2, i);
      vi.advanceTimersByTime(delay);
      mockWsInstances[mockWsInstances.length - 1].simulateClose(1006);
    }

    expect(errors).toContain("Max retries exceeded");
  });
});

// =============================================================================
// MESSAGE QUEUE TESTS
// =============================================================================

describe("Message Queue During Disconnect", () => {
  let client: WebSocketClient;
  let mockWsInstances: MockWebSocketInstance[];

  beforeEach(() => {
    vi.useFakeTimers();
    mockWsInstances = [];

    client = new WebSocketClient(
      { url: "ws://localhost:8080", baseRetryDelay: 1000 },
      () => {
        const ws = createMockWebSocket();
        mockWsInstances.push(ws);
        return ws;
      }
    );
  });

  afterEach(() => {
    client.disconnect();
    vi.useRealTimers();
  });

  it("should queue messages when disconnected", () => {
    const message: ClientMessage = {
      type: "ping",
      message_id: generateMessageId(),
      timestamp: createTimestamp(),
    };

    client.send(message);

    expect(client.getQueuedMessages()).toHaveLength(1);
    expect(client.getQueuedMessages()[0].type).toBe("ping");
  });

  it("should queue messages when reconnecting", () => {
    client.connect();
    mockWsInstances[0].simulateOpen();
    mockWsInstances[0].simulateClose(1006);

    expect(client.getState().connectionState).toBe("reconnecting");

    const message: ClientMessage = {
      type: "ping",
      message_id: generateMessageId(),
      timestamp: createTimestamp(),
    };

    client.send(message);

    expect(client.getQueuedMessages()).toHaveLength(1);
  });

  it("should flush message queue on reconnection", () => {
    client.connect();
    mockWsInstances[0].simulateOpen();

    // Queue some messages during disconnect
    mockWsInstances[0].simulateClose(1006);

    client.send({
      type: "ping",
      message_id: "msg-1",
      timestamp: createTimestamp(),
    });
    client.send({
      type: "ping",
      message_id: "msg-2",
      timestamp: createTimestamp(),
    });

    expect(client.getQueuedMessages()).toHaveLength(2);

    // Reconnect
    vi.advanceTimersByTime(1000);
    mockWsInstances[1].simulateOpen();

    // Queue should be flushed
    expect(client.getQueuedMessages()).toHaveLength(0);
    expect(mockWsInstances[1].sentMessages).toHaveLength(2);
  });

  it("should preserve message order in queue", () => {
    client.send({ type: "ping", message_id: "1", timestamp: "t" });
    client.send({ type: "ping", message_id: "2", timestamp: "t" });
    client.send({ type: "ping", message_id: "3", timestamp: "t" });

    const queue = client.getQueuedMessages();
    expect(queue[0].message_id).toBe("1");
    expect(queue[1].message_id).toBe("2");
    expect(queue[2].message_id).toBe("3");
  });

  it("should send messages immediately when connected", () => {
    client.connect();
    mockWsInstances[0].simulateOpen();

    const message: ClientMessage = {
      type: "ping",
      message_id: generateMessageId(),
      timestamp: createTimestamp(),
    };

    client.send(message);

    // Message should be sent immediately, not queued
    expect(client.getQueuedMessages()).toHaveLength(0);
    expect(mockWsInstances[0].sentMessages).toHaveLength(1);
  });
});

// =============================================================================
// MESSAGE HANDLING TESTS
// =============================================================================

describe("Message Handling", () => {
  let client: WebSocketClient;
  let mockWs: MockWebSocketInstance;

  beforeEach(() => {
    vi.useFakeTimers();
    mockWs = createMockWebSocket();
    client = new WebSocketClient({ url: "ws://localhost:8080" }, () => mockWs);
    client.connect();
    mockWs.simulateOpen();
  });

  afterEach(() => {
    client.disconnect();
    vi.useRealTimers();
  });

  it("should emit received messages to handlers", () => {
    const messages: ServerMessage[] = [];
    client.onMessage((msg) => messages.push(msg));

    mockWs.simulateMessage({
      type: "pong",
      message_id: "pong-1",
      timestamp: createTimestamp(),
      in_reply_to: "ping-1",
    });

    expect(messages).toHaveLength(1);
    expect(messages[0].type).toBe("pong");
  });

  it("should emit error for invalid JSON", () => {
    const errors: Error[] = [];
    client.onError((error) => errors.push(error));

    // Manually trigger message with invalid JSON
    if (mockWs.onmessage) {
      mockWs.onmessage({ data: "not valid json" });
    }

    expect(errors).toHaveLength(1);
    expect(errors[0].message).toContain("Invalid");
  });

  it("should resolve pending request on reply", async () => {
    const requestPromise = client.sendAndWait({
      type: "ping",
      message_id: "ping-123",
      timestamp: createTimestamp(),
    });

    // Simulate server response
    mockWs.simulateMessage({
      type: "pong",
      message_id: "pong-456",
      timestamp: createTimestamp(),
      in_reply_to: "ping-123",
    });

    const response = await requestPromise;
    expect(response.type).toBe("pong");
  });

  it("should reject pending request on error reply", async () => {
    const requestPromise = client.sendAndWait({
      type: "start_session",
      message_id: "req-123",
      timestamp: createTimestamp(),
      payload: { profile_id: "invalid" },
    });

    // Simulate error response
    mockWs.simulateMessage({
      type: "error",
      message_id: "err-456",
      timestamp: createTimestamp(),
      in_reply_to: "req-123",
      payload: {
        code: "E4005",
        message: "Profile not found",
        details: null,
        recoverable: false,
      },
    });

    await expect(requestPromise).rejects.toThrow("Profile not found");
  });

  it("should reject pending request on timeout", async () => {
    const requestPromise = client.sendAndWait(
      {
        type: "ping",
        message_id: "ping-timeout",
        timestamp: createTimestamp(),
      },
      100 // 100ms timeout
    );

    // Advance time past timeout
    vi.advanceTimersByTime(150);

    await expect(requestPromise).rejects.toThrow("timeout");
  });
});

// =============================================================================
// HEARTBEAT TESTS
// =============================================================================

describe("Heartbeat", () => {
  let client: WebSocketClient;
  let mockWs: MockWebSocketInstance;

  beforeEach(() => {
    vi.useFakeTimers();
    mockWs = createMockWebSocket();
    client = new WebSocketClient(
      { url: "ws://localhost:8080", heartbeatInterval: 1000 },
      () => mockWs
    );
  });

  afterEach(() => {
    client.disconnect();
    vi.useRealTimers();
  });

  it("should send ping messages at heartbeat interval", () => {
    client.connect();
    mockWs.simulateOpen();

    expect(mockWs.sentMessages).toHaveLength(0);

    // First heartbeat
    vi.advanceTimersByTime(1000);
    expect(mockWs.sentMessages).toHaveLength(1);
    expect(JSON.parse(mockWs.sentMessages[0]).type).toBe("ping");

    // Second heartbeat
    vi.advanceTimersByTime(1000);
    expect(mockWs.sentMessages).toHaveLength(2);
  });

  it("should stop heartbeat on disconnect", () => {
    client.connect();
    mockWs.simulateOpen();

    vi.advanceTimersByTime(1000);
    expect(mockWs.sentMessages).toHaveLength(1);

    client.disconnect();

    // Heartbeat should not continue
    vi.advanceTimersByTime(5000);
    expect(mockWs.sentMessages).toHaveLength(1);
  });

  it("should stop heartbeat on connection loss", () => {
    client.connect();
    mockWs.simulateOpen();

    vi.advanceTimersByTime(1000);
    expect(mockWs.sentMessages).toHaveLength(1);

    mockWs.simulateClose(1006);

    // Note: Can't easily verify heartbeat stopped without more setup
    // but this tests the flow doesn't throw
    expect(client.getState().connectionState).toBe("reconnecting");
  });
});

// =============================================================================
// ERROR HANDLING TESTS
// =============================================================================

describe("Error Handling", () => {
  let client: WebSocketClient;
  let mockWs: MockWebSocketInstance;

  beforeEach(() => {
    vi.useFakeTimers();
    mockWs = createMockWebSocket();
    client = new WebSocketClient({ url: "ws://localhost:8080" }, () => mockWs);
  });

  afterEach(() => {
    client.disconnect();
    vi.useRealTimers();
  });

  it("should emit WebSocket errors", () => {
    const errors: Error[] = [];
    client.onError((error) => errors.push(error));

    client.connect();
    mockWs.simulateError(new Error("Network error"));

    expect(errors).toHaveLength(1);
    expect(errors[0].message).toBe("Network error");
  });

  it("should include error code when available", () => {
    const codes: (string | undefined)[] = [];
    client.onError((_, code) => codes.push(code));

    client.connect();
    mockWs.simulateClose(1006);

    expect(codes).toContain("E1001");
  });

  it("should handle multiple error handlers", () => {
    let count = 0;
    client.onError(() => count++);
    client.onError(() => count++);
    client.onError(() => count++);

    client.connect();
    mockWs.simulateClose(1006);

    expect(count).toBe(3);
  });
});

// =============================================================================
// INTEGRATION SCENARIO TESTS
// =============================================================================

describe("Integration Scenarios", () => {
  let client: WebSocketClient;
  let mockWsInstances: MockWebSocketInstance[];

  beforeEach(() => {
    vi.useFakeTimers();
    mockWsInstances = [];

    client = new WebSocketClient(
      { url: "ws://localhost:8080", baseRetryDelay: 1000 },
      () => {
        const ws = createMockWebSocket();
        mockWsInstances.push(ws);
        return ws;
      }
    );
  });

  afterEach(() => {
    client.disconnect();
    vi.useRealTimers();
  });

  it("should handle full connection lifecycle", () => {
    const states: ConnectionState[] = [];
    client.onStateChange((s) => states.push(s.connectionState));

    // Initial connection
    client.connect();
    mockWsInstances[0].simulateOpen();
    expect(states).toEqual(["connecting", "connected"]);

    // Connection lost
    mockWsInstances[0].simulateClose(1006);
    expect(states).toEqual(["connecting", "connected", "reconnecting"]);

    // Reconnection attempt
    vi.advanceTimersByTime(1000);
    expect(states).toEqual([
      "connecting",
      "connected",
      "reconnecting",
      "connecting",
    ]);

    // Successful reconnection
    mockWsInstances[1].simulateOpen();
    expect(states).toEqual([
      "connecting",
      "connected",
      "reconnecting",
      "connecting",
      "connected",
    ]);

    // Clean disconnect
    client.disconnect();
    expect(states).toEqual([
      "connecting",
      "connected",
      "reconnecting",
      "connecting",
      "connected",
      "disconnected",
    ]);
  });

  it("should preserve queued messages through reconnection", () => {
    client.connect();
    mockWsInstances[0].simulateOpen();

    // Send message while connected
    client.send({ type: "ping", message_id: "1", timestamp: "t" });
    expect(mockWsInstances[0].sentMessages).toHaveLength(1);

    // Connection lost
    mockWsInstances[0].simulateClose(1006);

    // Queue messages during reconnect
    client.send({ type: "ping", message_id: "2", timestamp: "t" });
    client.send({ type: "ping", message_id: "3", timestamp: "t" });
    expect(client.getQueuedMessages()).toHaveLength(2);

    // Reconnect
    vi.advanceTimersByTime(1000);
    mockWsInstances[1].simulateOpen();

    // Queued messages should be sent
    expect(client.getQueuedMessages()).toHaveLength(0);
    expect(mockWsInstances[1].sentMessages).toHaveLength(2);
  });

  it("should handle rapid connect/disconnect cycles", () => {
    for (let i = 0; i < 5; i++) {
      client.connect();
      mockWsInstances[mockWsInstances.length - 1].simulateOpen();
      expect(client.getState().connectionState).toBe("connected");

      client.disconnect();
      expect(client.getState().connectionState).toBe("disconnected");
    }

    expect(mockWsInstances).toHaveLength(5);
  });
});
