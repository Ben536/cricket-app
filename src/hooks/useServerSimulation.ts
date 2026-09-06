/**
 * Server Simulation Hook
 *
 * Provides simulation that uses Pi server when connected,
 * falls back to local gameEngine when offline.
 *
 * Connection lifecycle is guarded by a generation token: every connect() and
 * disconnect() bumps the generation, and all async continuations / socket
 * handlers no-op if their generation is stale. This prevents the classic
 * flaky-WiFi failure modes: parallel discovery races creating zombie sockets,
 * intentional disconnects being "auto-reconnected", and orphaned timers.
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { simulateDelivery as localSimulate, calculateTrajectory, type SimulationResult } from '../gameEngine'
import type { FielderConfig } from '../gameEngine'
import { getServerUrl, discoverServer, saveLastWorkingUrl, DEFAULT_HOST } from '../api/config'

type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting' | 'discovering'

// Pending simulate request: carries a local-engine fallback so a dropped
// connection or timeout degrades to an offline result instead of an error.
interface PendingRequest {
  resolve: (result: SimulationResult) => void
  reject: (error: Error) => void
  timeout: number
  fallback: () => SimulationResult
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
  /** Discovery/connection status message */
  statusMessage: string | null
  /** Error message if any */
  error: string | null
  /** Manually reconnect */
  reconnect: () => void
  /** Connected server URL */
  serverUrl: string | null
  /**
   * This page is https:// so the browser will never allow a ws:// socket to
   * the Pi. No amount of retrying helps - the user has to open the app from
   * the Pi instead. Surfaced prominently rather than as a generic "Offline".
   */
  mixedContentBlocked: boolean
}

const HEARTBEAT_INTERVAL_MS = 20000
const HEARTBEAT_TIMEOUT_MS = 45000 // no inbound traffic for this long = dead link
const RECONNECT_DELAY_MS = 5000
const DISCOVERY_RETRY_MS = 10000
const SIMULATE_TIMEOUT_MS = 5000
const GENERIC_TIMEOUT_MS = 10000

