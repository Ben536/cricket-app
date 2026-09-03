/**
 * DataGatheringModal - capture raw radar data at the nets, labelled for tuning.
 *
 * Three capture types:
 *   - bowling: ball goes by, no batsman   (pure ball signature)
 *   - batting: batsman, no ball           (bat/body signature)
 *   - both:    ball bowled and hit        (real shot)
 *
 * For "both", each ball is labelled with the DIRECTION it went via a tappable
 * wagon-wheel -> ground truth for the horizontal angle the system must later
 * derive from radar. Frames + annotations stream to a crash-safe JSONL on the Pi.
 *
 * Direction convention: 0deg = toward bowler, +90 = leg side, -90 = off side
 * (right-handed batter). NOTE: the game engine's simulate `angle` input uses
 * the OPPOSITE sign (+off/-leg) - flip the sign when tuning against this data.
 *
 * The recording lives on the server: closing this modal does NOT stop it, and
 * reopening re-attaches to the in-progress session via get_recording_status.
 */

import { useState, useEffect, useCallback } from 'react'
import type { MouseEvent } from 'react'
import './RecordingModal.css'

type SessionType = 'bowling' | 'batting' | 'both'

interface DataGatheringModalProps {
  isConnected: boolean
  onClose: () => void
  sendMessage: (type: string, payload: Record<string, unknown>) => Promise<unknown>
}

interface StatusPayload {
  is_recording?: boolean
  frame_count?: number
  annotation_count?: number
  max_duration?: number
  current_session_type?: string
  current_start_time?: string
  mock?: boolean
  // Why the last recording stopped ITSELF (card full / I/O error), if it did
  last_error?: string | null
}

// Wagon-wheel geometry (SVG viewBox is SIZE x SIZE, batter at centre)
const SIZE = 300
const CX = SIZE / 2
const CY = SIZE / 2
const R = 130

const DURATIONS = [2, 5, 10] // minutes

