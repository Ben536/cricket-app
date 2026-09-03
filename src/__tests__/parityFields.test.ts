/**
 * The parity suite's copy of the UI field presets must match the source.
 *
 * tools/parity/gen_shots.py embeds FIELD_PRESET_POSITIONS (screen percent)
 * and converts them with the same arithmetic as fieldZones.screenToField.
 * If someone moves a fielder in the UI without updating the generator, the
 * parity suite would silently keep testing the old layout - this test fails
 * instead.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { FIELD_PRESET_POSITIONS, screenToField } from '../fieldZones'

const here = dirname(fileURLToPath(import.meta.url))
const shots = JSON.parse(readFileSync(join(here, '..', '..', 'tools', 'parity', 'shots.json'), 'utf-8')) as {
  fields: Record<string, Array<{ x: number; y: number; name: string }>>
}

describe('parity shots.json field presets', () => {
  for (const [name, positions] of Object.entries(FIELD_PRESET_POSITIONS)) {
    for (const hand of ['RH', 'LH'] as const) {
      it(`${name} (${hand}) matches fieldZones.ts`, () => {
        const key = `${name} (${hand})`
        expect(shots.fields[key], `gen_shots.py has no field "${key}" - re-run it`).toBeDefined()
        const expected = positions.map((p) => screenToField(hand === 'LH' ? 100 - p.x : p.x, p.y))
        expect(shots.fields[key].length).toBe(expected.length)
        shots.fields[key].forEach((f, i) => {
          expect(f.x).toBeCloseTo(expected[i].x, 9)
          expect(f.y).toBeCloseTo(expected[i].y, 9)
        })
      })
    }
  }
})
