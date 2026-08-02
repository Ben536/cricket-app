/**
 * Run the canonical parity shots through the TYPESCRIPT engine.
 *
 * Mirrors the production offline path exactly (useServerSimulation.runLocal):
 * trajectory from calculateTrajectory, then simulateDelivery.
 * Writes results_ts.json. Run with: npx tsx tools/parity/run_ts.ts
 */

import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { calculateTrajectory, simulateDelivery, type FielderConfig } from '../../src/gameEngine'

const here = dirname(fileURLToPath(import.meta.url))
const shotsBytes = readFileSync(join(here, 'shots.json'))
const shotsSha = createHash('sha256').update(shotsBytes).digest('hex')
const data = JSON.parse(shotsBytes.toString('utf-8')) as {
  field: FielderConfig[]
  shots: Array<{
    exit_speed: number
    horizontal_angle: number
    vertical_angle: number
    boundary_distance: number
    difficulty: 'easy' | 'medium' | 'hard'
    seed: number
  }>
}

const results = data.shots.map((shot) => {
  const traj = calculateTrajectory(shot.exit_speed, shot.horizontal_angle, shot.vertical_angle)
  const r = simulateDelivery(
    shot.exit_speed,
    shot.horizontal_angle,
    shot.vertical_angle,
    traj.landing_x,
    traj.landing_y,
    traj.projected_distance,
    traj.max_height,
    data.field,
    shot.boundary_distance,
    shot.difficulty,
    shot.seed,
  )
  return {
    outcome: r.outcome,
    runs: r.runs,
    is_boundary: r.is_boundary,
    is_aerial: r.is_aerial,
    fielder_involved: r.fielder_involved,
    end_x: r.end_position.x,
    end_y: r.end_position.y,
    fielding_time: r.fielding_time ?? null,
    boundary_distance: r.boundary_distance ?? null,
    seed: r.seed ?? null,
  }
})

// Stamp the input hash - see the note in run_py.py.
writeFileSync(join(here, 'results_ts.json'), JSON.stringify({ shots_sha: shotsSha, results }))
console.log(`typescript engine: ${results.length} results -> results_ts.json`)
