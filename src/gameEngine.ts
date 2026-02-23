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
  fielder_position?: { x: number; y: number }  // Field coordinates of involved fielder
  end_position: { x: number; y: number }        // Where ball ended up (fielder, boundary, or landing)
  description: string
  trajectory: TrajectoryData
  catch_analysis?: CatchAnalysis  // Detailed catch difficulty breakdown
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
const CATCH_HEIGHT_MIN = 0.3
const CATCH_HEIGHT_MAX = 3.5
const GROUND_FIELDING_RANGE = 4.0
const INNER_RING_RADIUS = 15.0
const MID_FIELD_RADIUS = 30.0

// Fielder movement constants
const FIELDER_REACTION_TIME = 0.25  // seconds to react and start moving
const FIELDER_RUN_SPEED = 6.5       // m/s - professional fielder sprint speed
const FIELDER_DIVE_RANGE = 2.0      // metres - diving catch extension
const FIELDER_STATIC_RANGE = 1.5    // metres - catch without moving

// Ground fielding time constants
const PITCH_LENGTH = 20.12          // metres between stumps (22 yards)
const TIME_FOR_FIRST_RUN = 5.0      // seconds - includes reaction, call, start from stationary
const TIME_FOR_EXTRA_RUN = 4.0      // seconds - already moving, just turn and run
const THROW_SPEED = 28.0            // m/s - average professional throw speed
const COLLECTION_TIME_DIRECT = 0.7  // seconds - ball straight to fielder, clean pickup
const COLLECTION_TIME_MOVING = 1.2  // seconds - fielder moves to collect while ball moving
const COLLECTION_TIME_DIVING = 1.8  // seconds - diving stop, recover, throw
const PICKUP_TIME_STOPPED = 0.5     // seconds - picking up a stationary ball
const GROUND_FRICTION = 0.08        // deceleration factor per metre (ball slows on grass)

// Difficulty weights for catch scoring
const WEIGHT_REACTION = 0.25        // How much time pressure matters
const WEIGHT_MOVEMENT = 0.35        // How far fielder must move
const WEIGHT_HEIGHT = 0.20          // Awkwardness of catch height
const WEIGHT_SPEED = 0.20           // Ball speed at fielder

export interface TrajectoryData {
  projected_distance: number
  max_height: number
  landing_x: number
  landing_y: number
  time_of_flight: number
  horizontal_speed: number  // m/s along ground
  vertical_speed: number    // m/s initial vertical component
  direction_x: number       // unit vector x component
  direction_y: number       // unit vector y component
}

/**
 * Calculate ball trajectory from speed and angles
 * Returns full trajectory data including timing for fielder calculations
 */
export function calculateTrajectory(
  speedKmh: number,
  hAngle: number,
  vAngle: number
): TrajectoryData {
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
  // +angle = off side, field coords: +x = leg side, +y = toward bowler (down screen)
  const landingX = -distance * Math.sin(hRad)
  const landingY = distance * Math.cos(hRad)  // Positive = toward bowler

  // Calculate direction unit vector
  const dirMag = Math.sqrt(landingX * landingX + landingY * landingY)
  const dirX = dirMag > 0 ? landingX / dirMag : 0
  const dirY = dirMag > 0 ? landingY / dirMag : -1

  return {
    projected_distance: distance,
    max_height: maxHeight,
    landing_x: landingX,
    landing_y: landingY,
    time_of_flight: tFlight,
    horizontal_speed: vHorizontal,
    vertical_speed: vVertical,
    direction_x: dirX,
    direction_y: dirY,
  }
}

/**
 * Get ball position at a specific time along trajectory
 */
function getBallPositionAtTime(
  trajectory: TrajectoryData,
  time: number
): { x: number; y: number; z: number } {
  const g = 9.81
  // Horizontal position: constant velocity
  const horizontalDist = trajectory.horizontal_speed * time
  const x = horizontalDist * trajectory.direction_x
  const y = horizontalDist * trajectory.direction_y
  // Vertical position: projectile motion from height 1m
  const z = 1 + trajectory.vertical_speed * time - 0.5 * g * time * time
  return { x, y, z: Math.max(0, z) }
}

