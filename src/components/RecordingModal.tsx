/**
 * RecordingModal - UI for radar data recording
 *
 * Allows users to start/stop recording radar data for analysis,
 * categorized by session type (bowling, batting, both).
 */

import { useState, useEffect, useRef } from 'react'
import './RecordingModal.css'

interface RecordingCounts {
  bowling: number
  batting: number
  both: number
}

interface RecordingModalProps {
  isConnected: boolean
  onClose: () => void
  sendMessage: (type: string, payload: Record<string, unknown>) => Promise<unknown>
}

type SessionType = 'bowling' | 'batting' | 'both'

export function RecordingModal({ isConnected, onClose, sendMessage }: RecordingModalProps) {
  const [sessionType, setSessionType] = useState<SessionType>('both')
  const [isRecording, setIsRecording] = useState(false)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [counts, setCounts] = useState<RecordingCounts>({ bowling: 0, batting: 0, both: 0 })
  const [error, setError] = useState<string | null>(null)
  const [lastResult, setLastResult] = useState<string | null>(null)

  const timerRef = useRef<number | null>(null)
  const maxDuration = 15

  // Fetch initial recording status
  useEffect(() => {
    if (isConnected) {
      sendMessage('get_recording_status', {})
        .then((response: unknown) => {
          const resp = response as { payload?: { is_recording?: boolean; counts?: RecordingCounts } }
          if (resp?.payload) {
            setIsRecording(resp.payload.is_recording || false)
            if (resp.payload.counts) {
              setCounts(resp.payload.counts)
            }
          }
        })
        .catch((e) => console.error('Failed to get recording status:', e))
    }
  }, [isConnected, sendMessage])

  // Timer for recording progress (pure tick - state updaters must not have
  // side effects; StrictMode invokes them twice in dev)
  useEffect(() => {
    if (!isRecording) return
    timerRef.current = window.setInterval(() => {
      setElapsedSeconds((prev) => prev + 1)
    }, 1000)
    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
  }, [isRecording])

  // Auto-stop mirror: the server enforces max duration; when the local clock
  // reaches it, reflect that in the UI and refresh counts.
  useEffect(() => {
    if (!isRecording || elapsedSeconds < maxDuration) return
    setIsRecording(false)
    setLastResult(`Recording complete (${maxDuration}s)`)
    sendMessage('get_recording_status', {})
      .then((response: unknown) => {
        const resp = response as { payload?: { counts?: RecordingCounts } }
        if (resp?.payload?.counts) {
          setCounts(resp.payload.counts)
        }
      })
      .catch(() => {})
  }, [isRecording, elapsedSeconds, maxDuration, sendMessage])

  const handleStartRecording = async () => {
    if (!isConnected) {
      setError('Not connected to server')
      return
    }

    setError(null)
    setLastResult(null)

    try {
      const response = await sendMessage('start_recording', { session_type: sessionType }) as {
        type: string
        payload?: { counts?: RecordingCounts }
      }

      if (response.type === 'recording_started') {
        setIsRecording(true)
        setElapsedSeconds(0)
        if (response.payload?.counts) {
          setCounts(response.payload.counts)
        }
      } else if (response.type === 'error') {
        setError((response as { payload?: { message?: string } }).payload?.message || 'Failed to start recording')
      }
    } catch (e) {
      setError(`Failed to start: ${e}`)
    }
  }

  const handleStopRecording = async () => {
    if (!isConnected) {
      setError('Not connected to server')
      return
    }

    setError(null)

    try {
      const response = await sendMessage('stop_recording', {}) as {
        type: string
        payload?: {
          duration_seconds?: number
          frame_count?: number
          counts?: RecordingCounts
        }
      }

      if (response.type === 'recording_stopped') {
        setIsRecording(false)
        setElapsedSeconds(0)
        if (response.payload) {
          setLastResult(
            `Saved: ${response.payload.duration_seconds?.toFixed(1)}s, ` +
            `${response.payload.frame_count} frames`
          )
          if (response.payload.counts) {
            setCounts(response.payload.counts)
          }
        }
      } else if (response.type === 'error') {
        setError((response as { payload?: { message?: string } }).payload?.message || 'Failed to stop recording')
      }
    } catch (e) {
      setError(`Failed to stop: ${e}`)
    }
  }

  const progressPercent = (elapsedSeconds / maxDuration) * 100

  return (
    <>
      <div className="recording-overlay" onClick={onClose} />
      <div className="recording-modal">
        <div className="recording-header">
          <h2>Radar Recording</h2>
          <button className="close-btn" onClick={onClose}>×</button>
        </div>

        <div className="recording-content">
          {!isConnected && (
            <div className="recording-warning">
              Not connected to Pi server
            </div>
          )}

          {/* Session Type Selector */}
          <div className="recording-section">
            <label>Recording Type</label>
            <div className="session-type-buttons">
              {(['bowling', 'batting', 'both'] as SessionType[]).map((type) => (
                <button
                  key={type}
                  className={`type-btn ${sessionType === type ? 'active' : ''}`}
                  onClick={() => setSessionType(type)}
                  disabled={isRecording}
                >
                  {type.charAt(0).toUpperCase() + type.slice(1)}
                </button>
              ))}
            </div>
          </div>

          {/* Recording Status */}
          <div className="recording-section">
            {isRecording ? (
              <div className="recording-status recording">
                <div className="recording-indicator">
                  <span className="rec-dot"></span>
                  REC
                </div>
                <div className="recording-time">
                  {elapsedSeconds}s / {maxDuration}s
                </div>
                <div className="progress-bar">
                  <div
                    className="progress-fill"
                    style={{ width: `${progressPercent}%` }}
                  />
                </div>
              </div>
            ) : (
              <div className="recording-status ready">
                Ready to record
              </div>
            )}
          </div>

          {/* Start/Stop Button */}
          <div className="recording-section">
            {isRecording ? (
              <button
                className="record-btn stop"
                onClick={handleStopRecording}
              >
                Stop Recording
              </button>
            ) : (
              <button
                className="record-btn start"
                onClick={handleStartRecording}
                disabled={!isConnected}
              >
                Start Recording
              </button>
            )}
          </div>

          {/* Error/Result Messages */}
          {error && (
            <div className="recording-error">{error}</div>
          )}
          {lastResult && (
            <div className="recording-success">{lastResult}</div>
          )}

          {/* Recording Counts */}
          <div className="recording-section counts">
            <label>Saved Recordings</label>
            <div className="counts-grid">
              <div className="count-item">
                <span className="count-value">{counts.bowling}</span>
                <span className="count-label">Bowling</span>
              </div>
              <div className="count-item">
                <span className="count-value">{counts.batting}</span>
                <span className="count-label">Batting</span>
              </div>
              <div className="count-item">
                <span className="count-value">{counts.both}</span>
                <span className="count-label">Both</span>
              </div>
            </div>
          </div>

          {/* Instructions */}
          <div className="recording-section instructions">
            <p>
              <strong>Bowling:</strong> Record radar data while bowling only (no batter)
            </p>
            <p>
              <strong>Batting:</strong> Record shadow batting (no ball)
            </p>
            <p>
              <strong>Both:</strong> Record actual batting with ball
            </p>
            <p className="note">
              Max recording duration: {maxDuration} seconds
            </p>
          </div>
        </div>
      </div>
    </>
  )
}
