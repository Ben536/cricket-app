/**
 * DataGatheringModal - capture labelled radar data at the nets.
 *
 * Two tabs:
 *   Record      start/stop a capture and label each ball on the wagon wheel
 *   Recordings  the log of everything captured, and the wheel for each one
 *
 * Capture types:
 *   bowling: ball goes by, no batsman   (pure ball signature)
 *   batting: batsman, no ball           (bat/body signature)
 *   both:    ball bowled and hit        (real shot - the one that needs labels)
 *
 * A label is one tap on the wheel: WHERE the ball went (direction) and HOW
 * FAR (distance to the rope). An optional outcome (dot/1/2/3/4/6/W) can be
 * armed before the tap, so one tap is always enough to record a ball and two
 * give a fully labelled one. Nothing is ever lost by not choosing an outcome.
 *
 * Direction convention: 0deg = toward bowler, +90 = leg side, -90 = off side,
 * always in the RIGHT-HANDED frame so every session is comparable. For a
 * left-hander the display mirrors; the recorded value does not. NOTE the game
 * engine's simulate `angle` uses the OPPOSITE sign (+off/-leg) - flip the
 * sign when feeding this data to the engine.
 *
 * The recording lives on the server: closing this modal does NOT stop it, and
 * reopening re-attaches to the in-progress session via get_recording_status.
 */

import { useCallback, useEffect, useState } from 'react'
import './RecordingModal.css'
import { WagonWheel } from './WagonWheel'
import type { WheelMark } from '../wagonWheelGeometry'

type SessionType = 'bowling' | 'batting' | 'both'
type Tab = 'record' | 'log'

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

interface RecordingSummary {
  file: string
  session_type: string
  start_time: string | null
  duration_seconds: number
  frame_count: number
  annotation_count: number
  mock?: boolean
  // The radar dropped out PART-WAY through: mock is false (it was present at
  // the start) but this many frames are fabricated.
  mock_frame_count?: number
  partial_mock?: boolean
  incomplete?: boolean
}

interface RecordingDetail extends RecordingSummary {
  annotations: Array<Record<string, unknown>>
  annotations_truncated?: boolean
  size_bytes?: number
}

const DURATIONS = [2, 5, 10] // minutes
const OUTCOMES = ['dot', '1', '2', '3', '4', '6', 'W'] as const

const fmtClock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`

function fmtWhen(iso: string | null): string {
  if (!iso) return 'unknown time'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

function fmtSize(bytes?: number): string {
  if (!bytes) return ''
  if (bytes > 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(1)}GB`
  if (bytes > 1024 * 1024) return `${Math.round(bytes / 1024 / 1024)}MB`
  return `${Math.round(bytes / 1024)}KB`
}

/** Annotations from the file -> marks the wheel can draw. */
function toMarks(annotations: Array<Record<string, unknown>>): WheelMark[] {
  return annotations
    .filter((a) => typeof a.direction_deg === 'number')
    .map((a) => ({
      direction_deg: a.direction_deg as number,
      distance_norm: typeof a.distance_norm === 'number' ? a.distance_norm : undefined,
      outcome: typeof a.outcome === 'string' ? a.outcome : null,
    }))
}

