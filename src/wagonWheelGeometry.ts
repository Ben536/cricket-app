/**
 * Wagon-wheel geometry and conventions - pure, no React.
 *
 * These values become GROUND TRUTH labels in the recordings, so the
 * conventions are law:
 *
 *   direction_deg  0 = straight to the bowler, +90 = LEG, -90 = OFF,
 *                  +/-180 = behind the keeper. Always in the RIGHT-HANDED
 *                  frame, so every session is directly comparable.
 *                  NOTE the game engine's `horizontal_angle` uses the
 *                  OPPOSITE sign (+off) - flip when feeding the engine.
 *   distance_norm  0 at the batter, 1.0 at the boundary rope.
 *
 * For a left-handed batter the DISPLAY mirrors (so "Off" stays on the
 * batter's off side) but the recorded value does not.
 */

export const WHEEL_SIZE = 300
export const WHEEL_CX = WHEEL_SIZE / 2
export const WHEEL_CY = WHEEL_SIZE / 2
export const WHEEL_R = 118

export interface WheelMark {
  direction_deg: number
  distance_norm?: number
  outcome?: string | null
}

/** Screen point for a mark. `mirror` flips the display for a left-hander. */
export function markToPoint(mark: WheelMark, mirror = false): { x: number; y: number } {
  const dir = mirror ? -mark.direction_deg : mark.direction_deg
  const rad = (dir * Math.PI) / 180
  const r = Math.max(0, Math.min(1, mark.distance_norm ?? 1)) * WHEEL_R
  return { x: WHEEL_CX + Math.sin(rad) * r, y: WHEEL_CY - Math.cos(rad) * r }
}

/** Direction (+leg) and normalised distance from a point in the SVG. */
export function pointToMark(px: number, py: number, mirror = false): { direction_deg: number; distance_norm: number } {
  const dx = px - WHEEL_CX
  const dy = WHEEL_CY - py
  const raw = Math.atan2(dx, dy) * (180 / Math.PI)
  const dir = mirror ? -raw : raw
  return {
    direction_deg: Math.round(dir * 10) / 10,
    distance_norm: Math.round(Math.min(1, Math.hypot(dx, dy) / WHEEL_R) * 100) / 100,
  }
}

const OUTCOME_COLOUR: Record<string, string> = {
  '6': '#f5a623',
  '4': '#2ecc71',
  W: '#e63946',
  dot: '#9fb3c8',
}

export function outcomeColour(outcome?: string | null): string {
  if (!outcome) return '#ffd700'
  return OUTCOME_COLOUR[outcome] ?? '#57a0ff'
}
