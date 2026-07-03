/**
 * RadarVisualizer - Real-time radar point cloud display
 *
 * Shows radar data streaming from the Pi in a top-down view.
 * Points are colored by velocity (doppler).
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import './RadarVisualizer.css'

interface RadarPoint {
  x: number
  y: number
  z: number
  v: number  // velocity/doppler
}

interface RadarFrame {
  frame_number: number
  timestamp_ms: number
  point_count: number
  points: RadarPoint[]
}

interface RadarVisualizerProps {
  isConnected: boolean
  onClose: () => void
  sendMessage: (type: string, payload: Record<string, unknown>) => Promise<unknown>
}

export function RadarVisualizer({ isConnected, onClose, sendMessage }: RadarVisualizerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const [frameCount, setFrameCount] = useState(0)
  const [pointCount, setPointCount] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const frameBufferRef = useRef<RadarFrame[]>([])
  const animationRef = useRef<number | null>(null)

  // Radar view settings (metres)
  const VIEW_RANGE_X = 6  // -3 to +3 metres
  const VIEW_RANGE_Y = 9  // 0 to 9 metres (forward)

  // Draw the radar view
  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const width = canvas.width
    const height = canvas.height

    // Reset all context state
    ctx.globalAlpha = 1
    ctx.shadowBlur = 0
    ctx.shadowColor = 'transparent'

    // Clear
    ctx.fillStyle = '#1a1a2e'
    ctx.fillRect(0, 0, width, height)

    // Draw grid
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.1)'
    ctx.lineWidth = 1

    // Vertical lines (every metre)
    for (let x = -3; x <= 3; x++) {
      const screenX = ((x + VIEW_RANGE_X / 2) / VIEW_RANGE_X) * width
      ctx.beginPath()
      ctx.moveTo(screenX, 0)
      ctx.lineTo(screenX, height)
      ctx.stroke()
    }

    // Horizontal lines (every metre)
    for (let y = 0; y <= VIEW_RANGE_Y; y++) {
      const screenY = height - (y / VIEW_RANGE_Y) * height
      ctx.beginPath()
      ctx.moveTo(0, screenY)
      ctx.lineTo(width, screenY)
      ctx.stroke()
    }

    // Draw centre line
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)'
    ctx.beginPath()
    ctx.moveTo(width / 2, 0)
    ctx.lineTo(width / 2, height)
    ctx.stroke()

    // Draw radar position indicator (at bottom centre)
    ctx.fillStyle = '#f4d03f'
    ctx.beginPath()
    ctx.arc(width / 2, height - 10, 8, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillStyle = '#1a1a2e'
    ctx.font = '10px sans-serif'
    ctx.textAlign = 'center'
    ctx.fillText('R', width / 2, height - 6)

    // Draw points from recent frames (reduced from 5 to 2 for cleaner display)
    const recentFrames = frameBufferRef.current.slice(-2)

    recentFrames.forEach((frame, frameIdx) => {
      const age = recentFrames.length - frameIdx - 1
      const alpha = 1 - (age * 0.4)  // Faster fade for cleaner look

      frame.points.forEach(point => {
        // Convert radar coords to screen coords
        // Radar: x is left/right, y is forward distance
        const screenX = ((point.x + VIEW_RANGE_X / 2) / VIEW_RANGE_X) * width
        const screenY = height - (point.y / VIEW_RANGE_Y) * height

        // Skip points outside view
        if (screenX < 0 || screenX > width || screenY < 0 || screenY > height) return

        // Clean, uniform appearance - theme green with subtle glow
        const size = 5

        ctx.save()
        ctx.globalAlpha = alpha
        ctx.fillStyle = '#4ade80'  // Soft green matching theme
        ctx.beginPath()
        ctx.arc(screenX, screenY, size, 0, Math.PI * 2)
        ctx.fill()
        ctx.restore()
      })
    })

    // Draw legend - simplified single color
    ctx.fillStyle = 'rgba(255, 255, 255, 0.6)'
    ctx.font = '11px sans-serif'
    ctx.textAlign = 'left'
    ctx.fillText('Detected', 10, 20)
    ctx.fillStyle = '#4ade80'
    ctx.beginPath()
    ctx.arc(70, 16, 5, 0, Math.PI * 2)
    ctx.fill()

    // Distance markers
    ctx.fillStyle = 'rgba(255, 255, 255, 0.5)'
    ctx.textAlign = 'right'
    ctx.fillText('3m', width - 5, height - (3 / VIEW_RANGE_Y) * height + 4)
    ctx.fillText('6m', width - 5, height - (6 / VIEW_RANGE_Y) * height + 4)
    ctx.fillText('9m', width - 5, height - (9 / VIEW_RANGE_Y) * height + 4)

    // Schedule next frame
    animationRef.current = requestAnimationFrame(draw)
  }, [])

  // Handle incoming radar frames via custom event. The canvas reads from the
  // ref buffer at its own pace; the React stat counters update at most once
  // per second - setting state per frame re-rendered the whole modal at
  // radar frame rate (10-20Hz) for two numbers.
  useEffect(() => {
    if (!isStreaming) return

    const latest = { frame: 0, points: 0 }
    const handleRadarFrame = (event: CustomEvent<RadarFrame>) => {
      const frame = event.detail
      if (frame) {
        frameBufferRef.current.push(frame)
        // Keep last 30 frames
        if (frameBufferRef.current.length > 30) {
          frameBufferRef.current.shift()
        }
        latest.frame = frame.frame_number
        latest.points = frame.point_count
      }
    }

    const statTimer = window.setInterval(() => {
      setFrameCount(latest.frame)
      setPointCount(latest.points)
    }, 1000)

    window.addEventListener('radar-frame', handleRadarFrame as EventListener)

    return () => {
      window.removeEventListener('radar-frame', handleRadarFrame as EventListener)
      window.clearInterval(statTimer)
    }
  }, [isStreaming])

  // Start animation loop
  useEffect(() => {
    if (isStreaming) {
      animationRef.current = requestAnimationFrame(draw)
    }
    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current)
      }
    }
  }, [isStreaming, draw])

  // Resize canvas
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const resize = () => {
      const container = canvas.parentElement
      if (container) {
        canvas.width = container.clientWidth
        canvas.height = container.clientHeight
      }
    }

    resize()
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [])

  const handleStart = async () => {
    if (!isConnected) {
      setError('Not connected to Pi')
      return
    }

    setError(null)
    frameBufferRef.current = []

    try {
      const response = await sendMessage('start_radar_stream', {}) as { type: string }
      if (response.type === 'radar_stream_started') {
        setIsStreaming(true)
      } else {
        setError('Failed to start stream')
      }
    } catch (e) {
      setError(`Error: ${e}`)
    }
  }

  const handleStop = async () => {
    try {
      await sendMessage('stop_radar_stream', {})
    } catch {
      // Ignore
    }
    setIsStreaming(false)
    setFrameCount(0)
    setPointCount(0)
  }

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (isStreaming) {
        sendMessage('stop_radar_stream', {}).catch(() => {})
      }
    }
  }, [isStreaming, sendMessage])

  // If the server connection drops and comes back while we think we're
  // streaming, the server has forgotten our subscription - re-establish it.
  useEffect(() => {
    if (!isStreaming) return
    const resubscribe = () => {
      sendMessage('start_radar_stream', {}).catch(() => {
        setIsStreaming(false)
        setError('Stream lost - press Start to resume')
      })
    }
    window.addEventListener('server-reconnected', resubscribe)
    return () => window.removeEventListener('server-reconnected', resubscribe)
  }, [isStreaming, sendMessage])

  return (
    <>
      <div className="radar-viz-overlay" onClick={onClose} />
      <div className="radar-viz-modal">
        <div className="radar-viz-header">
          <h2>Radar View</h2>
          <div className="radar-viz-stats">
            {isStreaming && (
              <>
                <span className="stat">Frame: {frameCount}</span>
                <span className="stat">Points: {pointCount}</span>
              </>
            )}
          </div>
          <button className="close-btn" onClick={onClose}>×</button>
        </div>

        <div className="radar-viz-canvas-container">
          <canvas ref={canvasRef} className="radar-viz-canvas" />

          {!isStreaming && (
            <div className="radar-viz-placeholder">
              <p>Top-down radar view</p>
              <p className="sub">Points colored by velocity</p>
            </div>
          )}
        </div>

        <div className="radar-viz-controls">
          {!isConnected && (
            <div className="radar-viz-warning">Not connected to Pi</div>
          )}

          {error && <div className="radar-viz-error">{error}</div>}

          {isStreaming ? (
            <button className="radar-btn stop" onClick={handleStop}>
              Stop Stream
            </button>
          ) : (
            <button
              className="radar-btn start"
              onClick={handleStart}
              disabled={!isConnected}
            >
              Start Stream
            </button>
          )}
        </div>
      </div>
    </>
  )
}
