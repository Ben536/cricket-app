/**
 * Cricket Shot Outcome Simulator - TypeScript Version
 *
 * Determines the outcome of cricket shots based on ball trajectory
 * and field configuration. Runs entirely in the browser.
 *
 * PARITY: this engine and engine/game_engine.py must produce IDENTICAL
 * results for identical inputs + seed - the app uses the Python engine when
 * connected to the Pi and this one when offline, so any divergence means the
 * same shot scores differently depending on WiFi. All tunable constants come
 * from the shared engine/engine_params.json, randomness comes from the shared
 * mulberry32 PRNG, and tools/parity/ holds the golden cross-engine tests.
 */

import PARAMS from '../engine/engine_params.json'

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
  fielder_position?: { x: number; y: number }    // Field coordinates of involved fielder (start)
  fielding_position?: { x: number; y: number }   // Where fielder collects the ball (for animation)
  end_position: { x: number; y: number }         // Where ball ended up (fielder, boundary, or landing)
  description: string
  trajectory?: TrajectoryData
  catch_analysis?: CatchAnalysis  // Detailed catch difficulty breakdown
  fielding_time?: number          // Total time from bat to stumps (seconds)
  collection_difficulty?: number  // 0-1, how rushed was the fielder
  alignment_score?: number        // 0-1, how directly ball was going at fielder (0 = direct)
  priority_score?: number         // Combined weighted score used for fielder selection
  fielder_arrival_time?: number   // Seconds for fielder to reach intercept point
  ball_arrival_time?: number      // Seconds for ball to reach intercept point
  boundary_distance?: number      // Actual boundary distance at this shot angle (metres)
  seed?: number                   // PRNG seed used - replaying with it reproduces the outcome
}

type Difficulty = 'easy' | 'medium' | 'hard'
type Rand = () => number

/**
 * mulberry32 - shared PRNG, identical to engine/prng.py. A shot simulated
 * with the same seed produces the same outcome in BOTH engines.
 */