export function DataGatheringModal({ isConnected, onClose, sendMessage }: DataGatheringModalProps) {
  const [sessionType, setSessionType] = useState<string>('both')
  const [maxSeconds, setMaxSeconds] = useState(5 * 60)
  const [isRecording, setIsRecording] = useState(false)
  const [isMock, setIsMock] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [frameCount, setFrameCount] = useState(0)
  const [markCount, setMarkCount] = useState(0)
  const [lastMark, setLastMark] = useState<string | null>(null)
  const [tap, setTap] = useState<{ x: number; y: number } | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Re-attach to an in-progress recording (the server keeps recording if the
  // modal was closed or the page reloaded mid-session).
  useEffect(() => {
    if (!isConnected) return
    let cancelled = false
    sendMessage('get_recording_status', {})
      .then((resp) => {
        if (cancelled) return
        const p = (resp as { payload?: StatusPayload })?.payload
        if (!p?.is_recording) return
        setIsRecording(true)
        setIsMock(p.mock ?? false)
        setFrameCount(p.frame_count ?? 0)
        setMarkCount(p.annotation_count ?? 0)
        if (p.max_duration) setMaxSeconds(p.max_duration)
        if (p.current_session_type) setSessionType(p.current_session_type)
        if (p.current_start_time) {
          const started = Date.parse(p.current_start_time)
          if (!Number.isNaN(started)) {
            setElapsed(Math.max(0, Math.floor((Date.now() - started) / 1000)))
          }
        }
        setLastMark('Re-attached to recording in progress')
      })
      .catch(() => {})
    return () => { cancelled = true }
    // mount-only: adopt whatever session exists when the modal opens
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Elapsed-time ticker
  useEffect(() => {
    if (!isRecording) return
    const timer = window.setInterval(() => setElapsed((e) => e + 1), 1000)
    return () => window.clearInterval(timer)
  }, [isRecording])

  // Poll the server for frame/mark counts and auto-stop detection
  useEffect(() => {
    if (!isRecording) return
    const poll = window.setInterval(() => {
      sendMessage('get_recording_status', {})
        .then((resp) => {
          const p = (resp as { payload?: StatusPayload })?.payload
          if (!p) return
          setFrameCount(p.frame_count ?? 0)
          setMarkCount(p.annotation_count ?? 0)
          if (p.mock != null) setIsMock(p.mock)
          if (p.is_recording === false) {
            // Server stopped on its own: max duration, or a write failure
            // (card full) - the two must not read the same on the phone.
            setIsRecording(false)
            if (p.last_error) {
              setError(`Recording STOPPED - write failed: ${p.last_error}. The file is truncated; free space on the Pi.`)
            } else {
              setLastMark('Auto-stopped (max duration reached)')
            }
          }
        })
        .catch(() => {})
    }, 2000)
    return () => window.clearInterval(poll)
  }, [isRecording, sendMessage])

  const handleStart = async () => {
    if (!isConnected) { setError('Not connected to Pi'); return }
    setError(null); setLastMark(null); setTap(null)
    try {
      const resp = await sendMessage('start_recording', {
        session_type: sessionType,
        max_duration: maxSeconds,
      }) as { type: string; payload?: { message?: string; mock?: boolean; max_duration?: number } }
      if (resp.type === 'recording_started') {
        setIsRecording(true); setElapsed(0); setFrameCount(0); setMarkCount(0)
        setIsMock(resp.payload?.mock ?? false)
        // Server clamps the duration; trust its value for the countdown
        if (resp.payload?.max_duration) setMaxSeconds(resp.payload.max_duration)
      } else {
        setError(resp.payload?.message ?? 'Failed to start')
      }
    } catch (e) {
      setError(`Failed to start: ${e}`)
    }
  }

  const handleStop = async () => {
    setError(null)
    try {
      const resp = await sendMessage('stop_recording', {}) as {
        type: string
        payload?: { frame_count?: number; annotation_count?: number; duration_seconds?: number; mock?: boolean; message?: string; error?: string | null }
      }
      setIsRecording(false)
      if (resp.type === 'recording_stopped' && resp.payload) {
        setLastMark(
          `Saved${resp.payload.mock ? ' (MOCK DATA)' : ''}: ${resp.payload.duration_seconds?.toFixed(0)}s, ` +
          `${resp.payload.frame_count} frames, ${resp.payload.annotation_count} marks`
        )
        if (resp.payload.error) {
          setError(`Write failed during this recording: ${resp.payload.error}. The file is truncated.`)
        }
      } else if (resp.type === 'error') {
        setError(resp.payload?.message ?? 'Failed to stop')
      }
    } catch (e) {
      setError(`Failed to stop: ${e}`)
    }
  }

  const sendMark = useCallback((mark: Record<string, unknown>, label: string) => {
    sendMessage('add_annotation', mark)
      .then((resp) => {
        const p = (resp as { payload?: { annotation_count?: number } })?.payload
        if (p?.annotation_count != null) setMarkCount(p.annotation_count)
        else setMarkCount((c) => c + 1)
        setLastMark(label)
      })
      .catch((e) => setError(`Mark failed: ${e}`))
  }, [sendMessage])

  // Tap on the wagon-wheel -> direction the ball went
  const handleWheelTap = (e: MouseEvent<SVGSVGElement>) => {
    if (!isRecording) return
    const rect = e.currentTarget.getBoundingClientRect()
    const px = ((e.clientX - rect.left) / rect.width) * SIZE
    const py = ((e.clientY - rect.top) / rect.height) * SIZE
    const dx = px - CX            // +ve = right (leg, RH batter)
    const dy = CY - py            // +ve = up (toward bowler)
    // 0deg straight to bowler, +90 = leg (right), -90 = off (left), +/-180 = behind
    const directionDeg = Math.round(Math.atan2(dx, dy) * (180 / Math.PI) * 10) / 10
    const xn = Math.max(-1, Math.min(1, dx / R))
    const yn = Math.max(-1, Math.min(1, dy / R))
    setTap({ x: px, y: py })
    sendMark(
      { direction_deg: directionDeg, x: Math.round(xn * 100) / 100, y: Math.round(yn * 100) / 100 },
      `Ball -> ${directionDeg}deg`
    )
  }

  const fmt = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
  const progressPct = Math.min(100, (elapsed / maxSeconds) * 100)

  return (
    <>
      {/* While recording, tapping outside must not dismiss the modal (a stray
          tap at the nets would hide the marking UI mid-session) */}
      <div className="recording-overlay" onClick={isRecording ? undefined : onClose} />
      <div className="recording-modal">
        <div className="recording-header">
          <h2>Data Gathering</h2>
          <button className="close-btn" onClick={onClose}>×</button>
        </div>

        <div className="recording-content">
          {!isConnected && <div className="recording-warning">Not connected to Pi server</div>}
          {isMock && (
            <div className="recording-warning">
              ⚠️ RADAR NOT DETECTED — this session records fabricated mock data,
              not real radar. Check the radar's USB connection, then stop and
              start again.
            </div>
          )}

          {/* Capture type */}
          <div className="recording-section">
            <label>Capture Type</label>
            <div className="session-type-buttons">
              {([
                ['bowling', 'Ball only'],
                ['batting', 'Batsman only'],
                ['both', 'Ball + hit'],
              ] as [SessionType, string][]).map(([type, lbl]) => (
                <button
                  key={type}
                  className={`type-btn ${sessionType === type ? 'active' : ''}`}
                  onClick={() => setSessionType(type)}
                  disabled={isRecording}
                >
                  {lbl}
                </button>
              ))}
            </div>
          </div>

          {/* Duration */}
          {!isRecording && (
            <div className="recording-section">
              <label>Max Duration</label>
              <div className="session-type-buttons">
                {DURATIONS.map((m) => (
                  <button
                    key={m}
                    className={`type-btn ${maxSeconds === m * 60 ? 'active' : ''}`}
                    onClick={() => setMaxSeconds(m * 60)}
                  >
                    {m} min
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Status */}
          <div className="recording-section">
            {isRecording ? (
              <div className="recording-status recording">
                <div className="recording-indicator"><span className="rec-dot" /> REC</div>
                <div className="recording-time">{fmt(elapsed)} / {fmt(maxSeconds)}</div>
                <div className="progress-bar"><div className="progress-fill" style={{ width: `${progressPct}%` }} /></div>
                <div style={{ marginTop: 8, fontSize: 13 }}>
                  {frameCount} frames &middot; {markCount} balls marked
                </div>
              </div>
            ) : (
              <div className="recording-status ready">Ready to record</div>
            )}
          </div>

          {/* Marking area */}
          {isRecording && sessionType === 'both' && (
            <div className="recording-section">
              <label>Tap where the ball went</label>
              <svg
                viewBox={`0 0 ${SIZE} ${SIZE}`}
                onClick={handleWheelTap}
                style={{ width: '100%', maxWidth: 320, touchAction: 'manipulation', background: '#0b3d2e', borderRadius: 8, display: 'block', margin: '0 auto' }}
              >
                <circle cx={CX} cy={CY} r={R} fill="#0f5132" stroke="#2e8b57" strokeWidth={2} />
                <circle cx={CX} cy={CY} r={R * 0.5} fill="none" stroke="#2e8b57" strokeWidth={1} strokeDasharray="4 4" />
                {/* crosshair */}
                <line x1={CX} y1={CY - R} x2={CX} y2={CY + R} stroke="#2e8b57" strokeWidth={1} />
                <line x1={CX - R} y1={CY} x2={CX + R} y2={CY} stroke="#2e8b57" strokeWidth={1} />
                {/* batter */}
                <circle cx={CX} cy={CY} r={5} fill="#ffd700" />
                {/* labels */}
                <text x={CX} y={CY - R + 16} fill="#cde" fontSize={13} textAnchor="middle">Bowler</text>
                <text x={CX} y={CY + R - 6} fill="#cde" fontSize={13} textAnchor="middle">Keeper</text>
                <text x={CX - R + 18} y={CY + 4} fill="#cde" fontSize={13} textAnchor="middle">Off</text>
                <text x={CX + R - 18} y={CY + 4} fill="#cde" fontSize={13} textAnchor="middle">Leg</text>
                {tap && <line x1={CX} y1={CY} x2={tap.x} y2={tap.y} stroke="#ffd700" strokeWidth={2} />}
                {tap && <circle cx={tap.x} cy={tap.y} r={6} fill="#ff4136" />}
              </svg>
              <p className="note" style={{ textAlign: 'center', marginTop: 6 }}>
                Off/Leg shown for a right-handed batter. Tap = one ball.
              </p>
            </div>
          )}

          {isRecording && sessionType !== 'both' && (
            <div className="recording-section">
              <button
                className="record-btn start"
                onClick={() => sendMark({ label: sessionType === 'bowling' ? 'ball' : 'swing' }, 'Marked')}
              >
                Mark {sessionType === 'bowling' ? 'Ball' : 'Swing'}
              </button>
            </div>
          )}

          {/* Start / Stop */}
          <div className="recording-section">
            {isRecording ? (
              <>
                <button className="record-btn stop" onClick={handleStop}>Stop &amp; Save</button>
                <p className="note" style={{ textAlign: 'center', marginTop: 6 }}>
                  Closing this window keeps recording — reopen to re-attach.
                </p>
              </>
            ) : (
              <button className="record-btn start" onClick={handleStart} disabled={!isConnected}>
                Start Recording
              </button>
            )}
          </div>

          {error && <div className="recording-error">{error}</div>}
          {lastMark && <div className="recording-success">{lastMark}</div>}

          <div className="recording-section instructions">
            <p><strong>Ball only:</strong> bowl past, no batsman — pure ball signature.</p>
            <p><strong>Batsman only:</strong> shadow shots, no ball — bat/body signature.</p>
            <p><strong>Ball + hit:</strong> real shots — tap the wheel to log the direction each ball went.</p>
            <p className="note">Saved crash-safe to the Pi as you go (one file per session).</p>
          </div>
        </div>
      </div>
    </>
  )
}
