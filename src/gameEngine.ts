/**
 * Cricket Shot Outcome Simulator - TypeScript Version
 *
 * Determines the outcome of cricket shots based on ball trajectory
 * and field configuration. Runs entirely in the browser.
 */

// Types
export interface FielderConfig {
  x: number
  y: number
  name: string
}

export interface SimulationResult {
  outcome: string
  runs: number
  is_boundary: boolean
  is_aerial: boolean
  fielder_involved: string | null
  description: string
  trajectory: {
    projected_distance: number
    max_height: number
    landing_x: number
    landing_y: number
  }
}

type Difficulty = 'easy' | 'medium' | 'hard'

// Difficulty settings
const DIFFICULTY_SETTINGS = {
  easy: {
    regulation_catch: { caught: 0.70, dropped: 0.20, runs: 0.10 },
    hard_catch: { caught: 0.30, dropped: 0.40, runs: 0.30 },
    ground_fielding: { stopped: 0.70, misfield_no_extra: 0.20, misfield_extra: 0.10 },
  },
  medium: {
    regulation_catch: { caught: 0.90, dropped: 0.08, runs: 0.02 },
    hard_catch: { caught: 0.55, dropped: 0.30, runs: 0.15 },
    ground_fielding: { stopped: 0.85, misfield_no_extra: 0.10, misfield_extra: 0.05 },
  },
  hard: {
    regulation_catch: { caught: 0.98, dropped: 0.02, runs: 0.00 },
    hard_catch: { caught: 0.75, dropped: 0.20, runs: 0.05 },
    ground_fielding: { stopped: 0.95, misfield_no_extra: 0.04, misfield_extra: 0.01 },
  },
}

// Thresholds
const CATCH_LATERAL_EASY = 1.0
const CATCH_LATERAL_HARD = 2.5
const CATCH_LATERAL_MAX = 3.0
const CATCH_HEIGHT_MIN = 0.3
const CATCH_HEIGHT_EASY_MIN = 0.5
const CATCH_HEIGHT_EASY_MAX = 2.2
const CATCH_HEIGHT_MAX = 3.5
const CATCH_SPEED_EASY = 80
const CATCH_SPEED_HARD = 120
const GROUND_FIELDING_RANGE = 4.0
const INNER_RING_RADIUS = 15.0
const MID_FIELD_RADIUS = 30.0

/**
 * Calculate ball trajectory from speed and angles
 */
export function calculateTrajectory(
  speedKmh: number,
  hAngle: number,
  vAngle: number
): { projected_distance: number; max_height: number; landing_x: number; landing_y: number } {
  const speedMs = speedKmh / 3.6
  const hRad = (hAngle * Math.PI) / 180
  const vRad = (vAngle * Math.PI) / 180

  const vHorizontal = speedMs * Math.cos(vRad)
  const vVertical = speedMs * Math.sin(vRad)
  const g = 9.81

  let tFlight: number
  let maxHeight: number

  if (vVertical > 0) {
    const tUp = vVertical / g
    const apexHeight = 1 + (vVertical * vVertical) / (2 * g)
    const tDown = Math.sqrt((2 * apexHeight) / g)
    tFlight = tUp + tDown
    maxHeight = apexHeight
  } else {
    tFlight = Math.sqrt(2 / g)
    maxHeight = 1.0
  }

  const distance = vHorizontal * tFlight
  const landingX = distance * Math.sin(hRad)
  const landingY = -distance * Math.cos(hRad)

  return {
    projected_distance: distance,
    max_height: maxHeight,
    landing_x: landingX,
    landing_y: landingY,
  }
}

function getShotDirectionName(horizontalAngle: number, isAerial: boolean): string {
  let angle = horizontalAngle
  while (angle > 180) angle -= 360
  while (angle < -180) angle += 360

  if (angle >= -15 && angle <= 15) {
    return isAerial ? 'lofted straight' : 'driven straight'
  } else if (angle > 15 && angle <= 45) {
    return isAerial ? 'lofted over cover' : 'driven through cover'
  } else if (angle > 45 && angle <= 75) {
    return isAerial ? 'cut in the air' : 'cut'
  } else if (angle > 75 && angle <= 105) {
    return isAerial ? 'upper cut' : 'square cut'
  } else if (angle > 105 && angle <= 135) {
    return isAerial ? 'edged' : 'late cut'
  } else if (angle > 135 || angle < -135) {
    return isAerial ? 'edged in the air' : 'edged behind'
  } else if (angle >= -135 && angle < -105) {
    return isAerial ? 'flicked fine' : 'glanced fine'
  } else if (angle >= -105 && angle < -75) {
    return isAerial ? 'swept in the air' : 'swept'
  } else if (angle >= -75 && angle < -45) {
    return isAerial ? 'hooked' : 'pulled'
  } else if (angle >= -45 && angle < -15) {
    return isAerial ? 'lofted over midwicket' : 'flicked through midwicket'
  }
  return 'played'
}