export function mulberry32(seed: number): Rand {
  let a = seed >>> 0
  return function () {
    a = (a + 0x6D2B79F5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1) >>> 0
    t = (t ^ (t + Math.imul(t ^ (t >>> 7), t | 61))) >>> 0
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

// ALL tunable constants come from the shared params file (engine/
// engine_params.json) - the Python engine loads the same file, so a constant
// can never fork between the engines again.
const GROUND_FIELDING_PROBS = PARAMS.ground_fielding as Record<
  Difficulty, { stopped: number; misfield_no_extra: number; misfield_extra: number }
>
const CATCH_DIFFICULTY_MODIFIER = PARAMS.catch_difficulty_modifier as Record<Difficulty, number>

const GRAVITY = PARAMS.gravity
const BAT_HEIGHT = PARAMS.bat_height
const BATTER_OFFSET_FROM_CENTER = PARAMS.batter_offset_from_center

const CATCH_HEIGHT_MIN = PARAMS.catch_height_min
const CATCH_HEIGHT_MAX = PARAMS.catch_height_max
const CATCH_OPTIMAL_MIN = PARAMS.catch_optimal_min
const CATCH_OPTIMAL_MAX = PARAMS.catch_optimal_max
const GROUND_FIELDING_RANGE = PARAMS.ground_fielding_range
const INNER_RING_RADIUS = PARAMS.inner_ring_radius
const MID_FIELD_RADIUS = PARAMS.mid_field_radius

const FIELDER_REACTION_TIME = PARAMS.fielder_reaction_time
const FIELDER_RUN_SPEED = PARAMS.fielder_run_speed
const FIELDER_DIVE_RANGE = PARAMS.fielder_dive_range
const FIELDER_STATIC_RANGE = PARAMS.fielder_static_range
const FIELDER_ACCEL_TIME = PARAMS.fielder_accel_time

const PITCH_LENGTH = PARAMS.pitch_length
const TIME_FOR_FIRST_RUN = PARAMS.time_for_first_run
const TIME_FOR_EXTRA_RUN = PARAMS.time_for_extra_run
const THROW_SPEED = PARAMS.throw_speed
const COLLECTION_TIME_DIRECT = PARAMS.collection_time_direct
const COLLECTION_TIME_MOVING = PARAMS.collection_time_moving
const COLLECTION_TIME_DIVING = PARAMS.collection_time_diving
const PICKUP_TIME_STOPPED = PARAMS.pickup_time_stopped
const GROUND_FRICTION = PARAMS.ground_friction
const MISFIELD_TIME_PENALTY = PARAMS.misfield_time_penalty
const FUMBLE_TIME_PENALTY = PARAMS.fumble_time_penalty

const WEIGHT_REACTION = PARAMS.weight_reaction
const WEIGHT_MOVEMENT = PARAMS.weight_movement
const WEIGHT_HEIGHT = PARAMS.weight_height
const WEIGHT_SPEED = PARAMS.weight_speed

const AERIAL_HEIGHT_THRESHOLD = PARAMS.aerial_height_threshold
const AERIAL_ANGLE_THRESHOLD = PARAMS.aerial_angle_threshold
const SIX_HEIGHT_AT_BOUNDARY = PARAMS.six_height_at_boundary

/** Normalize angle to -180..180 (positive modulo, matching Python). */
function normalizeAngle(angle: number): number {
  return ((((angle + 180) % 360) + 360) % 360) - 180
}

/**
 * Actual boundary distance from the BATTER at a given shot angle.
 *
 * The boundary is a circle centered on the pitch center, but the batter is
 * offset from it by BATTER_OFFSET_FROM_CENTER (8.84m): straight shots travel
 * further to the rope (~79m), shots behind square reach it sooner (~61m).
 * Ray-circle intersection: offset*cos(th) + sqrt(R^2 - offset^2*sin^2(th)).
 */
function getBoundaryDistanceAtAngle(horizontalAngle: number, boundaryRadius: number): number {
  const angleRad = (horizontalAngle * Math.PI) / 180
  const cosA = Math.cos(angleRad)
  const sinA = Math.sin(angleRad)
  const offset = BATTER_OFFSET_FROM_CENTER
  return offset * cosA + Math.sqrt(boundaryRadius * boundaryRadius - offset * offset * sinA * sinA)
}

export interface TrajectoryData {
  projected_distance: number  // Total distance including air + rolling
  aerial_distance: number     // Distance traveled through air only
  rolling_distance: number    // Distance ball rolls after landing
  max_height: number
  landing_x: number           // Where ball lands (after air travel)
  landing_y: number
  final_x: number             // Where ball stops (after rolling)
  final_y: number
  time_of_flight: number
  horizontal_speed: number    // m/s along ground
  vertical_speed: number      // m/s initial vertical component
  direction_x: number         // unit vector x component
  direction_y: number         // unit vector y component
}

/**
 * Calculate how far the ball rolls after landing.
 * Uses exponential decay model: v = v0 * e^(-k*d)
 * Ball effectively stops when speed drops below threshold.
 *
 * Factors:
 * - Landing speed (horizontal component)
 * - Impact angle (steeper = more energy lost on bounce)
 * - Ground friction coefficient
 */
function calculateRollingDistance(
  horizontalSpeedMs: number,
  verticalAngle: number
): number {
  // Energy retention on landing depends on impact angle
  // Low flat shots retain more speed, high lofted shots bounce and slow more
  // At 0 degrees: 85% retention, at 45 degrees: 40% retention, at 90 degrees: 5%
  const impactRetention = 0.85 - 0.8 * Math.sin((verticalAngle * Math.PI) / 180)
  const landingSpeed = horizontalSpeedMs * impactRetention

  // Ball stops when it slows to ~1.5 m/s (typical stopping threshold)
  const stopThreshold = 1.5
  if (landingSpeed <= stopThreshold) return 0

  // Exponential decay: v = v0 * e^(-k*d)
  // Solving for d when v = threshold: d = ln(v0/threshold) / k
  const rollingDistance = Math.log(landingSpeed / stopThreshold) / GROUND_FRICTION

  return Math.max(0, rollingDistance)
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
  // Sanitize inputs: clamp ranges, NORMALIZE the angle (a wraparound angle
  // from radar noise must not clamp to the wrong side of the field)
  const clampedSpeed = Math.max(0, Math.min(200, speedKmh))
  const clampedHAngle = normalizeAngle(hAngle)
  const clampedVAngle = Math.max(0, Math.min(90, vAngle))

  // Edge case: zero speed - no trajectory at all (PY parity)
  if (clampedSpeed <= 0) {
    return {
      projected_distance: 0, aerial_distance: 0, rolling_distance: 0,
      max_height: BAT_HEIGHT, landing_x: 0, landing_y: 0, final_x: 0, final_y: 0,
      time_of_flight: 0, horizontal_speed: 0, vertical_speed: 0,
      direction_x: 0, direction_y: -1,
    }
  }

  const speedMs = clampedSpeed / 3.6
  const hRad = (clampedHAngle * Math.PI) / 180
  const vRad = (clampedVAngle * Math.PI) / 180

  const vHorizontal = speedMs * Math.cos(vRad)
  const vVertical = speedMs * Math.sin(vRad)
  const g = GRAVITY

  // Edge case: hit (almost) straight up - lands near the batter (PY parity)
  if (vHorizontal < 0.1) {
    let tFlightUp: number
    let maxHeightUp: number
    if (vVertical > 0) {
      const tUp = vVertical / g
      maxHeightUp = BAT_HEIGHT + (vVertical * vVertical) / (2 * g)
      tFlightUp = 2 * tUp
    } else {
      tFlightUp = Math.sqrt((2 * BAT_HEIGHT) / g)
      maxHeightUp = BAT_HEIGHT
    }
    const aerialUp = 0.1
    const rollingUp = calculateRollingDistance(0.1, clampedVAngle)
    return {
      projected_distance: aerialUp + rollingUp,
      aerial_distance: aerialUp, rolling_distance: rollingUp,
      max_height: maxHeightUp, landing_x: 0, landing_y: 0, final_x: 0, final_y: 0,
      time_of_flight: tFlightUp, horizontal_speed: 0.1, vertical_speed: vVertical,
      direction_x: 0, direction_y: -1,
    }
  }

  let tFlight: number
  let maxHeight: number

  if (vVertical > 0) {
    const tUp = vVertical / g
    const apexHeight = BAT_HEIGHT + (vVertical * vVertical) / (2 * g)
    const tDown = Math.sqrt((2 * apexHeight) / g)
    tFlight = tUp + tDown
    maxHeight = apexHeight
  } else {
    tFlight = Math.sqrt((2 * BAT_HEIGHT) / g)
    maxHeight = BAT_HEIGHT
  }

  // Aerial distance
  const aerialDistance = vHorizontal * tFlight

  // Rolling distance after landing
  const rollingDist = calculateRollingDistance(vHorizontal, clampedVAngle)

  // Total distance = air + rolling
  const totalDistance = aerialDistance + rollingDist

  // Landing position (where ball hits ground after air travel)
  // +angle = off side, field coords: +x = leg side, +y = toward bowler (down screen)
  const landingX = -aerialDistance * Math.sin(hRad)
  const landingY = aerialDistance * Math.cos(hRad)

  // Calculate direction unit vector
  const dirMag = Math.sqrt(landingX * landingX + landingY * landingY)
  const dirX = dirMag > 0 ? landingX / dirMag : 0
  const dirY = dirMag > 0 ? landingY / dirMag : -1

  // Final position (where ball stops after rolling)
  const finalX = -totalDistance * Math.sin(hRad)
  const finalY = totalDistance * Math.cos(hRad)

  return {
    projected_distance: totalDistance,
    aerial_distance: aerialDistance,
    rolling_distance: rollingDist,
    max_height: maxHeight,
    landing_x: landingX,
    landing_y: landingY,
    final_x: finalX,
    final_y: finalY,
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
  const g = GRAVITY
  // Horizontal position: constant velocity
  const horizontalDist = trajectory.horizontal_speed * time
  const x = horizontalDist * trajectory.direction_x
  const y = horizontalDist * trajectory.direction_y
  // Vertical position: projectile motion from bat height
  const z = BAT_HEIGHT + trajectory.vertical_speed * time - 0.5 * g * time * time
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
  const angle = normalizeAngle(horizontalAngle)

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

  // Simple check: if ball is going to one side (off/leg) and fielder is
  // clearly on the opposite side, exclude them
  // Allow fielders within 8m of center line to field either side
  const ballGoingOffSide = landingX < -5   // Ball going to off side (negative X)
  const ballGoingLegSide = landingX > 5    // Ball going to leg side (positive X)
  const fielderOnLegSide = fielderX > 8    // Fielder clearly on leg side
  const fielderOnOffSide = fielderX < -8   // Fielder clearly on off side

  // Exclude fielders on the wrong side of the field
  if (ballGoingOffSide && fielderOnLegSide) return false
  if (ballGoingLegSide && fielderOnOffSide) return false

  const shotDirX = landingX / shotLength
  const shotDirY = landingY / shotLength
  const dot = fielderX * shotDirX + fielderY * shotDirY
  const fielderDistance = Math.sqrt(fielderX ** 2 + fielderY ** 2)

  // Calculate perpendicular distance from fielder to ball path line
  const crossProduct = fielderX * shotDirY - fielderY * shotDirX
  const perpendicularDist = Math.abs(crossProduct)

  // If fielder is more than 20m laterally from ball path, exclude them
  if (perpendicularDist > 20) {
    return false
  }

  // For forward shots, exclude distant fielders behind the batter - they
  // can't intercept a ball going away from them at speed (parity with PY)
  const ballGoingForward = landingY > 5
  if (ballGoingForward && fielderY < 0 && fielderDistance > 10) {
    return false
  }

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
  timeToIntercept: number     // seconds until ball reaches intercept point
  fielderArrivalTime: number  // seconds for fielder to reach intercept point
  arrivedBeforeLanding: boolean // did fielder get there before ball landed?
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
  const OPTIMAL_HEIGHT_MIN = CATCH_OPTIMAL_MIN
  const OPTIMAL_HEIGHT_MAX = CATCH_OPTIMAL_MAX

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
      const movementPossible = getFielderMovementDistance(movementTime) + FIELDER_DIVE_RANGE

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
    const fielderArrivalTime = getFielderTravelTime(lateralDistance)
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
      fielderArrivalTime,
      arrivedBeforeLanding: fielderArrivalTime <= trajectory.time_of_flight,
    }
  }

  const timeToIntercept = intercept.time
  const lateralDistActual = intercept.lateralDistance
  const heightAtIntercept = intercept.height

  // Time available for fielder to move (after reaction)
  const movementTime = Math.max(0, timeToIntercept - FIELDER_REACTION_TIME)

  // How far fielder can move in available time (with acceleration)
  const movementPossible = getFielderMovementDistance(movementTime) + FIELDER_DIVE_RANGE

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

  // Calculate fielder arrival time
  const runDistance = Math.max(0, lateralDistActual - FIELDER_STATIC_RANGE)
  const fielderArrivalTime = getFielderTravelTime(runDistance)

  // Did fielder arrive before ball landed?
  const arrivedBeforeLanding = fielderArrivalTime <= trajectory.time_of_flight

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
    fielderArrivalTime,
    arrivedBeforeLanding,
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
  difficulty: Difficulty,
  rand: Rand
): 'caught' | 'dropped' {
  // Base catch probability based on difficulty score
  // difficulty=0.00 → 98% catch
  // difficulty=0.25 → 85% catch
  // difficulty=0.50 → 72% catch
  // difficulty=0.75 → 59% catch
  // difficulty=1.00 → 46% catch (even hardest catches have decent chance)
  const baseCatchProb = PARAMS.catch_base_prob - PARAMS.catch_prob_slope * catchAnalysis.difficulty

  const catchProb = Math.min(0.99, baseCatchProb * CATCH_DIFFICULTY_MODIFIER[difficulty])

  return rand() < catchProb ? 'caught' : 'dropped'
}

function rollGroundFieldingOutcome(difficulty: Difficulty, collectionDifficulty: number, rand: Rand): string {
  const probs = GROUND_FIELDING_PROBS[difficulty]

  // Collection difficulty is based on how rushed the fielder was:
  // 0 = routine (arrived very early, walking to the ball)
  // 0.3 = easy (arrived early, set for the ball)
  // 0.5 = moderate (had to hustle)
  // 1.0 = hard (barely made it, diving/stretching)

  // Super easy - fielder arrived with plenty of time, routine collection
  if (collectionDifficulty < 0.15) {
    return 'stopped'  // 100% clean stop for routine collections
  }

  let stoppedProb = probs.stopped
  let misfieldNoExtraProb = probs.misfield_no_extra

  if (collectionDifficulty > 0.7) {
    // Hard collection - diving/rushing, high misfield chance
    // On medium: 85% stopped → 50% stopped, 30% misfield_no_extra, 20% misfield_extra
    stoppedProb = probs.stopped * 0.6
    misfieldNoExtraProb = 0.30
  } else if (collectionDifficulty > 0.3) {
    // Moderate - had to move quickly but under control
    // On medium: 85% stopped → 75% stopped, 15% misfield_no_extra, 10% misfield_extra
    stoppedProb = probs.stopped * 0.88
    misfieldNoExtraProb = probs.misfield_no_extra + 0.05
  }
  // else (0.15-0.3): easy collection - use base probabilities (85% stopped on medium)

  const roll = rand()
  if (roll < stoppedProb) return 'stopped'
  if (roll < stoppedProb + misfieldNoExtraProb) return 'misfield_no_extra'
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
 * Calculate distance a fielder can cover.
 * If FIELDER_ACCEL_TIME > 0, uses linear acceleration model.
 * If FIELDER_ACCEL_TIME = 0, instant max speed.
 */
function getFielderMovementDistance(movementTime: number): number {
  if (movementTime <= 0) return 0

  // Instant max speed when no acceleration time
  if (FIELDER_ACCEL_TIME <= 0) {
    return FIELDER_RUN_SPEED * movementTime
  }

  const accel = FIELDER_RUN_SPEED / FIELDER_ACCEL_TIME  // acceleration in m/s²

  if (movementTime <= FIELDER_ACCEL_TIME) {
    // Still accelerating: d = 0.5 * a * t²
    return 0.5 * accel * movementTime * movementTime
  } else {
    // Reached max speed
    const accelDist = 0.5 * accel * FIELDER_ACCEL_TIME * FIELDER_ACCEL_TIME
    const maxSpeedTime = movementTime - FIELDER_ACCEL_TIME
    return accelDist + FIELDER_RUN_SPEED * maxSpeedTime
  }
}

/**
 * Calculate time for fielder to cover a distance (inverse of getFielderMovementDistance).
 * Includes reaction time.
 */
function getFielderTravelTime(distance: number): number {
  if (distance <= 0) return FIELDER_REACTION_TIME

  // Instant max speed when no acceleration time
  if (FIELDER_ACCEL_TIME <= 0) {
    return FIELDER_REACTION_TIME + distance / FIELDER_RUN_SPEED
  }

  const accel = FIELDER_RUN_SPEED / FIELDER_ACCEL_TIME
  const accelDist = 0.5 * accel * FIELDER_ACCEL_TIME * FIELDER_ACCEL_TIME

  if (distance <= accelDist) {
    // Still in acceleration phase: d = 0.5 * a * t², so t = sqrt(2d/a)
    return FIELDER_REACTION_TIME + Math.sqrt(2 * distance / accel)
  } else {
    // Past acceleration: d = accelDist + speed * (t - accelTime)
    const remainingDist = distance - accelDist
    return FIELDER_REACTION_TIME + FIELDER_ACCEL_TIME + remainingDist / FIELDER_RUN_SPEED
  }
}

/**
 * Find the BEST (easiest) point along the ball path where a fielder can intercept.
 *
 * Scans all reachable points and returns the one with lowest collection difficulty.
 * This ensures fielders collect at their natural position rather than sprinting
 * to cut off the ball early when they could collect more comfortably later.
 *
 * Returns null if fielder cannot intercept at any point.
 */
function findBestGroundIntercept(
  fielderX: number,
  fielderY: number,
  finalX: number,
  finalY: number,
  exitSpeedKmh: number,
  aerialDistance: number,
  timeOfFlight: number,
  projectedDistance: number
): { interceptX: number; interceptY: number; interceptDistance: number; lateralDistance: number; collectionDifficulty: number; fielderTime: number; ballTime: number } | null {
  // Direction unit vector of ball path
  const pathLength = Math.sqrt(finalX * finalX + finalY * finalY)
  if (pathLength < 0.1) return null
  const dirX = finalX / pathLength
  const dirY = finalY / pathLength

  // Sample ALL points along the ball path and find the one with lowest difficulty
  const stepSize = 2.0
  let bestIntercept: { interceptX: number; interceptY: number; interceptDistance: number; lateralDistance: number; collectionDifficulty: number; fielderTime: number; ballTime: number } | null = null
  let lowestDifficulty = Infinity

  for (let dist = 5; dist <= projectedDistance; dist += stepSize) {
    const pointX = dirX * dist
    const pointY = dirY * dist

    // Calculate ball travel time to this point
    let ballTime: number
    if (dist <= aerialDistance) {
      // Ball still in air
      ballTime = timeOfFlight * (dist / aerialDistance)
    } else {
      // Ball has landed, rolling
      const rollingDist = dist - aerialDistance
      const rollingSpeed = getGroundBallSpeed(exitSpeedKmh, rollingDist)
      ballTime = timeOfFlight + (rollingDist / rollingSpeed)
    }

    // Calculate fielder travel time to this point
    const dx = pointX - fielderX
    const dy = pointY - fielderY
    const fielderDist = Math.sqrt(dx * dx + dy * dy)

    // Fielder needs reaction time + movement time
    // Solve: getFielderMovementDistance(t) = fielderDist - GROUND_FIELDING_RANGE
    // (fielder can reach within GROUND_FIELDING_RANGE of the point)
    const distToTravel = Math.max(0, fielderDist - GROUND_FIELDING_RANGE)

    // Calculate time for fielder to cover this distance
    let fielderTime: number
    if (distToTravel <= 0) {
      fielderTime = FIELDER_REACTION_TIME  // Already in range, just react
    } else if (FIELDER_ACCEL_TIME <= 0) {
      // Instant max speed: t = d / speed
      fielderTime = FIELDER_REACTION_TIME + (distToTravel / FIELDER_RUN_SPEED)
    } else {
      // Inverse of getFielderMovementDistance with acceleration
      const accel = FIELDER_RUN_SPEED / FIELDER_ACCEL_TIME
      const accelDist = 0.5 * accel * FIELDER_ACCEL_TIME * FIELDER_ACCEL_TIME

      if (distToTravel <= accelDist) {
        // Still in acceleration phase: d = 0.5 * a * t², so t = sqrt(2d/a)
        fielderTime = FIELDER_REACTION_TIME + Math.sqrt(2 * distToTravel / accel)
      } else {
        // Past acceleration: d = accelDist + speed * (t - accelTime)
        const remainingDist = distToTravel - accelDist
        fielderTime = FIELDER_REACTION_TIME + FIELDER_ACCEL_TIME + (remainingDist / FIELDER_RUN_SPEED)
      }
    }

    // Can fielder reach this point before or when ball arrives?
    if (fielderTime <= ballTime + 0.1) {  // 0.1s grace for diving/stretching
      // This is a valid intercept point
      // Collection difficulty based on how rushed the fielder is:
      // - timeRatio < 0.6: easy (arrived early, can set) → difficulty 0
      // - timeRatio 0.6-0.9: moderate → difficulty 0.3-0.7
      // - timeRatio > 0.9: hard (barely made it) → difficulty 0.8-1.0
      const timeRatio = fielderTime / ballTime
      let collectionDifficulty: number
      if (timeRatio < 0.6) {
        collectionDifficulty = 0  // Easy - fielder arrived with plenty of time
      } else if (timeRatio < 0.9) {
        collectionDifficulty = (timeRatio - 0.6) / 0.3 * 0.5  // 0 to 0.5
      } else {
        collectionDifficulty = 0.5 + (timeRatio - 0.9) / 0.2 * 0.5  // 0.5 to 1.0
      }

      collectionDifficulty = Math.min(1, collectionDifficulty)

      // Keep track of the EASIEST intercept point (lowest difficulty)
      if (collectionDifficulty < lowestDifficulty) {
        lowestDifficulty = collectionDifficulty
        bestIntercept = {
          interceptX: pointX,
          interceptY: pointY,
          interceptDistance: dist,
          lateralDistance: Math.min(fielderDist, GROUND_FIELDING_RANGE + 2.5),
          collectionDifficulty,
          fielderTime,
          ballTime,
        }
      }
    }
  }

  return bestIntercept
}

/**
 * Calculate distance from fielder to the relevant stumps.
 * Coordinate system: +Y = toward bowler
 * - Batting end stumps at (0, 0)
 * - Bowler's end stumps at (0, +PITCH_LENGTH) = (0, ~20m toward bowler)
 * Fielder throws to whichever end is closer.
 */
function getThrowDistance(fielderX: number, fielderY: number): number {
  const distToBattingEnd = Math.sqrt(fielderX * fielderX + fielderY * fielderY)
  const distToBowlerEnd = Math.sqrt(fielderX * fielderX + (fielderY - PITCH_LENGTH) * (fielderY - PITCH_LENGTH))
  return Math.max(0.1, Math.min(distToBattingEnd, distToBowlerEnd))  // floor avoids zero (PY parity)
}

/**
 * Calculate total fielding time from ball leaving bat to ball reaching stumps.
 *
 * Accounts for fielder movement during ball flight - the fielder can
 * cover ground while the ball is traveling, reducing effective lateral distance.
 */
function calculateFieldingTime(
  exitSpeedKmh: number,
  interceptDistance: number,
  lateralDistance: number,
  interceptX: number,
  interceptY: number,
  aerialDistance: number,
  timeOfFlight: number
): number {
  // Ball travel time = air time + rolling time
  // If intercept is before landing, use trajectory time (not applicable for ground fielding)
  // If intercept is after landing, add rolling time
  let ballTravelTime: number
  if (interceptDistance <= aerialDistance) {
    // Ball still in air at intercept point - use trajectory time proportionally
    ballTravelTime = timeOfFlight * (interceptDistance / aerialDistance)
  } else {
    // Ball has landed, need to roll to intercept
    const rollingDistance = interceptDistance - aerialDistance
    const rollingSpeed = getGroundBallSpeed(exitSpeedKmh, rollingDistance)
    const rollingTime = rollingDistance / rollingSpeed
    ballTravelTime = timeOfFlight + rollingTime
  }

  // Fielder can move toward intercept point during ball flight
  // Uses linear acceleration model - takes 1s to reach max speed
  const availableMovementTime = Math.max(0, ballTravelTime - FIELDER_REACTION_TIME)
  const distanceCovered = getFielderMovementDistance(availableMovementTime)

  // Effective lateral distance after accounting for movement during flight
  const effectiveLateral = Math.max(0, lateralDistance - distanceCovered)

  const collectionTime = getCollectionTime(effectiveLateral)
  // Throw from where the ball is collected, not fielder's starting position
  const throwDistance = getThrowDistance(interceptX, interceptY)
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
  const effectiveTime = isMisfield ? fieldingTime + MISFIELD_TIME_PENALTY : fieldingTime

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

/** Runs conceded on a dropped catch, by how deep the chance was. */
function calculateRunsForDropped(distance: number, rand: Rand): number {
  if (distance >= MID_FIELD_RADIUS) {
    return rand() < 0.33 ? 3 : 2
  } else if (distance >= INNER_RING_RADIUS) {
    return rand() < 0.33 ? 2 : 1
  }
  return 1
}

/** Average ball travel time treating the whole distance as ground travel
 * (the fallback-retrieval approximation, parity with PY _get_ball_travel_time). */
function getBallTravelTime(exitSpeedKmh: number, distance: number): number {
  if (distance <= 0) return 0
  return distance / getGroundBallSpeed(exitSpeedKmh, distance)
}

/**
 * Alignment of the ball path with the throw path from the intercept point
 * (1.0 = ball travelling straight at the stumps the fielder throws to).
 * Parity port of PY _calculate_alignment_score.
 */
function calculateAlignmentScore(
  interceptX: number, interceptY: number,
  landingX: number, landingY: number
): number {
  const distToBatting = Math.sqrt(interceptX * interceptX + interceptY * interceptY)
  const dyBowling = interceptY - PITCH_LENGTH
  const distToBowling = Math.sqrt(interceptX * interceptX + dyBowling * dyBowling)
  const isBattingEnd = distToBatting <= distToBowling

  const ballPathLength = Math.sqrt(landingX * landingX + landingY * landingY)
  if (ballPathLength < 0.1) return 1.0

  const ballDirX = landingX / ballPathLength
  const ballDirY = landingY / ballPathLength

  const throwDirXRaw = -interceptX
  const throwDirYRaw = isBattingEnd ? -interceptY : PITCH_LENGTH - interceptY
  const throwDirLength = Math.sqrt(throwDirXRaw * throwDirXRaw + throwDirYRaw * throwDirYRaw)
  if (throwDirLength < 0.1) return 1.0

  const alignment = -(ballDirX * (throwDirXRaw / throwDirLength) + ballDirY * (throwDirYRaw / throwDirLength))
  return Math.max(0, Math.min(1, (alignment + 1.0) / 2.0))
}

/** Combined fielding-opportunity score, parity port of PY _calculate_priority_score. */
function calculatePriorityScore(
  collectionDifficulty: number,
  alignmentScore: number,
  fieldingTime: number
): number {
  const easeScore = 1.0 - collectionDifficulty
  const timeScore = Math.max(0, Math.min(1, (8.0 - fieldingTime) / 6.0))
  const priority = 0.4 * easeScore + 0.3 * alignmentScore + 0.3 * timeScore
  return Math.max(0, Math.min(1, priority))
}

/**
 * Main simulation function.
 *
 * Pass `seed` to reproduce an outcome exactly - the same seed yields the
 * same result in the Python engine too (shared mulberry32 + shared params).
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
  difficulty: Difficulty = 'medium',
  seed?: number
): Omit<SimulationResult, 'trajectory'> & { catch_analysis?: CatchAnalysis } {
  const resolvedSeed = (seed === undefined ? Math.floor(Math.random() * 4294967296) : seed) >>> 0
  const rand = mulberry32(resolvedSeed)

  // Sanitize inputs (parity with PY _validate_and_sanitize_inputs): NaN/Inf
  // from a flaky radar reading must not flow into outcome comparisons.
  const finite = (v: number, fallback: number) => (Number.isFinite(v) ? v : fallback)
  exitSpeed = Math.max(0, Math.min(200, finite(exitSpeed, 0)))
  horizontalAngle = normalizeAngle(finite(horizontalAngle, 0))
  verticalAngle = Math.max(0, Math.min(90, finite(verticalAngle, 0)))
  landingX = finite(landingX, 0)
  landingY = finite(landingY, 0)
  projectedDistance = Math.max(0, Math.min(150, finite(projectedDistance, 0)))
  maxHeight = Math.max(0, Math.min(50, finite(maxHeight, 0)))
  if (!Number.isFinite(boundaryDistance) || boundaryDistance <= 0) boundaryDistance = 70.0

  // The boundary circle is centered on the pitch, not the batter: the real
  // distance to the rope depends on shot angle (~79m straight, ~61m behind)
  const actualBoundary = getBoundaryDistanceAtAngle(horizontalAngle, boundaryDistance)

  const result = simulateInternal(
    exitSpeed, horizontalAngle, verticalAngle, landingX, landingY,
    projectedDistance, maxHeight, fieldConfig, actualBoundary, difficulty, rand
  )
  return { ...result, boundary_distance: actualBoundary, seed: resolvedSeed }
}

function simulateInternal(
  exitSpeed: number,
  horizontalAngle: number,
  verticalAngle: number,
  landingX: number,
  landingY: number,
  projectedDistance: number,
  maxHeight: number,
  fieldConfig: FielderConfig[],
  boundaryDistance: number,  // the angle-adjusted actual boundary
  difficulty: Difficulty,
  rand: Rand
): Omit<SimulationResult, 'trajectory'> & { catch_analysis?: CatchAnalysis } {
  const isAerial = maxHeight > AERIAL_HEIGHT_THRESHOLD || verticalAngle > AERIAL_ANGLE_THRESHOLD
  const shotName = getShotDirectionName(horizontalAngle, isAerial)
  const batterX = 0
  const batterY = 0

  // Check 1: Boundary (six)
  if (projectedDistance >= boundaryDistance) {
    const heightAtBoundary = getBallHeightAtDistance(
      boundaryDistance, projectedDistance, maxHeight, verticalAngle
    )
    if (isAerial && heightAtBoundary > SIX_HEIGHT_AT_BOUNDARY) {
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
      const outcome = rollCatchOutcome(chance.analysis, difficulty, rand)

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
        // Get position where the drop happened
        const dropPos = getBallPositionAtTime(trajectory, chance.analysis.timeToIntercept)
        if (projectedDistance >= boundaryDistance) {
          const boundaryPoint = getBoundaryIntersection(landingX, landingY, boundaryDistance)
          return {
            outcome: '4',
            runs: 4,
            is_boundary: true,
            is_aerial: true,
            fielder_involved: chance.fielder,
            fielder_position: { x: chance.fielderX, y: chance.fielderY },
            fielding_position: { x: dropPos.x, y: dropPos.y },  // Where the drop happened
            end_position: boundaryPoint,
            description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, dropped at ${chance.fielder}, four!`,
            catch_analysis: chance.analysis,
          }
        }
        const runs = calculateRunsForDropped(projectedDistance, rand)
        return {
          outcome: 'dropped',
          runs,
          is_boundary: false,
          is_aerial: true,
          fielder_involved: chance.fielder,
          fielder_position: { x: chance.fielderX, y: chance.fielderY },
          fielding_position: { x: dropPos.x, y: dropPos.y },  // Where the drop happened
          end_position: { x: landingX, y: landingY },  // Lands where it would have
          description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, dropped at ${chance.fielder}, runs ${runs}`,
          catch_analysis: chance.analysis,
        }
      }
    }
  }

  // Check 3: Ground fielding (including potential boundary balls)
  // For boundary balls, fielders must intercept BEFORE the boundary
  const isBoundaryBall = projectedDistance >= boundaryDistance
  const maxInterceptDistance = isBoundaryBall ? boundaryDistance : projectedDistance

  // Find best intercept point for each fielder
  const groundChances: Array<{
    fielder: string
    fielderX: number
    fielderY: number
    lateralDistance: number
    interceptDistance: number
    fielderDistance: number
    interceptX: number  // Where fielder collects the ball
    interceptY: number
    collectionDifficulty: number  // 0 = easy (arrived early), 1 = hard (barely made it)
    alignmentScore: number  // 0 = ball going directly at fielder, 1 = far from ball path
    priorityScore: number   // Combined weighted score (lower = higher priority)
    fielderTime: number     // Seconds for fielder to reach intercept
    ballTime: number        // Seconds for ball to reach intercept
  }> = []

  // Calculate ball path direction for alignment scoring
  const ballPathLength = Math.sqrt(landingX * landingX + landingY * landingY)
  const ballDirX = ballPathLength > 0 ? landingX / ballPathLength : 0
  const ballDirY = ballPathLength > 0 ? landingY / ballPathLength : 1

  for (const fielder of fieldConfig) {
    if (!isFielderInBallPath(fielder.x, fielder.y, landingX, landingY)) continue

    const fielderDist = distanceFromBatter(fielder.x, fielder.y)

    // Find best point where this fielder can intercept the ball
    // For boundary balls, only look for intercepts before the boundary
    const intercept = findBestGroundIntercept(
      fielder.x,
      fielder.y,
      landingX,
      landingY,
      exitSpeed,
      trajectory.aerial_distance,
      trajectory.time_of_flight,
      maxInterceptDistance  // Use boundary as limit for potential fours
    )

    if (intercept) {
      // Calculate alignment: perpendicular distance from fielder to ball path
      // Cross product gives signed distance, we use absolute value
      const crossProduct = fielder.x * ballDirY - fielder.y * ballDirX
      const perpendicularDist = Math.abs(crossProduct)
      // Normalize to 0-1 range (0 = directly on path, 1 = 30m+ away from path)
      const alignmentScore = Math.min(1, perpendicularDist / 30)

      // Weighted priority score:
      // - 0.5: alignment (is ball going toward them?)
      // - 0.25: collection difficulty (how easy is the stop?)
      // - 0.25: normalized intercept distance (closer intercept = can return faster)
      const normalizedIntercept = Math.min(1, intercept.interceptDistance / projectedDistance)
      const priorityScore =
        0.5 * alignmentScore +
        0.25 * intercept.collectionDifficulty +
        0.25 * normalizedIntercept

      groundChances.push({
        fielder: fielder.name,
        fielderX: fielder.x,
        fielderY: fielder.y,
        lateralDistance: intercept.lateralDistance,
        interceptDistance: intercept.interceptDistance,
        fielderDistance: fielderDist,
        interceptX: intercept.interceptX,
        interceptY: intercept.interceptY,
        collectionDifficulty: intercept.collectionDifficulty,
        alignmentScore,
        priorityScore,
        fielderTime: intercept.fielderTime,
        ballTime: intercept.ballTime,
      })
    }
  }

  // Sort by priority score - weighted combination of alignment, difficulty, and speed
  // Lower score = higher priority (ball going at them + easy collection + quick return)
  groundChances.sort((a, b) => a.priorityScore - b.priorityScore)

  for (const chance of groundChances) {
    const outcome = rollGroundFieldingOutcome(difficulty, chance.collectionDifficulty, rand)

    // Calculate time-based runs using physics model
    const fieldingTime = calculateFieldingTime(
      exitSpeed,
      chance.interceptDistance,
      chance.lateralDistance,
      chance.interceptX,
      chance.interceptY,
      trajectory.aerial_distance,
      trajectory.time_of_flight
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
          fielding_position: { x: chance.interceptX, y: chance.interceptY },
          end_position: { x: chance.interceptX, y: chance.interceptY },
          description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)} fielded by ${chance.fielder}, no run`,
          fielding_time: fieldingTime,
          collection_difficulty: chance.collectionDifficulty,
          alignment_score: chance.alignmentScore,
          priority_score: chance.priorityScore,
          fielder_arrival_time: chance.fielderTime,
          ball_arrival_time: chance.ballTime,
        }
      }
      return {
        outcome: String(runs),
        runs,
        is_boundary: false,
        is_aerial: isAerial,
        fielder_involved: chance.fielder,
        fielder_position: { x: chance.fielderX, y: chance.fielderY },
        fielding_position: { x: chance.interceptX, y: chance.interceptY },
        end_position: { x: chance.interceptX, y: chance.interceptY },
        description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, ${chance.fielder} fields, ${runs} run${runs > 1 ? 's' : ''}`,
        fielding_time: fieldingTime,
        collection_difficulty: chance.collectionDifficulty,
        alignment_score: chance.alignmentScore,
        priority_score: chance.priorityScore,
        fielder_arrival_time: chance.fielderTime,
        ball_arrival_time: chance.ballTime,
      }
    } else if (outcome === 'misfield_no_extra') {
      // Fumbled but recovered - slight delay, ball stays near fielder
      const runs = Math.max(1, calculateRunsFromFieldingTime(fieldingTime + FUMBLE_TIME_PENALTY, false))
      return {
        outcome: 'misfield',
        runs,
        is_boundary: false,
        is_aerial: isAerial,
        fielder_involved: chance.fielder,
        fielder_position: { x: chance.fielderX, y: chance.fielderY },
        fielding_position: { x: chance.interceptX, y: chance.interceptY },
        end_position: { x: chance.interceptX, y: chance.interceptY },
        description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, misfield by ${chance.fielder}, ${runs} run${runs > 1 ? 's' : ''}`,
        fielding_time: fieldingTime + FUMBLE_TIME_PENALTY,
        collection_difficulty: chance.collectionDifficulty,
        alignment_score: chance.alignmentScore,
        priority_score: chance.priorityScore,
        fielder_arrival_time: chance.fielderTime,
        ball_arrival_time: chance.ballTime,
      }
    } else {
      // Ball gets past fielder
      // If it was a boundary ball, misfield = four
      if (isBoundaryBall) {
        const boundaryPoint = getBoundaryIntersection(landingX, landingY, boundaryDistance)
        return {
          outcome: '4',
          runs: 4,
          is_boundary: true,
          is_aerial: isAerial,
          fielder_involved: chance.fielder,
          fielder_position: { x: chance.fielderX, y: chance.fielderY },
          fielding_position: { x: chance.interceptX, y: chance.interceptY },
          end_position: boundaryPoint,
          description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, misfield by ${chance.fielder}, four!`,
          fielding_time: fieldingTime,  // penalty applies to runs, not the reported time (PY parity)
          collection_difficulty: chance.collectionDifficulty,
          alignment_score: chance.alignmentScore,
          priority_score: chance.priorityScore,
          fielder_arrival_time: chance.fielderTime,
          ball_arrival_time: chance.ballTime,
        }
      }
      // Non-boundary ball - they must chase and throw from further back
      const runs = calculateRunsFromFieldingTime(fieldingTime, true)
      return {
        outcome: 'misfield',
        runs,
        is_boundary: false,
        is_aerial: isAerial,
        fielder_involved: chance.fielder,
        fielder_position: { x: chance.fielderX, y: chance.fielderY },
        fielding_position: { x: chance.interceptX, y: chance.interceptY },
        end_position: { x: landingX, y: landingY },
        description: `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, misfield by ${chance.fielder}, ${runs} run${runs > 1 ? 's' : ''}`,
        fielding_time: fieldingTime,  // penalty applies to runs, not the reported time (PY parity)
        collection_difficulty: chance.collectionDifficulty,
        alignment_score: chance.alignmentScore,
        priority_score: chance.priorityScore,
        fielder_arrival_time: chance.fielderTime,
        ball_arrival_time: chance.ballTime,
      }
    }
  }

  // No fielder intercepted - if it was a boundary ball, it's a four
  if (isBoundaryBall) {
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

  // Non-boundary ball with no fielder in path - nearest fielder retrieves.
  // Parity port of PY _fallback_nearest_fielder: filter to plausible
  // retrievers, target the ball's FINAL (rolled) position, exact chase-time
  // model, throw from where the ball stopped.
  if (fieldConfig.length === 0) {
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

  // Prefer fielders on the correct side AND in the ball's direction
  const ballGoingOffSide = landingX < -5
  const ballGoingLegSide = landingX > 5
  const ballGoingForward = landingY > 5
  const sameSideFielders = fieldConfig.filter((f) => {
    if (ballGoingOffSide && f.x > 8) return false
    if (ballGoingLegSide && f.x < -8) return false
    if (ballGoingForward && f.y < 0 && distanceFromBatter(f.x, f.y) > 10) return false
    return true
  })
  const candidates = sameSideFielders.length > 0 ? sameSideFielders : fieldConfig

  // Final position where the ball stops (rolls beyond the landing point)
  const landingDist = distanceFromBatter(landingX, landingY)
  let finalX = landingX
  let finalY = landingY
  if (landingDist > 0.1) {
    finalX = (landingX / landingDist) * projectedDistance
    finalY = (landingY / landingDist) * projectedDistance
  }

  // Nearest candidate to the final position
  let nearest = candidates[0]
  let nearestDist = Math.hypot(nearest.x - finalX, nearest.y - finalY)
  for (const f of candidates) {
    const d = Math.hypot(f.x - finalX, f.y - finalY)
    if (d < nearestDist) {
      nearest = f
      nearestDist = d
    }
  }

  // Time model: fielder runs during ball travel, then chases the remainder
  const ballTime = getBallTravelTime(exitSpeed, projectedDistance)
  const availableRunTime = Math.max(0, ballTime - FIELDER_REACTION_TIME)
  const covered = getFielderMovementDistance(availableRunTime)
  const remaining = Math.max(0, nearestDist - covered)
  const additionalRun = remaining > 0 ? getFielderTravelTime(remaining) - FIELDER_REACTION_TIME : 0
  const fielderArrivalTime = ballTime + additionalRun

  // Throw from the final position (where the ball stopped)
  const throwDist = getThrowDistance(finalX, finalY)
  const throwTime = throwDist / THROW_SPEED

  const totalTime = ballTime + additionalRun + PICKUP_TIME_STOPPED + throwTime
  const runs = calculateRunsFromFieldingTime(totalTime, false)

  // Collection difficulty: a retrieval is always a chase
  let collectionDifficulty: number
  if (ballTime > 0) {
    const timeRatio = fielderArrivalTime / ballTime
    if (timeRatio < 1.2) collectionDifficulty = 0.3       // arrived soon after ball
    else if (timeRatio < 1.8) collectionDifficulty = 0.5  // had to chase a bit
    else collectionDifficulty = 0.7                       // long chase
  } else {
    collectionDifficulty = 0.5
  }

  const alignmentScore = calculateAlignmentScore(finalX, finalY, finalX, finalY)
  const priorityScore = calculatePriorityScore(collectionDifficulty, alignmentScore, totalTime)

  return {
    outcome: runs > 0 ? String(runs) : 'dot',
    runs,
    is_boundary: false,
    is_aerial: isAerial,
    fielder_involved: nearest.name,
    fielder_position: { x: nearest.x, y: nearest.y },
    fielding_position: { x: finalX, y: finalY },
    end_position: { x: finalX, y: finalY },
    description: runs > 0
      ? `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, ${nearest.name} retrieves, ${runs} run${runs > 1 ? 's' : ''}`
      : `${shotName.charAt(0).toUpperCase() + shotName.slice(1)}, ${nearest.name} collects, no run`,
    fielding_time: totalTime,
    collection_difficulty: collectionDifficulty,
    alignment_score: alignmentScore,
    priority_score: priorityScore,
    fielder_arrival_time: fielderArrivalTime,
    ball_arrival_time: ballTime,
  }
}
