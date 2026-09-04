// @vitest-environment jsdom
/**
 * Connection lifecycle of the server-simulation hook, driven through a fake
 * WebSocket. Two regressions from the 2026-08 review are pinned here:
 *
 *  T1.11  "Save & Reconnect" while discovery was in flight left the app at
 *         `disconnected` with no socket, no error and no retry timer.
 *  T3.5   A server `error` reply to simulate_shot rejected the shot, losing
 *         the ball, where every other failure path fell back to the local
 *         engine with the same seed.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

import { useServerSimulation } from '../hooks/useServerSimulation'
import type { FielderConfig } from '../gameEngine'

// ---------------------------------------------------------------------------
// Fake WebSocket: the hook only uses the browser-API surface below
// ---------------------------------------------------------------------------

class FakeWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  static instances: FakeWebSocket[] = []

  readonly url: string
  readyState = FakeWebSocket.CONNECTING
  sent: string[] = []
  onopen: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    if (this.readyState === FakeWebSocket.CLOSED) return
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({} as CloseEvent)
  }

  // --- test controls
  open() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.({} as Event)
  }

  receive(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent)
  }

  lastSent<T = Record<string, unknown>>(): T {
    return JSON.parse(this.sent[this.sent.length - 1]) as T
  }
}

type Hook = ReturnType<typeof useServerSimulation>
let latest: Hook | null = null

function Harness() {
  latest = useServerSimulation()
  return null
}

let root: Root | null = null
let container: HTMLDivElement | null = null

async function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root!.render(<Harness />)
  })
}

const flush = () => act(async () => { await Promise.resolve() })

/** Drive discovery to a live connection: the probe socket opens (and is
 * closed by tryConnect), then the real socket opens. */
async function connectFully(): Promise<FakeWebSocket> {
  const probe = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
  await act(async () => { probe.open() })
  await flush()
  const live = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
  expect(live).not.toBe(probe)
  await act(async () => { live.open() })
  await flush()
  expect(latest!.connectionState).toBe('connected')
  return live
}

const FIELD: FielderConfig[] = [{ x: 20, y: 30, name: 'mid-off' }]

beforeEach(() => {
  ;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
  FakeWebSocket.instances = []
  ;(globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket
  localStorage.clear()
  latest = null
})

afterEach(async () => {
  await act(async () => { root?.unmount() })
  container?.remove()
  root = null
  container = null
})

describe('useServerSimulation', () => {
  it('discovers, then connects, then reports connected', async () => {
    await mount()
    expect(latest!.connectionState).toBe('discovering')
    expect(FakeWebSocket.instances).toHaveLength(1)
    const live = await connectFully()
    expect(live.readyState).toBe(FakeWebSocket.OPEN)
    expect(latest!.serverUrl).toBe(live.url)
  })

  it('T1.11: reconnect() during discovery starts a fresh discovery and connects', async () => {
    await mount()
    const orphanProbe = FakeWebSocket.instances[0]
    expect(latest!.connectionState).toBe('discovering')

    // Save & Reconnect while the first probe is still pending
    await act(async () => { latest!.reconnect() })
    // A NEW probe must exist. Before the fix, connect() saw the single-flight
    // latch still held by the orphaned discovery and returned immediately.
    expect(FakeWebSocket.instances).toHaveLength(2)
    const freshProbe = FakeWebSocket.instances[1]
    expect(freshProbe).not.toBe(orphanProbe)

    // The orphan resolving late must be ignored (no zombie socket)
    await act(async () => { orphanProbe.open() })
    await flush()
    expect(FakeWebSocket.instances).toHaveLength(2)

    // The fresh discovery completes and connects
    await act(async () => { freshProbe.open() })
    await flush()
    const live = FakeWebSocket.instances[2]
    await act(async () => { live.open() })
    await flush()
    expect(latest!.connectionState).toBe('connected')
    expect(latest!.error).toBeNull()
  })

  it('T3.5: a server error reply falls back to the local engine with the same seed', async () => {
    await mount()
    const live = await connectFully()

    let resolved: Awaited<ReturnType<Hook['simulateAsync']>> | null = null
    let rejected: unknown = null
    const promise = latest!.simulateAsync(90, 20, 15, FIELD, 70, 'medium')
      .then((r) => { resolved = r })
      .catch((e) => { rejected = e })
    await flush()

    const sent = live.lastSent<{ type: string; message_id: string; payload: { seed: number } }>()
    expect(sent.type).toBe('simulate_shot')

    await act(async () => {
      live.receive({
        type: 'error',
        message_id: 'x',
        in_reply_to: sent.message_id,
        payload: { code: 'E3004', message: 'Invalid value for field', recoverable: false },
      })
    })
    await promise
    await flush()

    expect(rejected).toBeNull()
    expect(resolved).not.toBeNull()
    expect(resolved!.seed).toBe(sent.payload.seed)
    expect(typeof resolved!.outcome).toBe('string')
    // A handled reply must not raise the global error banner
    expect(latest!.error).toBeNull()
  })

  it('a simulate_result with no simulation in it falls back locally instead of hanging', async () => {
    await mount()
    const live = await connectFully()
    let resolved: Awaited<ReturnType<Hook['simulateAsync']>> | null = null
    const promise = latest!.simulateAsync(90, 20, 15, FIELD, 70, 'medium').then((r) => { resolved = r })
    await flush()
    const sent = live.lastSent<{ message_id: string; payload: { seed: number } }>()
    // Timeout was cleared and the entry deleted BEFORE reading payload.simulation,
    // so a malformed reply used to leave the promise pending forever.
    await act(async () => {
      live.receive({ type: 'simulate_result', message_id: 'm', in_reply_to: sent.message_id })
    })
    await promise
    expect(resolved).not.toBeNull()
    expect(resolved!.seed).toBe(sent.payload.seed)
  })

  it('resolves a simulate_result reply with the server payload', async () => {
    await mount()
    const live = await connectFully()
    const promise = latest!.simulateAsync(90, 20, 15, FIELD, 70, 'medium')
    await flush()
    const sent = live.lastSent<{ message_id: string }>()
    const simulation = { outcome: '4', runs: 4, is_boundary: true, is_aerial: false, fielder_involved: null, end_position: { x: 1, y: 2 }, description: 'four', seed: 5 }
    await act(async () => {
      live.receive({ type: 'simulate_result', message_id: 'y', in_reply_to: sent.message_id, payload: { simulation } })
    })
    const r = await promise
    expect(r.outcome).toBe('4')
    expect(r.runs).toBe(4)
  })

  it('an unsolicited error still surfaces globally', async () => {
    await mount()
    const live = await connectFully()
    await act(async () => {
      live.receive({ type: 'error', message_id: 'z', in_reply_to: null, payload: { message: 'Server at capacity' } })
    })
    expect(latest!.error).toBe('Server at capacity')
  })
})
