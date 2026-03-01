/**
 * Server Simulation Hook
 *
 * Provides simulation that uses Pi server when connected,
 * falls back to local gameEngine when offline.
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { simulateDelivery as localSimulate, calculateTrajectory, type SimulationResult } from '../gameEngine'
import type { FielderConfig } from '../gameEngine'
import { getServerUrl } from '../api/config'

type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting'

// Pending request waiting for server response
interface PendingRequest {
  resolve: (result: SimulationResult) => void
  reject: (error: Error) => void
  timeout: number
}

// Generic pending request for any message type
interface GenericPendingRequest {
  resolve: (result: unknown) => void
  reject: (error: Error) => void
  timeout: number
}

interface UseServerSimulationResult {
  /** Run simulation - returns Promise, uses server if connected, local if not */
  simulateAsync: (
    exitSpeed: number,
    horizontalAngle: number,
    verticalAngle: number,
    fieldConfig: FielderConfig[],
    boundaryDistance?: number,
    difficulty?: 'easy' | 'medium' | 'hard'
  ) => Promise<SimulationResult>
  /** Synchronous simulation (always local) - for backwards compatibility */
  simulate: typeof localSimulate
  /** Calculate trajectory (always local) */
  calculateTrajectory: typeof calculateTrajectory
  /** Send a generic message to the server - returns Promise with response */
  sendMessage: (type: string, payload: Record<string, unknown>) => Promise<unknown>
  /** Whether connected to server */
  isConnected: boolean
  /** Current connection state */
  connectionState: ConnectionState
  /** Error message if any */
  error: string | null
  /** Manually reconnect */
  reconnect: () => void
}

