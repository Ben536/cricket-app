/**
 * The wagon wheel, used both to LABEL a ball during a capture and to REVIEW
 * a whole session afterwards. Read-only when no `onTap` is given.
 *
 * The geometry and the sign conventions live in src/wagonWheelGeometry.ts.
 */

import {
  markToPoint,
  outcomeColour,
  pointToMark,
  WHEEL_CX,
  WHEEL_CY,
  WHEEL_R,
  WHEEL_SIZE,
  type WheelMark,
} from '../wagonWheelGeometry'

interface WagonWheelProps {
  marks?: WheelMark[]
  /** The mark being placed right now, drawn brighter. */
  pending?: WheelMark | null
  mirror?: boolean
  onTap?: (mark: { direction_deg: number; distance_norm: number }) => void
  size?: number
}

export function WagonWheel({ marks = [], pending = null, mirror = false, onTap, size = 320 }: WagonWheelProps) {
  const interactive = Boolean(onTap)

  const handle = (clientX: number, clientY: number, target: SVGSVGElement) => {
    if (!onTap) return
    const rect = target.getBoundingClientRect()
    const px = ((clientX - rect.left) / rect.width) * WHEEL_SIZE
    const py = ((clientY - rect.top) / rect.height) * WHEEL_SIZE
    onTap(pointToMark(px, py, mirror))
  }

  return (
    <svg
      viewBox={`0 0 ${WHEEL_SIZE} ${WHEEL_SIZE}`}
      className="wagon-wheel-svg"
      role={interactive ? 'button' : 'img'}
      aria-label={interactive ? 'Tap where the ball went' : 'Wagon wheel of this session'}
      onClick={(e) => handle(e.clientX, e.clientY, e.currentTarget)}
      style={{
        width: '100%', maxWidth: size, display: 'block', margin: '0 auto',
        background: '#0b3d2e', borderRadius: 8,
        touchAction: 'manipulation', cursor: interactive ? 'crosshair' : 'default',
      }}
    >
      {/* Field, inner ring, and the pitch */}
      <circle cx={WHEEL_CX} cy={WHEEL_CY} r={WHEEL_R} fill="#0f5132" stroke="#2e8b57" strokeWidth={2} />
      <circle cx={WHEEL_CX} cy={WHEEL_CY} r={WHEEL_R * 0.5} fill="none" stroke="#2e8b57" strokeWidth={1} strokeDasharray="4 4" />
      <line x1={WHEEL_CX} y1={WHEEL_CY - WHEEL_R} x2={WHEEL_CX} y2={WHEEL_CY + WHEEL_R} stroke="#2e8b57" strokeWidth={1} />
      <line x1={WHEEL_CX - WHEEL_R} y1={WHEEL_CY} x2={WHEEL_CX + WHEEL_R} y2={WHEEL_CY} stroke="#2e8b57" strokeWidth={1} />
      <rect x={WHEEL_CX - 5} y={WHEEL_CY - 26} width={10} height={52} fill="#c4a574" opacity={0.75} rx={2} />

      {/* Every mark in the session */}
      {marks.map((m, i) => {
        const p = markToPoint(m, mirror)
        const colour = outcomeColour(m.outcome)
        return (
          <g key={i} opacity={0.85}>
            <line x1={WHEEL_CX} y1={WHEEL_CY} x2={p.x} y2={p.y} stroke={colour} strokeWidth={1.5} />
            <circle cx={p.x} cy={p.y} r={3.5} fill={colour} />
          </g>
        )
      })}

      {/* The ball being labelled now */}
      {pending && (() => {
        const p = markToPoint(pending, mirror)
        const colour = outcomeColour(pending.outcome)
        return (
          <g>
            <line x1={WHEEL_CX} y1={WHEEL_CY} x2={p.x} y2={p.y} stroke={colour} strokeWidth={3} />
            <circle cx={p.x} cy={p.y} r={7} fill={colour} stroke="#fff" strokeWidth={1.5} />
          </g>
        )
      })()}

      <circle cx={WHEEL_CX} cy={WHEEL_CY} r={4} fill="#ffd700" />

      <text x={WHEEL_CX} y={14} fill="#cde" fontSize={12} textAnchor="middle">Bowler</text>
      <text x={WHEEL_CX} y={WHEEL_SIZE - 5} fill="#cde" fontSize={12} textAnchor="middle">Keeper</text>
      <text x={10} y={WHEEL_CY + 4} fill="#cde" fontSize={12}>{mirror ? 'Leg' : 'Off'}</text>
      <text x={WHEEL_SIZE - 10} y={WHEEL_CY + 4} fill="#cde" fontSize={12} textAnchor="end">{mirror ? 'Off' : 'Leg'}</text>
    </svg>
  )
}