function generateMessageId(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

/** https pages cannot open ws:// sockets (mixed content) - detect it up-front
 * so the user gets an actionable message instead of silent discovery failure. */
function isMixedContentBlocked(): boolean {
  return window.location.protocol === 'https:'
}

export function useServerSimulation(): UseServerSimulationResult {
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected')
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [serverUrl, setServerUrl] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<number | null>(null)
  const heartbeatIntervalRef = useRef<number | null>(null)
  const lastInboundRef = useRef<number>(0)
  const generationRef = useRef(0)
  const connectingRef = useRef(false)
  const pendingRequestsRef = useRef<Map<string, PendingRequest>>(new Map())
  const genericPendingRef = useRef<Map<string, GenericPendingRequest>>(new Map())

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }
  }, [])

  const stopHeartbeat = useCallback(() => {
    if (heartbeatIntervalRef.current) {
      clearInterval(heartbeatIntervalRef.current)
      heartbeatIntervalRef.current = null
    }
  }, [])

  /** Settle all in-flight requests: simulations degrade to the local engine,
   * generic requests reject (their callers show the error inline). */
  const settlePendingOnDisconnect = useCallback(() => {
    pendingRequestsRef.current.forEach((req) => {
      clearTimeout(req.timeout)
      try {
        req.resolve(req.fallback())
      } catch (e) {
        req.reject(e instanceof Error ? e : new Error('Local simulation failed'))
      }
    })
    pendingRequestsRef.current.clear()

    genericPendingRef.current.forEach((req) => {
      clearTimeout(req.timeout)
      req.reject(new Error('Connection closed'))
    })
    genericPendingRef.current.clear()
  }, [])

  /** Detach handlers and close the socket without triggering reconnect logic. */
  const teardownSocket = useCallback(() => {
    const ws = wsRef.current
    if (ws) {
      ws.onopen = null
      ws.onclose = null
      ws.onerror = null
      ws.onmessage = null
      try { ws.close() } catch { /* already closed */ }
      wsRef.current = null
    }
    stopHeartbeat()
  }, [stopHeartbeat])

  const connectToUrl = useCallback((url: string, gen: number) => {
    const isStale = () => gen !== generationRef.current

    try {
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        if (isStale()) { try { ws.close() } catch { /* noop */ } return }
        setConnectionState('connected')
        setStatusMessage(`Connected to ${url}`)
        setServerUrl(url)
        setError(null)
        saveLastWorkingUrl(url)
        lastInboundRef.current = Date.now()

        // Heartbeat: detect half-open links (phone wandered out of AP range)
        // and force a fast reconnect instead of waiting on OS TCP timeouts.
        stopHeartbeat()
        heartbeatIntervalRef.current = window.setInterval(() => {
          if (isStale() || ws.readyState !== WebSocket.OPEN) return
          if (Date.now() - lastInboundRef.current > HEARTBEAT_TIMEOUT_MS) {
            console.warn('Heartbeat timeout, forcing reconnect')
            ws.close() // onclose handler schedules the reconnect
            return
          }
          ws.send(JSON.stringify({
            type: 'ping',
            message_id: generateMessageId(),
            timestamp: new Date().toISOString(),
            payload: {},
          }))
        }, HEARTBEAT_INTERVAL_MS)

        // Let stream consumers (RadarVisualizer) re-establish subscriptions
        window.dispatchEvent(new CustomEvent('server-reconnected'))
      }

      ws.onclose = () => {
        if (isStale()) return
        setConnectionState('disconnected')
        wsRef.current = null
        setServerUrl(null)
        stopHeartbeat()
        settlePendingOnDisconnect()

        // Auto-reconnect after a delay (clear any timer first)
        clearReconnectTimer()
        reconnectTimeoutRef.current = window.setTimeout(() => {
          if (isStale()) return
          setConnectionState('reconnecting')
          void connectRef.current()
        }, RECONNECT_DELAY_MS)
      }

      ws.onerror = () => {
        if (isStale()) return
        setError('Connection failed')
      }

      ws.onmessage = (event) => {
        if (isStale()) return
        lastInboundRef.current = Date.now()
        try {
          const data = JSON.parse(event.data)

          // Handle simulate_result response
          if (data.type === 'simulate_result' && data.in_reply_to) {
            const pending = pendingRequestsRef.current.get(data.in_reply_to)
            if (pending) {
              clearTimeout(pending.timeout)
              pendingRequestsRef.current.delete(data.in_reply_to)
              // A reply with no simulation in it (malformed server) used to
              // throw here AFTER the timeout was cleared, so the promise never
              // settled and the ball was silently lost. Same rule as every
              // other failure path: fall back to the local engine, same seed.
              const simulation = data.payload?.simulation
              if (simulation && typeof simulation === 'object') {
                pending.resolve(simulation)
              } else {
                console.warn('simulate_result without a simulation payload; using local engine')
                try {
                  pending.resolve(pending.fallback())
                } catch (e) {
                  pending.reject(e instanceof Error ? e : new Error('Local simulation failed'))
                }
              }
            }
          }

          // Handle errors
          if (data.type === 'error') {
            const message = data.payload?.message || 'Server error'

            if (data.in_reply_to) {
              const pending = pendingRequestsRef.current.get(data.in_reply_to)
              if (pending) {
                // A server `error` reply to simulate_shot used to REJECT the
                // shot - the only resolution path that lost the ball (no over
                // entry, no wagon-wheel line), while a timeout or disconnect
                // degraded to the local engine. Same seed, same outcome:
                // resolve locally, exactly like the other failure paths.
                clearTimeout(pending.timeout)
                pendingRequestsRef.current.delete(data.in_reply_to)
                console.warn(`Server rejected simulate_shot (${message}); using local engine`)
                try {
                  pending.resolve(pending.fallback())
                } catch (e) {
                  pending.reject(e instanceof Error ? e : new Error('Local simulation failed'))
                }
              } else {
                setError(message)
              }

              // Also check generic pending
              const genericPending = genericPendingRef.current.get(data.in_reply_to)
              if (genericPending) {
                clearTimeout(genericPending.timeout)
                genericPendingRef.current.delete(data.in_reply_to)
                // Resolve with error response so caller can handle it
                genericPending.resolve(data)
              }
            } else {
              // Unsolicited error (not a reply): surface it globally
              setError(message)
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

          // Dispatch radar frames as custom events for visualizer
          if (data.type === 'radar_frame') {
            window.dispatchEvent(new CustomEvent('radar-frame', { detail: data.payload }))
          }
        } catch {
          // Ignore parse errors
        }
      }
    } catch (e) {
      if (isStale()) return
      setConnectionState('disconnected')
      setError(e instanceof Error ? e.message : 'Connection failed')
    }
  }, [clearReconnectTimer, settlePendingOnDisconnect, stopHeartbeat])

  // connect needs to reference itself (reconnect scheduling) - use a ref to
  // avoid a circular useCallback dependency.
  const connectRef = useRef<() => Promise<void>>(async () => {})

  const connect = useCallback(async () => {
    // Single-flight: a connect (including its discovery await) is already
    // running, or a socket is already open/connecting.
    if (connectingRef.current) return
    const ws = wsRef.current
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return

    if (isMixedContentBlocked()) {
      setConnectionState('disconnected')
      setError(
        'This page is https:// but the CricketRadar server only speaks ws:// - '
        + 'the browser blocks that combination. Open the app from the Pi instead: '
        + `http://${DEFAULT_HOST}:5173 (or the Pi's IP address)`
      )
      setStatusMessage(null)
      return // retrying cannot help; the user must switch origin
    }

    const gen = ++generationRef.current
    connectingRef.current = true
    clearReconnectTimer()

    try {
      // First try direct URL if configured
      const directUrl = getServerUrl()

      // Check if we have a URL param (skip discovery)
      const params = new URLSearchParams(window.location.search)
      if (params.has('server')) {
        setConnectionState('connecting')
        setStatusMessage(`Connecting to ${directUrl}...`)
        connectToUrl(directUrl, gen)
        return
      }

      // Auto-discover
      setConnectionState('discovering')
      setError(null)

      const discoveredUrl = await discoverServer((msg) => {
        if (gen === generationRef.current) setStatusMessage(msg)
      })
      if (gen !== generationRef.current) return // superseded while discovering

      if (discoveredUrl) {
        setConnectionState('connecting')
        connectToUrl(discoveredUrl, gen)
      } else {
        setConnectionState('disconnected')
        setError('No server found. Check that CricketRadar is powered on.')
        setStatusMessage(null)

        // Retry discovery after 10s
        reconnectTimeoutRef.current = window.setTimeout(() => {
          if (gen !== generationRef.current) return
          void connectRef.current()
        }, DISCOVERY_RETRY_MS)
      }
    } finally {
      // Only the connect that OWNS the current generation may release the
      // single-flight latch. A superseded discovery (reconnect() ran while it
      // was awaiting) must not clear a latch that a newer connect holds.
      if (gen === generationRef.current) connectingRef.current = false
    }
  }, [clearReconnectTimer, connectToUrl])

  connectRef.current = connect

  const disconnect = useCallback(() => {
    generationRef.current++ // invalidate all handlers/timers/continuations
    // Release the single-flight latch too. "Save & Reconnect" during
    // discovery calls disconnect() then connect(); with the latch still held
    // by the orphaned discovery, connect() returned immediately, the orphan
    // then bailed on its stale generation (skipping both the error message
    // and the retry timer), and the app sat at `disconnected` with no
    // socket, no error and no retry until a second tap.
    connectingRef.current = false
    clearReconnectTimer()
    teardownSocket()
    settlePendingOnDisconnect()
    setConnectionState('disconnected')
  }, [clearReconnectTimer, teardownSocket, settlePendingOnDisconnect])

  /** Manual reconnect: tear down whatever exists, then connect fresh. */
  const reconnect = useCallback(() => {
    disconnect()
    void connect()
  }, [disconnect, connect])

  // Connect on mount
  useEffect(() => {
    void connect()
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
    // One seed per shot, shared by the server request AND the local fallback:
    // both engines use the same PRNG, so the outcome is identical whichever
    // one ends up answering (timeout/disconnect degradation is seamless).
    const shotSeed = Math.floor(Math.random() * 4294967296) >>> 0

    const runLocal = (): SimulationResult => {
      const trajectory = calculateTrajectory(exitSpeed, horizontalAngle, verticalAngle)
      const result = localSimulate(
        exitSpeed, horizontalAngle, verticalAngle,
        trajectory.landing_x, trajectory.landing_y,
        trajectory.projected_distance, trajectory.max_height,
        fieldConfig, boundaryDistance, difficulty, shotSeed
      )
      return { ...result, trajectory } as SimulationResult
    }

    const ws = wsRef.current

    // If connected, use server
    if (ws && ws.readyState === WebSocket.OPEN) {
      return new Promise((resolve, reject) => {
        const messageId = generateMessageId()

        // Timeout: degrade to the local engine rather than losing the shot
        const timeout = window.setTimeout(() => {
          pendingRequestsRef.current.delete(messageId)
          console.warn('Server simulation timeout, using local engine')
          resolve(runLocal())
        }, SIMULATE_TIMEOUT_MS)

        // Store pending request (fallback also fires if the socket closes)
        pendingRequestsRef.current.set(messageId, { resolve, reject, timeout, fallback: runLocal })

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
            seed: shotSeed,
          },
        }

        ws.send(JSON.stringify(message))
      })
    }

    // Not connected - use local engine
    return runLocal()
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
      }, GENERIC_TIMEOUT_MS)

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
    statusMessage,
    error,
    reconnect,
    serverUrl,
    mixedContentBlocked: isMixedContentBlocked(),
  }
}