/**
 * Calculate time when ball reaches a given horizontal distance
 */
function getTimeAtDistance(trajectory: TrajectoryData, distance: number): number {
  if (trajectory.horizontal_speed <= 0) return Infinity
  return distance / trajectory.horizontal_speed
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

/**
 * Calculate the point where the ball path intersects the boundary circle
 */
function getBoundaryIntersection(
  landingX: number,
  landingY: number,
  boundaryDistance: number
): { x: number; y: number } {
  const distance = Math.sqrt(landingX * landingX + landingY * landingY)
  if (distance === 0) {
    return { x: 0, y: -boundaryDistance }  // Default: straight back
  }
  // Scale the landing point to the boundary distance
  const scale = boundaryDistance / distance
  return {
    x: landingX * scale,
    y: landingY * scale,
  }
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

export interface CatchAnalysis {
  canCatch: boolean
  difficulty: number          // 0-1, higher = harder
  catchType: 'regulation' | 'hard' | 'spectacular' | null
  reactionTime: number        // seconds available to react
  movementRequired: number    // metres fielder must move
  movementPossible: number    // metres fielder can move given time
  ballSpeedAtFielder: number  // km/h
  heightAtIntercept: number   // metres
  timeToIntercept: number     // seconds until ball reaches fielder
}

/**
 * Find the best point along the trajectory where a fielder could catch the ball.
 *
 * A real fielder will run to where they can make the most comfortable catch.
 * Priority:
 * 1. Can they reach ANY catchable point? If not, no catch.
 * 2. Among reachable points, prefer optimal height (1.0-1.8m chest height)
 * 3. If they can reach optimal height with time to spare, minimal difficulty
 * 4. If they're rushed or must catch at awkward height, higher difficulty
 */
function findCatchableIntercept(
  fielderX: number,
  fielderY: number,
  trajectory: TrajectoryData
): {
  time: number
  lateralDistance: number
  height: number
  hadTimeForOptimal: boolean  // Could they get to a comfortable catch position?
} {
  const OPTIMAL_HEIGHT_MIN = 1.0
  const OPTIMAL_HEIGHT_MAX = 1.8

  // Collect ALL reachable catch points
  const reachablePoints: Array<{
    time: number
    lateralDist: number
    height: number
    isOptimalHeight: boolean
    movementMargin: number  // How much spare movement capacity
  }> = []

  const timeStep = 0.05
  let t = 0.1

  while (t < trajectory.time_of_flight) {
    const pos = getBallPositionAtTime(trajectory, t)

    if (pos.z >= CATCH_HEIGHT_MIN && pos.z <= CATCH_HEIGHT_MAX) {
      const dx = pos.x - fielderX
      const dy = pos.y - fielderY
      const lateralDist = Math.sqrt(dx * dx + dy * dy)

      const movementTime = Math.max(0, t - FIELDER_REACTION_TIME)
      const movementPossible = movementTime * FIELDER_RUN_SPEED + FIELDER_DIVE_RANGE

      if (lateralDist <= movementPossible) {
        reachablePoints.push({
          time: t,
          lateralDist,
          height: pos.z,
          isOptimalHeight: pos.z >= OPTIMAL_HEIGHT_MIN && pos.z <= OPTIMAL_HEIGHT_MAX,
          movementMargin: movementPossible - lateralDist,
        })
      }
    }
    t += timeStep
  }

  if (reachablePoints.length === 0) {
    return { time: Infinity, lateralDistance: Infinity, height: 0, hadTimeForOptimal: false }
  }

  // Check if ANY optimal height catch is reachable
  const optimalPoints = reachablePoints.filter(p => p.isOptimalHeight)
  const hadTimeForOptimal = optimalPoints.length > 0

  // Pick the best point:
  // - If optimal height available, pick the one with most movement margin (easiest)
  // - Otherwise pick the one closest to optimal height
  let best: typeof reachablePoints[0]

  if (optimalPoints.length > 0) {
    // Pick optimal point with most margin (fielder arrives comfortably)
    best = optimalPoints.reduce((a, b) => a.movementMargin > b.movementMargin ? a : b)
  } else {
    // No optimal height reachable - pick the closest to optimal range
    best = reachablePoints.reduce((a, b) => {
      const aDist = a.height < OPTIMAL_HEIGHT_MIN
        ? OPTIMAL_HEIGHT_MIN - a.height
        : a.height - OPTIMAL_HEIGHT_MAX
      const bDist = b.height < OPTIMAL_HEIGHT_MIN
        ? OPTIMAL_HEIGHT_MIN - b.height
        : b.height - OPTIMAL_HEIGHT_MAX
      return aDist < bDist ? a : b
    })
  }

  return {
    time: best.time,
    lateralDistance: best.lateralDist,
    height: best.height,
    hadTimeForOptimal,
  }
}

/**
 * Calculate detailed catch difficulty based on trajectory, fielder position, and timing
 *
 * Key insight: A fielder runs to the BEST catch position they can reach.
 * - If they have time to reach optimal height (1.0-1.8m), height penalty is 0
 * - If they're rushed and must catch at awkward height, height penalty applies
 * - Movement penalty based on how much running/diving required
 */
function analyzeCatchDifficulty(
  fielderX: number,
  fielderY: number,
  trajectory: TrajectoryData,
  interceptDistance: number,
  lateralDistance: number
): CatchAnalysis {
  // Find the best catchable intercept point along the trajectory
  const intercept = findCatchableIntercept(fielderX, fielderY, trajectory)

  // If no catchable point found, return impossible
  if (intercept.time === Infinity) {
    const origTime = getTimeAtDistance(trajectory, interceptDistance)
    const origPos = getBallPositionAtTime(trajectory, origTime)
    return {
      canCatch: false,
      difficulty: 1,
      catchType: null,
      reactionTime: origTime,
      movementRequired: lateralDistance,
      movementPossible: 0,
      ballSpeedAtFielder: trajectory.horizontal_speed * 3.6,
      heightAtIntercept: origPos.z,
      timeToIntercept: origTime,
    }
  }

  const timeToIntercept = intercept.time
  const lateralDistActual = intercept.lateralDistance
  const heightAtIntercept = intercept.height

  // Time available for fielder to move (after reaction)
  const movementTime = Math.max(0, timeToIntercept - FIELDER_REACTION_TIME)

  // How far fielder can move in available time
  const movementPossible = movementTime * FIELDER_RUN_SPEED + FIELDER_DIVE_RANGE

  // === Calculate difficulty score (0 = easy, 1 = impossible) ===

  // 1. Reaction score: less time = harder
  // 0.5s or less = very hard (1.0), 2s+ = easy (0.0)
  const reactionScore = Math.max(0, Math.min(1, 1 - (timeToIntercept - 0.5) / 1.5))

  // 2. Movement score: how much running/diving needed
  let movementScore: number
  if (lateralDistActual <= FIELDER_STATIC_RANGE) {
    movementScore = 0  // Standing catch
  } else if (lateralDistActual <= FIELDER_STATIC_RANGE + FIELDER_DIVE_RANGE) {
    // Diving catch
    movementScore = 0.3 + 0.2 * ((lateralDistActual - FIELDER_STATIC_RANGE) / FIELDER_DIVE_RANGE)
  } else {
    // Running catch - score based on how close to max range
    const runDistance = lateralDistActual - FIELDER_STATIC_RANGE
    const maxRunDistance = movementPossible - FIELDER_STATIC_RANGE
    movementScore = maxRunDistance > 0 ? 0.5 + 0.5 * (runDistance / maxRunDistance) : 1
  }

  // 3. Height score: ONLY penalize if fielder couldn't reach optimal height
  // If they had time to get to a comfortable position, no penalty
  let heightScore: number
  if (intercept.hadTimeForOptimal) {
    // Fielder reached optimal position - easy catch height
    heightScore = 0
  } else {
    // Fielder was rushed - penalty based on how far from optimal
    if (heightAtIntercept >= 1.0 && heightAtIntercept <= 1.8) {
      heightScore = 0
    } else if (heightAtIntercept < 1.0) {
      heightScore = Math.min(1, (1.0 - heightAtIntercept) / 0.7)
    } else {
      heightScore = Math.min(1, (heightAtIntercept - 1.8) / 1.7)
    }
  }

  // 4. Speed score: faster ball = harder to judge and hold onto
  const ballSpeedKmh = trajectory.horizontal_speed * 3.6
  const speedScore = Math.max(0, Math.min(1, (ballSpeedKmh - 60) / 60))

  // Weighted difficulty
  const difficulty =
    WEIGHT_REACTION * reactionScore +
    WEIGHT_MOVEMENT * movementScore +
    WEIGHT_HEIGHT * heightScore +
    WEIGHT_SPEED * speedScore

  // Classify catch type
  let catchType: 'regulation' | 'hard' | 'spectacular'
  if (difficulty < 0.25) {
    catchType = 'regulation'
  } else if (difficulty < 0.6) {
    catchType = 'hard'
  } else {
    catchType = 'spectacular'
  }

  return {
    canCatch: true,
    difficulty,
    catchType,
    reactionTime: timeToIntercept,
    movementRequired: lateralDistActual,
    movementPossible,
    ballSpeedAtFielder: ballSpeedKmh,
    heightAtIntercept,
    timeToIntercept,
  }
}

/**
 * Roll catch outcome using continuous difficulty score
 * Maps difficulty (0-1) to catch probability with difficulty setting modifier
 *
 * If a catch is possible (fielder can reach it), outcome is ALWAYS caught or dropped.
 * No silent fallthrough to ground fielding - if they got there, they either take it or shell it.
 */
function rollCatchOutcome(
  catchAnalysis: CatchAnalysis,
  difficulty: Difficulty
): 'caught' | 'dropped' {
  // Base catch probability based on difficulty score
  // difficulty=0.00 → 98% catch
  // difficulty=0.25 → 85% catch
  // difficulty=0.50 → 72% catch
  // difficulty=0.75 → 59% catch
  // difficulty=1.00 → 46% catch (even hardest catches have decent chance)
  const baseCatchProb = 0.98 - 0.52 * catchAnalysis.difficulty

  // Difficulty setting modifiers
  const modifiers = {
    easy: 0.85,    // Fielders 15% worse
    medium: 1.0,   // Baseline
    hard: 1.10,    // Fielders 10% better
  }

  const catchProb = Math.min(0.99, baseCatchProb * modifiers[difficulty])

  return Math.random() < catchProb ? 'caught' : 'dropped'
}

function rollGroundFieldingOutcome(difficulty: Difficulty): string {
  const settings = DIFFICULTY_SETTINGS[difficulty]
  const probs = settings.ground_fielding
  const roll = Math.random()

  if (roll < probs.stopped) return 'stopped'
  if (roll < probs.stopped + probs.misfield_no_extra) return 'misfield_no_extra'
  return 'misfield_extra'
}

/**
 * Calculate average ground ball speed accounting for friction/deceleration.
 * Ball slows down as it travels along the grass.
 */
function getGroundBallSpeed(exitSpeedKmh: number, distance: number): number {
  const exitSpeedMs = exitSpeedKmh / 3.6
  // Ball loses speed due to friction - exponential decay model
  // Average speed over the distance is less than initial speed
  const frictionFactor = Math.exp(-GROUND_FRICTION * distance * 0.5)
  return Math.max(3.0, exitSpeedMs * frictionFactor)  // minimum 3 m/s (ball always rolls somewhat)
}

/**
 * Calculate time for ball to travel along ground to fielder position.
 */
function getBallTravelTime(exitSpeedKmh: number, distance: number): number {
  const avgSpeed = getGroundBallSpeed(exitSpeedKmh, distance)
  return distance / avgSpeed
}

/**
 * Calculate collection time based on how far fielder must move.
 */
function getCollectionTime(lateralDistance: number): number {
  if (lateralDistance < 0.5) {
    return COLLECTION_TIME_DIRECT  // Ball straight to them
  } else if (lateralDistance < 2.0) {
    return COLLECTION_TIME_MOVING  // Quick sidestep
  } else {
    return COLLECTION_TIME_DIVING  // Diving/stretching stop
  }
}

/**
 * Calculate distance from fielder to the relevant stumps.
 * For first run, batsman runs to bowler's end (0, -PITCH_LENGTH).
 * For second run, back to batting end (0, 0).
 * We use the shorter throw distance since fielder chooses which end.
 */
function getThrowDistance(fielderX: number, fielderY: number): number {
  // Batting end stumps at (0, 0), bowler's end at (0, -PITCH_LENGTH)
  const distToBattingEnd = Math.sqrt(fielderX * fielderX + fielderY * fielderY)
  const distToBowlerEnd = Math.sqrt(fielderX * fielderX + (fielderY + PITCH_LENGTH) * (fielderY + PITCH_LENGTH))
  return Math.min(distToBattingEnd, distToBowlerEnd)
}

/**
 * Calculate total fielding time from ball leaving bat to ball reaching stumps.
 */
function calculateFieldingTime(
  exitSpeedKmh: number,
  interceptDistance: number,
  lateralDistance: number,
  fielderX: number,
  fielderY: number
): number {
  const ballTravelTime = getBallTravelTime(exitSpeedKmh, interceptDistance)
  const collectionTime = getCollectionTime(lateralDistance)
  const throwDistance = getThrowDistance(fielderX, fielderY)
  const throwTime = throwDistance / THROW_SPEED

  return ballTravelTime + collectionTime + throwTime
}

/**
 * Calculate runs based on fielding time.
 * First run takes longer (reaction, call, start from stationary).
 * Subsequent runs are faster (already moving, just turn and go).
 */
function calculateRunsFromFieldingTime(
  fieldingTime: number,
  isMisfield: boolean
): number {
  // Add buffer time on misfields (ball goes past, fielder chases)
  const effectiveTime = isMisfield ? fieldingTime + 2.0 : fieldingTime

  // First run: need 5 seconds (reaction + call + run from standing)
  if (effectiveTime < TIME_FOR_FIRST_RUN) {
    return 0  // Dot ball
  }

  let runs = 1
  let timeRemaining = effectiveTime - TIME_FOR_FIRST_RUN

  // Second run: need 4 more seconds (already moving)
  if (timeRemaining >= TIME_FOR_EXTRA_RUN) {
    runs = 2
    timeRemaining -= TIME_FOR_EXTRA_RUN
  }

  // Third run: need another 4 seconds
  if (timeRemaining >= TIME_FOR_EXTRA_RUN) {
    runs = 3
  }

  return runs
}

/**
 * Legacy function - kept for cases where time-based calc isn't applicable
 */
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
  boundaryDistance: number = 70.0,
  difficulty: Difficulty = 'medium'
): Omit<SimulationResult, 'trajectory'> & { catch_analysis?: CatchAnalysis } {
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
      const boundaryPoint = getBoundaryIntersection(landingX, landingY, boundaryDistance)
      return {
        outcome: '6',
        runs: 6,
        is_boundary: true,
        is_aerial: true,
        fielder_involved: null,
        end_position: boundaryPoint,
        description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)} for six!`,
      }
    }
  }

  // Build trajectory data for catch analysis
  const trajectory = calculateTrajectory(exitSpeed, horizontalAngle, verticalAngle)

  // Check 2: Catching chances - any ball at catchable height (0.3m+) can be caught
  const isCatchable = maxHeight >= CATCH_HEIGHT_MIN
  if (isCatchable) {
    const catchingChances: Array<{
      fielder: string
      fielderX: number
      fielderY: number
      analysis: CatchAnalysis
      interceptDistance: number
    }> = []

    for (const fielder of fieldConfig) {
      if (!isFielderInBallPath(fielder.x, fielder.y, landingX, landingY)) continue

      const fielderDist = distanceFromBatter(fielder.x, fielder.y)
      if (fielderDist > projectedDistance + 10) continue  // Slightly extended range for running catches

      const { distance: lateralDist, closestX, closestY, t } = distancePointToLineSegment(
        fielder.x, fielder.y, batterX, batterY, landingX, landingY
      )
      if (t < 0.05) continue

      const interceptDistance = distanceFromBatter(closestX, closestY)

      // Analyze catch difficulty with full trajectory data
      const analysis = analyzeCatchDifficulty(
        fielder.x, fielder.y,
        trajectory,
        interceptDistance,
        lateralDist
      )

      if (analysis.canCatch) {
        catchingChances.push({
          fielder: fielder.name,
          fielderX: fielder.x,
          fielderY: fielder.y,
          analysis,
          interceptDistance,
        })
      }
    }

    // Sort by intercept distance (closest fielder to batter gets first chance)
    catchingChances.sort((a, b) => a.interceptDistance - b.interceptDistance)

    for (const chance of catchingChances) {
      const outcome = rollCatchOutcome(chance.analysis, difficulty)

      if (outcome === 'caught') {
        let catchDesc: string
        if (chance.analysis.catchType === 'spectacular') {
          catchDesc = 'Spectacular catch'
        } else if (chance.analysis.catchType === 'hard') {
          catchDesc = 'Great catch'
        } else {
          catchDesc = 'Caught'
        }

        // Add detail about running catches
        if (chance.analysis.movementRequired > FIELDER_STATIC_RANGE + 1) {
          catchDesc += ` (running ${chance.analysis.movementRequired.toFixed(1)}m)`
        } else if (chance.analysis.movementRequired > FIELDER_STATIC_RANGE) {
          catchDesc += ' (diving)'
        }

        // Calculate where the ball was when caught (intercept point)
        const catchPos = getBallPositionAtTime(trajectory, chance.analysis.timeToIntercept)
        return {
          outcome: 'caught',
          runs: 0,
          is_boundary: false,
          is_aerial: true,
          fielder_involved: chance.fielder,
          fielder_position: { x: chance.fielderX, y: chance.fielderY },
          end_position: { x: catchPos.x, y: catchPos.y },  // Where ball was caught
          description: `${catchDesc} at ${chance.fielder}!`,
          catch_analysis: chance.analysis,
        }
      } else if (outcome === 'dropped') {
        if (projectedDistance >= boundaryDistance) {
          const boundaryPoint = getBoundaryIntersection(landingX, landingY, boundaryDistance)
          return {
            outcome: '4',
            runs: 4,
            is_boundary: true,
            is_aerial: true,
            fielder_involved: chance.fielder,
            fielder_position: { x: chance.fielderX, y: chance.fielderY },
            end_position: boundaryPoint,
            description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, dropped at ${chance.fielder}, four!`,
            catch_analysis: chance.analysis,
          }
        }
        const runs = calculateRunsForDistance(projectedDistance, false, exitSpeed > 80)
        return {
          outcome: 'dropped',
          runs,
          is_boundary: false,
          is_aerial: true,
          fielder_involved: chance.fielder,
          fielder_position: { x: chance.fielderX, y: chance.fielderY },
          end_position: { x: landingX, y: landingY },  // Lands where it would have
          description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, dropped at ${chance.fielder}, runs ${runs}`,
          catch_analysis: chance.analysis,
        }
      }
    }
  }

  // Check 3: Boundary (four)
  if (projectedDistance >= boundaryDistance) {
    const boundaryPoint = getBoundaryIntersection(landingX, landingY, boundaryDistance)
    return {
      outcome: '4',
      runs: 4,
      is_boundary: true,
      is_aerial: isAerial,
      fielder_involved: null,
      end_position: boundaryPoint,
      description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)} to the boundary for four!`,
    }
  }

  // Check 4: Ground fielding
  const groundChances: Array<{
    fielder: string
    fielderX: number
    fielderY: number
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
          fielderX: fielder.x,
          fielderY: fielder.y,
          lateralDistance: lateralDist,
          interceptDistance,
          fielderDistance: fielderDist,
        })
      }
    }
  }

  groundChances.sort((a, b) => a.lateralDistance - b.lateralDistance)

  for (const chance of groundChances) {
    const outcome = rollGroundFieldingOutcome(difficulty)

    // Calculate time-based runs using physics model
    const fieldingTime = calculateFieldingTime(
      exitSpeed,
      chance.interceptDistance,
      chance.lateralDistance,
      chance.fielderX,
      chance.fielderY
    )

    if (outcome === 'stopped') {
      // Clean fielding - calculate runs based on fielding time
      const runs = calculateRunsFromFieldingTime(fieldingTime, false)

      if (runs === 0) {
        return {
          outcome: 'dot',
          runs: 0,
          is_boundary: false,
          is_aerial: isAerial,
          fielder_involved: chance.fielder,
          fielder_position: { x: chance.fielderX, y: chance.fielderY },
          end_position: { x: chance.fielderX, y: chance.fielderY },
          description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)} fielded by ${chance.fielder}, no run`,
        }
      }
      return {
        outcome: String(runs),
        runs,
        is_boundary: false,
        is_aerial: isAerial,
        fielder_involved: chance.fielder,
        fielder_position: { x: chance.fielderX, y: chance.fielderY },
        end_position: { x: chance.fielderX, y: chance.fielderY },
        description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, ${chance.fielder} fields, ${runs} run${runs > 1 ? 's' : ''}`,
      }
    } else if (outcome === 'misfield_no_extra') {
      // Fumbled but recovered - slight delay, ball stays near fielder
      const runs = Math.max(1, calculateRunsFromFieldingTime(fieldingTime + 0.8, false))
      return {
        outcome: 'misfield',
        runs,
        is_boundary: false,
        is_aerial: isAerial,
        fielder_involved: chance.fielder,
        fielder_position: { x: chance.fielderX, y: chance.fielderY },
        end_position: { x: chance.fielderX, y: chance.fielderY },
        description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, misfield by ${chance.fielder}, ${runs} run${runs > 1 ? 's' : ''}`,
      }
    } else {
      // Ball gets past fielder - they must chase and throw from further back
      const runs = calculateRunsFromFieldingTime(fieldingTime, true)
      return {
        outcome: 'misfield',
        runs,
        is_boundary: false,
        is_aerial: isAerial,
        fielder_involved: chance.fielder,
        fielder_position: { x: chance.fielderX, y: chance.fielderY },
        end_position: { x: landingX, y: landingY },
        description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, misfield by ${chance.fielder}, ${runs} run${runs > 1 ? 's' : ''}`,
      }
    }
  }

  // No fielder directly in ball path - find nearest fielder to landing point
  let nearestFielder: { name: string; x: number; y: number; distance: number } | null = null
  for (const fielder of fieldConfig) {
    const dx = fielder.x - landingX
    const dy = fielder.y - landingY
    const dist = Math.sqrt(dx * dx + dy * dy)
    if (!nearestFielder || dist < nearestFielder.distance) {
      nearestFielder = { name: fielder.name, x: fielder.x, y: fielder.y, distance: dist }
    }
  }

  if (nearestFielder) {
    // Fielder can move while ball is in flight (after reaction time)
    const ballTravelTime = getBallTravelTime(exitSpeed, projectedDistance)
    const fielderAvailableRunTime = Math.max(0, ballTravelTime - FIELDER_REACTION_TIME)
    const distanceCoveredDuringFlight = fielderAvailableRunTime * FIELDER_RUN_SPEED
    const remainingDistance = Math.max(0, nearestFielder.distance - distanceCoveredDuringFlight)
    const additionalRunTime = remainingDistance / FIELDER_RUN_SPEED

    // Ball has landed and stopped - just need to pick it up
    const collectionTime = PICKUP_TIME_STOPPED
    const throwDistance = getThrowDistance(landingX, landingY)
    const throwTime = throwDistance / THROW_SPEED

    const totalTime = ballTravelTime + additionalRunTime + collectionTime + throwTime
    const runs = calculateRunsFromFieldingTime(totalTime, false)

    return {
      outcome: runs > 0 ? String(runs) : 'dot',
      runs,
      is_boundary: false,
      is_aerial: isAerial,
      fielder_involved: nearestFielder.name,
      fielder_position: { x: nearestFielder.x, y: nearestFielder.y },
      end_position: { x: landingX, y: landingY },
      description: runs > 0
        ? `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, ${nearestFielder.name} retrieves, ${runs} run${runs > 1 ? 's' : ''}`
        : `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, ${nearestFielder.name} collects, no run`,
    }
  }

  // Fallback (no fielders at all - shouldn't happen)
  return {
    outcome: '4',
    runs: 4,
    is_boundary: true,
    is_aerial: isAerial,
    fielder_involved: null,
    end_position: { x: landingX, y: landingY },
    description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)} to the boundary`,
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
