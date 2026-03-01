/**
 * Server Simulation Hook
 *
 * Provides simulation that uses Pi server when connected,
 * falls back to local gameEngine when offline.
 *
 * Usage in App.tsx - just replace the direct import with this hook:
 *   const { simulate, isConnected, connectionState } = useServerSimulation()
 *   // simulate() has same signature as simulateDelivery()
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { simulateDelivery as localSimulate, calculateTrajectory } from '../gameEngine'
import type { FielderConfig } from '../gameEngine'
import { getServerUrl } from '../api/config'

type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting'

interface UseServerSimulationResult {
  /** Run simulation - uses server if connected, local if not */
  simulate: typeof localSimulate
  /** Calculate trajectory (always local, it's just math) */
  calculateTrajectory: typeof calculateTrajectory
  /** Whether connected to server */
  isConnected: boolean
  /** Current connection state */
  connectionState: ConnectionState
  /** Error message if any */
  error: string | null
  /** Manually reconnect */
  reconnect: () => void
}

export function useServerSimulation(): UseServerSimulationResult {
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected')
  const [error, setError] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<number | null>(null)

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
        // Handle messages if needed
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'error') {
            setError(data.payload?.message || 'Server error')
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

  // Simulate function - uses local engine for now
  // TODO: When server protocol is ready, send to server and await response
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
    // For now, always use local simulation
    // Server simulation will be added when we implement the simulate_shot message
    return localSimulate(
      exitSpeed,
      horizontalAngle,
      verticalAngle,
      landingX,
      landingY,
      projectedDistance,
      maxHeight,
      fieldConfig,
      boundaryDistance,
      difficulty
    )
  }, [])

  return {
    simulate,
    calculateTrajectory,
    isConnected: connectionState === 'connected',
    connectionState,
    error,
    reconnect: connect,
  }
}