function getBallHeightAtDistance(
  distanceFromBatter: number,
  projectedDistance: number,
  maxHeight: number,
  verticalAngle: number
): number {
  if (projectedDistance <= 0) return 0

  const startHeight = 1.0

  if (verticalAngle < 5) {
    if (distanceFromBatter >= projectedDistance) return 0
    return Math.max(0, startHeight * (1 - distanceFromBatter / projectedDistance))
  }

  const apexFraction = 0.3 + (verticalAngle / 90) * 0.2
  const apexDistance = projectedDistance * apexFraction

  if (distanceFromBatter <= apexDistance) {
    const t = distanceFromBatter / apexDistance
    return startHeight + (maxHeight - startHeight) * (2 * t - t * t)
  } else {
    const remaining = projectedDistance - apexDistance
    if (remaining <= 0) return 0
    const t = (distanceFromBatter - apexDistance) / remaining
    return Math.max(0, maxHeight * (1 - t * t))
  }
}

function distancePointToLineSegment(
  px: number, py: number,
  x1: number, y1: number,
  x2: number, y2: number
): { distance: number; closestX: number; closestY: number; t: number } {
  const dx = x2 - x1
  const dy = y2 - y1

  if (dx === 0 && dy === 0) {
    return {
      distance: Math.sqrt((px - x1) ** 2 + (py - y1) ** 2),
      closestX: x1,
      closestY: y1,
      t: 0,
    }
  }

  let t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
  t = Math.max(0, Math.min(1, t))

  const closestX = x1 + t * dx
  const closestY = y1 + t * dy
  const distance = Math.sqrt((px - closestX) ** 2 + (py - closestY) ** 2)

  return { distance, closestX, closestY, t }
}

function distanceFromBatter(x: number, y: number): number {
  return Math.sqrt(x * x + y * y)
}

function isFielderInBallPath(
  fielderX: number, fielderY: number,
  landingX: number, landingY: number
): boolean {
  const shotLength = Math.sqrt(landingX ** 2 + landingY ** 2)
  if (shotLength < 0.1) return false

  const shotDirX = landingX / shotLength
  const shotDirY = landingY / shotLength
  const dot = fielderX * shotDirX + fielderY * shotDirY
  const fielderDistance = Math.sqrt(fielderX ** 2 + fielderY ** 2)

  if (fielderDistance < 10) {
    return dot > -5
  }
  return dot > 0
}

function classifyCatchDifficulty(
  lateralDistance: number,
  height: number,
  ballSpeed: number
): 'regulation' | 'hard' | null {
  if (lateralDistance > CATCH_LATERAL_MAX) return null
  if (height < CATCH_HEIGHT_MIN || height > CATCH_HEIGHT_MAX) return null

  let difficultyScore = 0

  if (lateralDistance > CATCH_LATERAL_EASY) difficultyScore++
  if (lateralDistance > CATCH_LATERAL_HARD) difficultyScore++
  if (height < CATCH_HEIGHT_EASY_MIN || height > CATCH_HEIGHT_EASY_MAX) difficultyScore++
  if (height < CATCH_HEIGHT_MIN + 0.1 || height > CATCH_HEIGHT_MAX - 0.3) difficultyScore++
  if (ballSpeed > CATCH_SPEED_EASY) difficultyScore++
  if (ballSpeed > CATCH_SPEED_HARD) difficultyScore++

  return difficultyScore >= 2 ? 'hard' : 'regulation'
}

