/**
 * Wagon-wheel geometry. These numbers become ground-truth labels in the
 * recordings, so a sign error here silently poisons every future tuning run
 * (and a mirrored wheel is exactly the class of bug the detector had).
 *
 * Convention: 0 = toward the bowler, +90 = LEG, -90 = OFF, +/-180 = behind
 * the keeper, always in the RIGHT-HANDED frame. The engine's angle is the
 * opposite sign (+off).
 */

import { describe, it, expect } from 'vitest'

import {
  markToPoint,
  pointToMark,
  outcomeColour,
  WHEEL_CX,
  WHEEL_CY,
  WHEEL_R,
} from '../wagonWheelGeometry'

describe('pointToMark', () => {
  it('maps the four cardinal taps to the documented directions', () => {
    // Straight to the bowler is UP the screen (smaller y)
    expect(pointToMark(WHEEL_CX, WHEEL_CY - WHEEL_R).direction_deg).toBe(0)
    // Leg side is to the right for a right-hander
    expect(pointToMark(WHEEL_CX + WHEEL_R, WHEEL_CY).direction_deg).toBe(90)
    // Off side is to the left
    expect(pointToMark(WHEEL_CX - WHEEL_R, WHEEL_CY).direction_deg).toBe(-90)
    // Behind the keeper
    expect(Math.abs(pointToMark(WHEEL_CX, WHEEL_CY + WHEEL_R).direction_deg)).toBe(180)
  })

  it('reports distance as a fraction of the rope, clamped at 1', () => {
    expect(pointToMark(WHEEL_CX, WHEEL_CY).distance_norm).toBe(0)
    expect(pointToMark(WHEEL_CX, WHEEL_CY - WHEEL_R / 2).distance_norm).toBeCloseTo(0.5, 2)
    expect(pointToMark(WHEEL_CX, WHEEL_CY - WHEEL_R).distance_norm).toBe(1)
    // Beyond the rope still reads as a boundary, never more
    expect(pointToMark(WHEEL_CX, WHEEL_CY - WHEEL_R * 3).distance_norm).toBe(1)
  })

  it('mirrors the DISPLAY for a left-hander but records the right-handed value', () => {
    const rightTap = pointToMark(WHEEL_CX + WHEEL_R, WHEEL_CY, false)
    const leftTap = pointToMark(WHEEL_CX + WHEEL_R, WHEEL_CY, true)
    expect(rightTap.direction_deg).toBe(90)   // right-hander: right of screen = leg
    expect(leftTap.direction_deg).toBe(-90)   // left-hander taps the same pixel = off
    expect(leftTap.distance_norm).toBe(rightTap.distance_norm)
  })
})

describe('markToPoint', () => {
  it('round-trips with pointToMark', () => {
    for (const direction of [-180, -135, -90, -45, 0, 30, 90, 150]) {
      for (const distance of [0.25, 0.6, 1]) {
        const p = markToPoint({ direction_deg: direction, distance_norm: distance })
        const back = pointToMark(p.x, p.y)
        expect(back.distance_norm).toBeCloseTo(distance, 2)
        // -180 and +180 are the same bearing
        const diff = Math.abs(((back.direction_deg - direction + 540) % 360) - 180)
        expect(diff).toBeLessThan(0.2)
      }
    }
  })

  it('defaults a mark with no distance to the boundary', () => {
    const p = markToPoint({ direction_deg: 0 })
    expect(p.y).toBeCloseTo(WHEEL_CY - WHEEL_R, 5)
  })

  it('round-trips through the mirror too', () => {
    const p = markToPoint({ direction_deg: 40, distance_norm: 0.8 }, true)
    expect(pointToMark(p.x, p.y, true).direction_deg).toBeCloseTo(40, 1)
  })
})

describe('outcomeColour', () => {
  it('gives boundaries and wickets their own colour and never returns undefined', () => {
    expect(outcomeColour('6')).not.toBe(outcomeColour('4'))
    expect(outcomeColour('W')).not.toBe(outcomeColour('4'))
    for (const o of ['dot', '1', '2', '3', '4', '6', 'W', null, undefined, 'weird']) {
      expect(outcomeColour(o)).toMatch(/^#[0-9a-f]{6}$/i)
    }
  })
})