function generateMessageId(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

export function useServerSimulation(): UseServerSimulationResult {
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected')
  const [error, setError] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<number | null>(null)
  const pendingRequestsRef = useRef<Map<string, PendingRequest>>(new Map())
  const genericPendingRef = useRef<Map<string, GenericPendingRequest>>(new Map())

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const url = getServerUrl()
    setConnectionState('connecting')
    setError(null)

    try {
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        setConnectionState('connected')
        setError(null)
      }

      ws.onclose = () => {
        setConnectionState('disconnected')
        wsRef.current = null

        // Reject all pending requests
        pendingRequestsRef.current.forEach((req) => {
          clearTimeout(req.timeout)
          req.reject(new Error('Connection closed'))
        })
        pendingRequestsRef.current.clear()

        // Reject all generic pending requests
        genericPendingRef.current.forEach((req) => {
          clearTimeout(req.timeout)
          req.reject(new Error('Connection closed'))
        })
        genericPendingRef.current.clear()

        // Auto-reconnect after 5s
        reconnectTimeoutRef.current = window.setTimeout(() => {
          setConnectionState('reconnecting')
          connect()
        }, 5000)
      }

      ws.onerror = () => {
        setError('Connection failed')
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)

          // Handle simulate_result response
          if (data.type === 'simulate_result' && data.in_reply_to) {
            const pending = pendingRequestsRef.current.get(data.in_reply_to)
            if (pending) {
              clearTimeout(pending.timeout)
              pendingRequestsRef.current.delete(data.in_reply_to)
              pending.resolve(data.payload.simulation)
            }
          }

          // Handle errors
          if (data.type === 'error') {
            setError(data.payload?.message || 'Server error')

            // Reject pending request if this is a reply
            if (data.in_reply_to) {
              const pending = pendingRequestsRef.current.get(data.in_reply_to)
              if (pending) {
                clearTimeout(pending.timeout)
                pendingRequestsRef.current.delete(data.in_reply_to)
                pending.reject(new Error(data.payload?.message || 'Server error'))
              }

              // Also check generic pending
              const genericPending = genericPendingRef.current.get(data.in_reply_to)
              if (genericPending) {
                clearTimeout(genericPending.timeout)
                genericPendingRef.current.delete(data.in_reply_to)
                // Resolve with error response so caller can handle it
                genericPending.resolve(data)
              }
            }
          }

          // Handle generic responses (recording, etc.)
          if (data.in_reply_to && !data.type?.startsWith('simulate')) {
            const genericPending = genericPendingRef.current.get(data.in_reply_to)
            if (genericPending) {
              clearTimeout(genericPending.timeout)
              genericPendingRef.current.delete(data.in_reply_to)
              genericPending.resolve(data)
            }
          }
        } catch {
          // Ignore parse errors
        }
      }
    } catch (e) {
      setConnectionState('disconnected')
      setError(e instanceof Error ? e.message : 'Connection failed')
    }
  }, [])

  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    setConnectionState('disconnected')
  }, [])

  // Connect on mount
  useEffect(() => {
    connect()
    return () => disconnect()
  }, [connect, disconnect])

  // Async simulate - uses server when connected
  const simulateAsync = useCallback(async (
    exitSpeed: number,
    horizontalAngle: number,
    verticalAngle: number,
    fieldConfig: FielderConfig[],
    boundaryDistance: number = 70.0,
    difficulty: 'easy' | 'medium' | 'hard' = 'medium'
  ): Promise<SimulationResult> => {
    const ws = wsRef.current

    // If connected, use server
    if (ws && ws.readyState === WebSocket.OPEN) {
      return new Promise((resolve, reject) => {
        const messageId = generateMessageId()

        // Set timeout for response
        const timeout = window.setTimeout(() => {
          pendingRequestsRef.current.delete(messageId)
          // Fall back to local on timeout
          console.warn('Server simulation timeout, using local engine')
          const trajectory = calculateTrajectory(exitSpeed, horizontalAngle, verticalAngle)
          const result = localSimulate(
            exitSpeed, horizontalAngle, verticalAngle,
            trajectory.landing_x, trajectory.landing_y,
            trajectory.projected_distance, trajectory.max_height,
            fieldConfig, boundaryDistance, difficulty
          )
          resolve({ ...result, trajectory } as SimulationResult)
        }, 5000)

        // Store pending request
        pendingRequestsRef.current.set(messageId, { resolve, reject, timeout })

        // Send message
        const message = {
          type: 'simulate_shot',
          message_id: messageId,
          timestamp: new Date().toISOString(),
          payload: {
            exit_speed: exitSpeed,
            horizontal_angle: horizontalAngle,
            vertical_angle: verticalAngle,
            field_config: fieldConfig,
            boundary_distance: boundaryDistance,
            difficulty: difficulty,
          },
        }

        ws.send(JSON.stringify(message))
      })
    }

    // Not connected - use local engine
    const trajectory = calculateTrajectory(exitSpeed, horizontalAngle, verticalAngle)
    const result = localSimulate(
      exitSpeed, horizontalAngle, verticalAngle,
      trajectory.landing_x, trajectory.landing_y,
      trajectory.projected_distance, trajectory.max_height,
      fieldConfig, boundaryDistance, difficulty
    )
    return { ...result, trajectory } as SimulationResult
  }, [])

  // Sync simulate - always uses local (for backwards compatibility)
  const simulate = useCallback((
    exitSpeed: number,
    horizontalAngle: number,
    verticalAngle: number,
    landingX: number,
    landingY: number,
    projectedDistance: number,
    maxHeight: number,
    fieldConfig: FielderConfig[],
    boundaryDistance: number = 70.0,
    difficulty: 'easy' | 'medium' | 'hard' = 'medium'
  ) => {
    return localSimulate(
      exitSpeed, horizontalAngle, verticalAngle,
      landingX, landingY, projectedDistance, maxHeight,
      fieldConfig, boundaryDistance, difficulty
    )
  }, [])

  // Generic message sender for any message type
  const sendMessage = useCallback(async (
    type: string,
    payload: Record<string, unknown>
  ): Promise<unknown> => {
    const ws = wsRef.current

    if (!ws || ws.readyState !== WebSocket.OPEN) {
      throw new Error('Not connected to server')
    }

    return new Promise((resolve, reject) => {
      const messageId = generateMessageId()

      // Set timeout for response (10 seconds for generic messages)
      const timeout = window.setTimeout(() => {
        genericPendingRef.current.delete(messageId)
        reject(new Error('Request timeout'))
      }, 10000)

      // Store pending request
      genericPendingRef.current.set(messageId, { resolve, reject, timeout })

      // Send message
      const message = {
        type,
        message_id: messageId,
        timestamp: new Date().toISOString(),
        payload,
      }

      ws.send(JSON.stringify(message))
    })
  }, [])

  return {
    simulateAsync,
    simulate,
    calculateTrajectory,
    sendMessage,
    isConnected: connectionState === 'connected',
    connectionState,
    error,
    reconnect: connect,
  }
}