function rollFieldingOutcome(
  outcomeType: 'regulation_catch' | 'hard_catch' | 'ground_fielding',
  difficulty: Difficulty
): string {
  const settings = DIFFICULTY_SETTINGS[difficulty]
  const roll = Math.random()

  if (outcomeType === 'regulation_catch' || outcomeType === 'hard_catch') {
    const probs = settings[outcomeType]
    if (roll < probs.caught) return 'caught'
    if (roll < probs.caught + probs.dropped) return 'dropped'
    return 'runs'
  } else {
    const probs = settings.ground_fielding
    if (roll < probs.stopped) return 'stopped'
    if (roll < probs.stopped + probs.misfield_no_extra) return 'misfield_no_extra'
    return 'misfield_extra'
  }
}

function calculateRunsForDistance(
  distance: number,
  isStopped: boolean,
  hitFirmly: boolean
): number {
  if (isStopped) {
    return hitFirmly ? 1 : 0
  }

  if (distance >= MID_FIELD_RADIUS) {
    return Math.random() < 0.33 ? 3 : 2
  } else if (distance >= INNER_RING_RADIUS) {
    return Math.random() < 0.33 ? 2 : 1
  }
  return 1
}

/**
 * Main simulation function
 */
export function simulateDelivery(
  exitSpeed: number,
  horizontalAngle: number,
  verticalAngle: number,
  landingX: number,
  landingY: number,
  projectedDistance: number,
  maxHeight: number,
  fieldConfig: FielderConfig[],
  boundaryDistance: number = 65.0,
  difficulty: Difficulty = 'medium'
): Omit<SimulationResult, 'trajectory'> {
  const isAerial = maxHeight > 1.5 || verticalAngle > 10
  const shotName = getShotDirectionName(horizontalAngle, isAerial)
  const batterX = 0
  const batterY = 0

  // Check 1: Boundary (six)
  if (projectedDistance >= boundaryDistance) {
    const heightAtBoundary = getBallHeightAtDistance(
      boundaryDistance, projectedDistance, maxHeight, verticalAngle
    )
    if (isAerial && heightAtBoundary > 0.5) {
      return {
        outcome: '6',
        runs: 6,
        is_boundary: true,
        is_aerial: true,
        fielder_involved: null,
        description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)} for six!`,
      }
    }
  }

  // Check 2: Catching chances
  if (isAerial) {
    const catchingChances: Array<{
      fielder: string
      lateralDistance: number
      height: number
      catchType: 'regulation' | 'hard'
      interceptDistance: number
    }> = []

    for (const fielder of fieldConfig) {
      if (!isFielderInBallPath(fielder.x, fielder.y, landingX, landingY)) continue

      const fielderDist = distanceFromBatter(fielder.x, fielder.y)
      if (fielderDist > projectedDistance + 5) continue

      const { distance: lateralDist, closestX, closestY, t } = distancePointToLineSegment(
        fielder.x, fielder.y, batterX, batterY, landingX, landingY
      )
      if (t < 0.05) continue

      const interceptDistance = distanceFromBatter(closestX, closestY)
      const ballHeight = getBallHeightAtDistance(
        interceptDistance, projectedDistance, maxHeight, verticalAngle
      )

      const catchType = classifyCatchDifficulty(lateralDist, ballHeight, exitSpeed)
      if (catchType) {
        catchingChances.push({
          fielder: fielder.name,
          lateralDistance: lateralDist,
          height: ballHeight,
          catchType,
          interceptDistance,
        })
      }
    }

    catchingChances.sort((a, b) => a.interceptDistance - b.interceptDistance)

    for (const chance of catchingChances) {
      const outcome = rollFieldingOutcome(`${chance.catchType}_catch` as 'regulation_catch' | 'hard_catch', difficulty)

      if (outcome === 'caught') {
        const catchDesc = chance.catchType === 'regulation' ? 'Caught' : 'Great catch'
        return {
          outcome: 'caught',
          runs: 0,
          is_boundary: false,
          is_aerial: true,
          fielder_involved: chance.fielder,
          description: `${catchDesc} at ${chance.fielder}!`,
        }
      } else if (outcome === 'dropped') {
        if (projectedDistance >= boundaryDistance) {
          return {
            outcome: '4',
            runs: 4,
            is_boundary: true,
            is_aerial: true,
            fielder_involved: chance.fielder,
            description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, dropped at ${chance.fielder}, four!`,
          }
        }
        const runs = calculateRunsForDistance(projectedDistance, false, exitSpeed > 80)
        return {
          outcome: 'dropped',
          runs,
          is_boundary: false,
          is_aerial: true,
          fielder_involved: chance.fielder,
          description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, dropped at ${chance.fielder}, runs ${runs}`,
        }
      }
    }
  }

  // Check 3: Boundary (four)
  if (projectedDistance >= boundaryDistance) {
    return {
      outcome: '4',
      runs: 4,
      is_boundary: true,
      is_aerial: isAerial,
      fielder_involved: null,
      description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)} to the boundary for four!`,
    }
  }

  // Check 4: Ground fielding
  const groundChances: Array<{
    fielder: string
    lateralDistance: number
    interceptDistance: number
    fielderDistance: number
  }> = []

  for (const fielder of fieldConfig) {
    if (!isFielderInBallPath(fielder.x, fielder.y, landingX, landingY)) continue

    const { distance: lateralDist, closestX, closestY, t } = distancePointToLineSegment(
      fielder.x, fielder.y, batterX, batterY, landingX, landingY
    )
    if (t < 0.05) continue

    if (lateralDist <= GROUND_FIELDING_RANGE) {
      const interceptDistance = distanceFromBatter(closestX, closestY)
      const fielderDist = distanceFromBatter(fielder.x, fielder.y)
      if (fielderDist <= projectedDistance + GROUND_FIELDING_RANGE) {
        groundChances.push({
          fielder: fielder.name,
          lateralDistance: lateralDist,
          interceptDistance,
          fielderDistance: fielderDist,
        })
      }
    }
  }

  groundChances.sort((a, b) => a.lateralDistance - b.lateralDistance)
  const hitFirmly = exitSpeed > 80

  for (const chance of groundChances) {
    const outcome = rollFieldingOutcome('ground_fielding', difficulty)
    const hitToFielder = chance.lateralDistance < 1.5

    if (outcome === 'stopped') {
      if (hitToFielder && !hitFirmly) {
        return {
          outcome: 'dot',
          runs: 0,
          is_boundary: false,
          is_aerial: isAerial,
          fielder_involved: chance.fielder,
          description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)} straight to ${chance.fielder}`,
        }
      }
      return {
        outcome: '1',
        runs: 1,
        is_boundary: false,
        is_aerial: isAerial,
        fielder_involved: chance.fielder,
        description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, ${chance.fielder} fields, 1 run`,
      }
    } else if (outcome === 'misfield_no_extra') {
      const runs = Math.max(1, calculateRunsForDistance(chance.fielderDistance, true, hitFirmly))
      return {
        outcome: 'misfield',
        runs,
        is_boundary: false,
        is_aerial: isAerial,
        fielder_involved: chance.fielder,
        description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, misfield by ${chance.fielder}, ${runs} run${runs > 1 ? 's' : ''}`,
      }
    } else {
      const baseRuns = calculateRunsForDistance(chance.fielderDistance, false, hitFirmly)
      const runs = Math.min(baseRuns + 1, 3)
      return {
        outcome: 'misfield',
        runs,
        is_boundary: false,
        is_aerial: isAerial,
        fielder_involved: chance.fielder,
        description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, misfield by ${chance.fielder}, ${runs} runs`,
      }
    }
  }

  // No fielder involved
  const runs = calculateRunsForDistance(projectedDistance, false, hitFirmly)
  return {
    outcome: runs > 0 ? String(runs) : 'dot',
    runs,
    is_boundary: false,
    is_aerial: isAerial,
    fielder_involved: null,
    description: runs > 0
      ? `${shotName.charAt(0).toUpperCase() + shotName.slice(1)} into the gap for ${runs}`
      : `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, no run`,
  }
}

// Default field configuration
export const DEFAULT_FIELD: FielderConfig[] = [
  { x: 0, y: 3, name: 'wicketkeeper' },
  { x: 5, y: 4, name: 'first slip' },
  { x: 7, y: 5, name: 'second slip' },
  { x: 8, y: -2, name: 'gully' },
  { x: 15, y: -15, name: 'point' },
  { x: 20, y: -30, name: 'cover' },
  { x: 5, y: -35, name: 'mid-off' },
  { x: -5, y: -35, name: 'mid-on' },
  { x: -20, y: -25, name: 'midwicket' },
  { x: -15, y: -10, name: 'square leg' },
  { x: -45, y: -45, name: 'deep midwicket' },
]