export function DataGatheringModal({ isConnected, onClose, sendMessage }: DataGatheringModalProps) {
  const [tab, setTab] = useState<Tab>('record')

  // --- recording state
  const [sessionType, setSessionType] = useState<string>('both')
  const [maxSeconds, setMaxSeconds] = useState(5 * 60)
  const [isRecording, setIsRecording] = useState(false)
  const [isMock, setIsMock] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [frameCount, setFrameCount] = useState(0)
  const [markCount, setMarkCount] = useState(0)
  const [lastMark, setLastMark] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // --- labelling
  const [marks, setMarks] = useState<WheelMark[]>([])
  const [pending, setPending] = useState<WheelMark | null>(null)
  const [outcome, setOutcome] = useState<string | null>(null)
  const [leftHanded, setLeftHanded] = useState(false)

  // --- recordings log
  const [recordings, setRecordings] = useState<RecordingSummary[] | null>(null)
  const [detail, setDetail] = useState<RecordingDetail | null>(null)
  const [logBusy, setLogBusy] = useState(false)

  // Re-attach to an in-progress recording (the server keeps recording if the
  // modal was closed or the page reloaded mid-session).
  useEffect(() => {
    if (!isConnected) return
    let cancelled = false
    sendMessage('get_recording_status', {})
      .then((resp) => {
        if (cancelled) return
        const p = (resp as { payload?: StatusPayload })?.payload
        if (!p?.is_recording) {
          // The last recording stopped itself (card full) while this modal
          // was closed: say so on open, not only while polling.
          if (p?.last_error) {
            setError(`Last recording STOPPED - write failed: ${p.last_error}. The file is truncated; free space on the Pi.`)
          }
          return
        }
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

  const refreshLog = useCallback(() => {
    if (!isConnected) return
    setLogBusy(true)
    sendMessage('list_recordings', {})
      .then((resp) => {
        const r = resp as { type: string; payload?: { recordings?: RecordingSummary[]; message?: string } }
        if (r.type === 'recordings_list') setRecordings(r.payload?.recordings ?? [])
        else setError(r.payload?.message ?? 'Could not list recordings')
      })
      .catch((e) => setError(`Could not list recordings: ${e}`))
      .finally(() => setLogBusy(false))
  }, [isConnected, sendMessage])

  // Load the log when the tab is first opened, and after a recording stops
  useEffect(() => {
    if (tab === 'log' && recordings === null) refreshLog()
  }, [tab, recordings, refreshLog])

  const openRecording = (file: string) => {
    setLogBusy(true)
    setDetail(null)
    sendMessage('get_recording', { file })
      .then((resp) => {
        const r = resp as { type: string; payload?: RecordingDetail & { message?: string } }
        if (r.type === 'recording_detail' && r.payload) setDetail(r.payload)
        else setError(r.payload?.message ?? 'Could not open that recording')
      })
      .catch((e) => setError(`Could not open that recording: ${e}`))
      .finally(() => setLogBusy(false))
  }

  const handleStart = async () => {
    if (!isConnected) { setError('Not connected to Pi'); return }
    setError(null); setLastMark(null); setPending(null); setMarks([])
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
    setRecordings(null) // the log is stale now
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

  /** One tap on the wheel = one ball, recorded immediately. */
  const handleWheelTap = (m: { direction_deg: number; distance_norm: number }) => {
    if (!isRecording) return
    const full: WheelMark = { ...m, outcome }
    setPending(full)
    setMarks((prev) => [...prev, full])
    const rad = (m.direction_deg * Math.PI) / 180
    sendMark(
      {
        direction_deg: m.direction_deg,
        distance_norm: m.distance_norm,
        outcome,
        batting_hand: leftHanded ? 'left' : 'right',
        // x/y kept for the existing offline tools
        x: Math.round(Math.sin(rad) * m.distance_norm * 100) / 100,
        y: Math.round(Math.cos(rad) * m.distance_norm * 100) / 100,
      },
      `Ball -> ${m.direction_deg > 0 ? '+' : ''}${m.direction_deg}deg${outcome ? ` (${outcome})` : ''}`,
    )
  }

  const progressPct = Math.min(100, (elapsed / maxSeconds) * 100)
  const detailMarks = detail ? toMarks(detail.annotations) : []

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

        <div className="dg-tabs">
          <button className={`dg-tab ${tab === 'record' ? 'active' : ''}`} onClick={() => setTab('record')}>
            Record{isRecording ? ' ●' : ''}
          </button>
          <button className={`dg-tab ${tab === 'log' ? 'active' : ''}`} onClick={() => setTab('log')}>
            Recordings{recordings ? ` (${recordings.length})` : ''}
          </button>
        </div>

        <div className="recording-content">
          {!isConnected && <div className="recording-warning">Not connected to Pi server</div>}
          {isMock && isRecording && (
            <div className="recording-warning">
              ⚠️ RADAR NOT DETECTED — this session records fabricated mock data,
              not real radar. Check the radar's USB connection, then stop and
              start again.
            </div>
          )}
          {error && <div className="recording-error">{error}</div>}

          {/* ============================= RECORD ============================= */}
          {tab === 'record' && (
            <>
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

              <div className="recording-section">
                {isRecording ? (
                  <div className="recording-status recording">
                    <div className="recording-indicator"><span className="rec-dot" /> REC</div>
                    <div className="recording-time">{fmtClock(elapsed)} / {fmtClock(maxSeconds)}</div>
                    <div className="progress-bar"><div className="progress-fill" style={{ width: `${progressPct}%` }} /></div>
                    <div style={{ marginTop: 8, fontSize: 13 }}>
                      {frameCount} frames &middot; {markCount} balls marked
                    </div>
                  </div>
                ) : (
                  <div className="recording-status ready">Ready to record</div>
                )}
              </div>

              {/* Labelling */}
              {isRecording && sessionType === 'both' && (
                <div className="recording-section">
                  <div className="dg-label-row">
                    <label>Tap where the ball went</label>
                    <button className="dg-hand-btn" onClick={() => setLeftHanded((v) => !v)}>
                      {leftHanded ? 'Left-handed' : 'Right-handed'}
                    </button>
                  </div>

                  <div className="dg-outcome-row">
                    {OUTCOMES.map((o) => (
                      <button
                        key={o}
                        className={`dg-outcome ${outcome === o ? 'active' : ''}`}
                        onClick={() => setOutcome(outcome === o ? null : o)}
                      >
                        {o === 'dot' ? '•' : o}
                      </button>
                    ))}
                  </div>

                  <WagonWheel marks={marks} pending={pending} mirror={leftHanded} onTap={handleWheelTap} />

                  <p className="note" style={{ textAlign: 'center', marginTop: 6 }}>
                    One tap = one ball. Tap near the rope for a boundary, near the
                    middle for a short one. Pick an outcome first to label it too.
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

              {lastMark && <div className="recording-success">{lastMark}</div>}

              <div className="recording-section instructions">
                <p><strong>Ball only:</strong> bowl past, no batsman — pure ball signature.</p>
                <p><strong>Batsman only:</strong> shadow shots, no ball — bat/body signature.</p>
                <p><strong>Ball + hit:</strong> real shots — tap the wheel for each ball.</p>
                <p className="note">Saved crash-safe to the Pi as you go (one file per session).</p>
              </div>
            </>
          )}

          {/* =========================== RECORDINGS =========================== */}
          {tab === 'log' && !detail && (
            <>
              <div className="recording-section">
                <div className="dg-label-row">
                  <label>Saved recordings</label>
                  <button className="dg-hand-btn" onClick={refreshLog} disabled={!isConnected || logBusy}>
                    {logBusy ? 'Loading…' : 'Refresh'}
                  </button>
                </div>
              </div>

              {recordings === null && <div className="recording-status ready">{logBusy ? 'Loading…' : 'Not loaded'}</div>}
              {recordings?.length === 0 && (
                <div className="recording-status ready">No recordings on the device yet.</div>
              )}

              <div className="dg-list">
                {recordings?.map((r) => (
                  <button key={r.file} className="dg-list-item" onClick={() => openRecording(r.file)}>
                    <div className="dg-list-top">
                      <span className="dg-type">{r.session_type}</span>
                      <span className="dg-when">{fmtWhen(r.start_time)}</span>
                    </div>
                    <div className="dg-list-stats">
                      <span>{Math.round(r.duration_seconds)}s</span>
                      <span>{r.frame_count} frames</span>
                      <span>{r.annotation_count} marks</span>
                      {r.mock && <span className="dg-badge mock">MOCK</span>}
                      {!r.mock && r.partial_mock && (
                        <span className="dg-badge mock">PART MOCK</span>
                      )}
                      {r.incomplete && <span className="dg-badge warn">INCOMPLETE</span>}
                    </div>
                  </button>
                ))}
              </div>
            </>
          )}

          {tab === 'log' && detail && (
            <>
              <div className="recording-section">
                <div className="dg-label-row">
                  <button className="dg-hand-btn" onClick={() => setDetail(null)}>← Back</button>
                  <span className="dg-when">{fmtWhen(detail.start_time)}</span>
                </div>
              </div>

              {detail.mock && (
                <div className="recording-warning">
                  ⚠️ MOCK DATA — the radar was not detected for this recording.
                  The frames are fabricated: do not tune anything against it.
                </div>
              )}
              {!detail.mock && detail.partial_mock && (
                <div className="recording-warning">
                  ⚠️ RADAR DROPPED OUT — {detail.mock_frame_count} of {detail.frame_count} frames
                  are fabricated. The radar was present when this started and disappeared
                  part-way (check the cable and the power supply). The real frames are still
                  usable; the fabricated ones are not.
                </div>
              )}
              {detail.incomplete && (
                <div className="recording-warning">
                  This session has no end marker — it crashed or was interrupted.
                  The counts below are recovered by scanning the file.
                </div>
              )}

              <div className="recording-section">
                <div className="dg-detail-stats">
                  <div><b>{detail.session_type}</b></div>
                  <div>{Math.round(detail.duration_seconds)}s &middot; {detail.frame_count} frames &middot; {detail.annotation_count} marks</div>
                  <div className="note">{detail.file.split('/').slice(-2).join('/')} {fmtSize(detail.size_bytes)}</div>
                </div>
              </div>

              {detailMarks.length > 0 ? (
                <div className="recording-section">
                  <label>Where the balls went ({detailMarks.length})</label>
                  <WagonWheel marks={detailMarks} />
                </div>
              ) : (
                <div className="recording-section">
                  <div className="recording-status ready">
                    No directional marks in this recording
                    {detail.annotation_count > 0 ? ' (marks were timing-only)' : ''}.
                  </div>
                </div>
              )}

              {detail.annotations.length > 0 && (
                <div className="recording-section">
                  <label>Marks</label>
                  <div className="dg-marks">
                    {detail.annotations.map((a, i) => (
                      <div key={i} className="dg-mark-row">
                        <span className="dg-mark-t">{fmtClock(Math.round(Number(a.t_ms ?? 0) / 1000))}</span>
                        <span>
                          {typeof a.direction_deg === 'number'
                            ? `${(a.direction_deg as number) > 0 ? '+' : ''}${a.direction_deg}°`
                            : String(a.label ?? '—')}
                        </span>
                        <span className="dg-mark-out">{typeof a.outcome === 'string' ? a.outcome : ''}</span>
                      </div>
                    ))}
                  </div>
                  {detail.annotations_truncated && (
                    <p className="note">Only the first {detail.annotations.length} marks are shown.</p>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </>
  )
}
