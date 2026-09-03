/**
 * TypeScript engine invariants.
 *
 * Cross-engine agreement is proven by tools/parity/ (thousands of shots,
 * both engines). These tests pin the things parity cannot see: behaviour on
 * inputs that are not valid JSON (NaN/Infinity), the PRNG's exact output,
 * and the runtime guards that protect the browser's offline fallback.
 */

import { describe, it, expect } from 'vitest'

import {
  calculateTrajectory,
  getBoundaryDistanceAtAngle,
  mulberry32,
  normalizeAngle,
  simulateDelivery,
  ENGINE_LIMITS,
  type FielderConfig,
} from '../gameEngine'

const FIELD: FielderConfig[] = [
  { x: 20, y: 30, name: 'mid-off' },
  { x: -25, y: 20, name: 'midwicket' },
  { x: 0, y: -3, name: 'keeper' },
]

function simulate(speed: number, angle: number, elevation: number, opts: {
  field?: FielderConfig[]
  boundary?: number
  difficulty?: 'easy' | 'medium' | 'hard'
  seed?: number
} = {}) {
  const t = calculateTrajectory(speed, angle, elevation)
  return simulateDelivery(
    speed, angle, elevation, t.landing_x, t.landing_y, t.projected_distance, t.max_height,
    opts.field ?? FIELD, opts.boundary ?? 70, opts.difficulty ?? 'medium', opts.seed ?? 42,
  )
}

describe('mulberry32', () => {
  // These values are the ground truth for BOTH engines: engine/prng.py is
  // pinned to the same vectors in tests/test_engine.py. Change one and you
  // have forked the engines. The previous test only asserted 0 <= v < 1 and
  // that two identical seeds agree - which random.Random would also pass.
  it('matches the shared golden vectors', () => {
    const golden: Record<number, number[]> = {
      42: [0.6011037519201636, 0.44829055899754167, 0.8524657934904099, 0.6697340414393693, 0.17481389874592423],
      0: [0.26642920868471265, 0.0003297457005828619, 0.2232720274478197, 0.1462021479383111, 0.46732782293111086],
      4294967295: [0.8964226141106337, 0.189478256739676, 0.7156526781618595, 0.9440599093213677, 0.8452364315744489],
      2342376404: [0.6776549476198852, 0.0221342071890831, 0.9222554524894804, 0.3933766789268702, 0.21716754604130983],
    }
    for (const [seed, expected] of Object.entries(golden)) {
      const r = mulberry32(Number(seed))
      expect(Array.from({ length: 5 }, () => r())).toEqual(expected)
    }
  })
})

describe('normalizeAngle', () => {
  it('maps any angle into -180..180 with the same convention as Python', () => {
    expect(normalizeAngle(0)).toBe(0)
    expect(normalizeAngle(190)).toBe(-170)
    expect(normalizeAngle(-190)).toBe(170)
    expect(normalizeAngle(720)).toBe(0)
    expect(normalizeAngle(-540)).toBe(-180)
    expect(normalizeAngle(180)).toBe(-180) // Python: ((180+180) % 360) - 180 = -180
  })
})

describe('getBoundaryDistanceAtAngle', () => {
  it('is further straight, nearer behind', () => {
    const straight = getBoundaryDistanceAtAngle(0, 70)
    const square = getBoundaryDistanceAtAngle(90, 70)
    const behind = getBoundaryDistanceAtAngle(180, 70)
    expect(straight).toBeCloseTo(78.84, 2)
    expect(square).toBeCloseTo(69.44, 2)
    expect(behind).toBeCloseTo(61.16, 2)
    expect(straight).toBeGreaterThan(square)
    expect(square).toBeGreaterThan(behind)
  })

  it('never returns NaN when the radius is smaller than the batter offset', () => {
    // 5m radius, 90 degrees: radicand = 25 - 8.84^2 < 0. Used to be NaN.
    const d = getBoundaryDistanceAtAngle(90, 5)
    expect(Number.isFinite(d)).toBe(true)
    expect(d).toBeCloseTo(8.84 * Math.cos(Math.PI / 2), 6)
  })
})

describe('calculateTrajectory sanitisation', () => {
  it('treats NaN/Infinity as zero instead of propagating them', () => {
    for (const bad of [NaN, Infinity, -Infinity]) {
      const t = calculateTrajectory(bad, 30, 10)
      expect(t.projected_distance).toBe(0)
      expect(Number.isFinite(t.landing_x)).toBe(true)
    }
    const t = calculateTrajectory(100, NaN, Infinity)
    expect(Number.isFinite(t.landing_x)).toBe(true)
    expect(Number.isFinite(t.max_height)).toBe(true)
  })

  it('clamps speed to the engine limit so 300 km/h is 200 km/h', () => {
    const capped = calculateTrajectory(ENGINE_LIMITS.speedKmh.max, 20, 15)
    const over = calculateTrajectory(300, 20, 15)
    expect(over).toEqual(capped)
  })
})

describe('simulateDelivery guards', () => {
  it('is deterministic for a seed and echoes it', () => {
    const a = simulate(90, 20, 15, { seed: 7 })
    const b = simulate(90, 20, 15, { seed: 7 })
    expect(a).toEqual(b)
    expect(a.seed).toBe(7)
    expect(a.boundary_distance).toBeGreaterThan(70) // straight-ish: further than nominal
  })

  it('degrades an unknown difficulty to medium instead of throwing', () => {
    const medium = simulate(90, 20, 15, { difficulty: 'medium', seed: 11 })
    const bogus = simulate(90, 20, 15, { difficulty: 'god' as unknown as 'medium', seed: 11 })
    expect(bogus).toEqual(medium)
  })

  it('survives NaN everywhere and still produces an outcome', () => {
    const r = simulateDelivery(
      NaN, Infinity, -5, NaN, 0, 1e9, -2, FIELD, NaN, 'medium', 1,
    )
    expect(typeof r.outcome).toBe('string')
    expect(Number.isFinite(r.end_position.x)).toBe(true)
    expect(Number.isFinite(r.end_position.y)).toBe(true)
  })

  it('does not throw when the boundary radius is degenerate', () => {
    expect(() => simulate(100, 90, 10, { boundary: 5 })).not.toThrow()
  })

  it('mirrors: a leg-side shot against a mirrored field scores like its off-side twin', () => {
    // +angle = off side. Mirroring the field in X and negating the angle must
    // give the same discrete outcome with the same seed - the engine has no
    // handedness of its own. (Names differ, so compare everything else.)
    const mirrored = FIELD.map((f) => ({ ...f, x: -f.x }))
    for (const seed of [1, 2, 3, 4, 5, 6, 7, 8]) {
      const off = simulate(95, 35, 12, { seed })
      const leg = simulate(95, -35, 12, { seed, field: mirrored })
      expect(leg.outcome).toBe(off.outcome)
      expect(leg.runs).toBe(off.runs)
      expect(leg.end_position.x).toBeCloseTo(-off.end_position.x, 6)
      expect(leg.end_position.y).toBeCloseTo(off.end_position.y, 6)
    }
  })
})
