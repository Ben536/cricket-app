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

    // Draw points from recent frames
    const recentFrames = frameBufferRef.current.slice(-5)  // Last 5 frames

    recentFrames.forEach((frame, frameIdx) => {
      const age = recentFrames.length - frameIdx - 1
      const alpha = 1 - (age * 0.15)  // Older frames fade

      frame.points.forEach(point => {
        // Convert radar coords to screen coords
        // Radar: x is left/right, y is forward distance
        const screenX = ((point.x + VIEW_RANGE_X / 2) / VIEW_RANGE_X) * width
        const screenY = height - (point.y / VIEW_RANGE_Y) * height

        // Skip points outside view
        if (screenX < 0 || screenX > width || screenY < 0 || screenY > height) return

        // Color by velocity (doppler)
        // Positive = moving away (red), Negative = moving toward (blue)
        // High velocity = brighter
        const absV = Math.abs(point.v)
        const intensity = Math.min(1, absV / 20)  // Max at 20 m/s

        let r, g, b
        if (point.v > 2) {
          // Moving away - red/orange
          r = 255
          g = Math.floor(100 * (1 - intensity))
          b = 50
        } else if (point.v < -2) {
          // Moving toward - blue/cyan
          r = 50
          g = Math.floor(200 * intensity)
          b = 255
        } else {
          // Slow/stationary - white/grey
          r = 200
          g = 200
          b = 200
        }

        // Size based on velocity
        const size = 4 + absV * 0.3

        ctx.globalAlpha = alpha
        ctx.fillStyle = `rgb(${r}, ${g}, ${b})`
        ctx.beginPath()
        ctx.arc(screenX, screenY, size, 0, Math.PI * 2)
        ctx.fill()

        // Add glow for fast-moving points
        if (absV > 10) {
          ctx.shadowColor = `rgb(${r}, ${g}, ${b})`
          ctx.shadowBlur = 10
          ctx.beginPath()
          ctx.arc(screenX, screenY, size, 0, Math.PI * 2)
          ctx.fill()
          ctx.shadowBlur = 0
        }
      })
    })

    ctx.globalAlpha = 1

    // Draw legend
    ctx.fillStyle = 'rgba(255, 255, 255, 0.7)'
    ctx.font = '11px sans-serif'
    ctx.textAlign = 'left'
    ctx.fillText('Away →', 10, 20)
    ctx.fillStyle = '#ff6432'
    ctx.fillRect(60, 12, 12, 12)

    ctx.fillStyle = 'rgba(255, 255, 255, 0.7)'
    ctx.fillText('← Toward', 10, 38)
    ctx.fillStyle = '#32c8ff'
    ctx.fillRect(70, 30, 12, 12)

    // Distance markers
    ctx.fillStyle = 'rgba(255, 255, 255, 0.5)'
    ctx.textAlign = 'right'
    ctx.fillText('3m', width - 5, height - (3 / VIEW_RANGE_Y) * height + 4)
    ctx.fillText('6m', width - 5, height - (6 / VIEW_RANGE_Y) * height + 4)
    ctx.fillText('9m', width - 5, height - (9 / VIEW_RANGE_Y) * height + 4)

    // Schedule next frame
    animationRef.current = requestAnimationFrame(draw)
  }, [])

  // Handle incoming radar frames via custom event
  useEffect(() => {
    if (!isStreaming) return

    const handleRadarFrame = (event: CustomEvent<RadarFrame>) => {
      const frame = event.detail
      if (frame) {
        frameBufferRef.current.push(frame)
        // Keep last 30 frames
        if (frameBufferRef.current.length > 30) {
          frameBufferRef.current.shift()
        }
        setFrameCount(frame.frame_number)
        setPointCount(frame.point_count)
      }
    }

    window.addEventListener('radar-frame', handleRadarFrame as EventListener)

    return () => {
      window.removeEventListener('radar-frame', handleRadarFrame as EventListener)
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
