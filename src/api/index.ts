/**
 * Cricket App API Module
 *
 * WebSocket client and React hooks for communication with the game engine.
 */

// Types
export * from './types';

// WebSocket client
export { CricketWebSocket, getWebSocket, resetWebSocket } from './websocket';

// React hooks
export { useConnection, useWebSocketClient } from './hooks/useConnection';
export type { UseConnectionResult } from './hooks/useConnection';

export { useGameState } from './hooks/useGameState';
export type { GameState, UseGameStateResult } from './hooks/useGameState';
