/**
 * Field Zone System
 *
 * Automatically labels fielders based on their position on the cricket field.
 * Uses polar coordinates from the batter's position to determine zones.
 *
 * Coordinate system (viewed from above, batter at center, bowler below):
 * - 0° = straight toward bowler (down on screen)
 * - Angles increase clockwise
 * - 90° = off side (left for right-hander)
 * - 180° = behind batter (up on screen)
 * - 270° = leg side (right for right-hander)
 */

// Batter position in field coordinates (percentage)
const BATTER_X = 50
const BATTER_Y = 62

// Distance thresholds (as percentage of field, roughly calibrated to meters)
// The field is approximately 70m radius, displayed as ~50% from center to edge
// So 1% ≈ 1.4m
const KEEPER_DISTANCE = 12      // Within ~8m of batter (very close behind stumps)
const VERY_CLOSE_DISTANCE = 10  // ~7m - silly positions
const CLOSE_DISTANCE = 18       // ~12m - slips, short leg, gully
const MID_DISTANCE = 35         // ~25m - point, cover, mid-on, etc.
const DEEP_DISTANCE = 50        // ~35m+ - boundary riders

interface PolarPosition {
  angle: number    // Degrees, 0° = toward bowler, clockwise
  distance: number // Percentage units from batter
}

interface Zone {
  name: string
  shortName: string
  minAngle: number
  maxAngle: number
  minDistance: number
  maxDistance: number
  priority: number // Lower = higher priority (checked first)
}

// Zone definitions for right-handed batter
// Priority ensures close catching positions are checked first
const ZONES: Zone[] = [
  // === KEEPER (highest priority) ===
  { name: 'Keeper', shortName: 'WK', minAngle: 160, maxAngle: 200, minDistance: 0, maxDistance: KEEPER_DISTANCE, priority: 1 },

  // === VERY CLOSE CATCHING (silly positions) ===
  { name: 'Silly Point', shortName: 'SP', minAngle: 50, maxAngle: 100, minDistance: 0, maxDistance: VERY_CLOSE_DISTANCE, priority: 2 },
  { name: 'Silly Mid-Off', shortName: 'SMO', minAngle: 15, maxAngle: 50, minDistance: 0, maxDistance: VERY_CLOSE_DISTANCE, priority: 2 },
  { name: 'Silly Mid-On', shortName: 'SMN', minAngle: 310, maxAngle: 345, minDistance: 0, maxDistance: VERY_CLOSE_DISTANCE, priority: 2 },
  { name: 'Short Leg', shortName: 'SL', minAngle: 250, maxAngle: 310, minDistance: 0, maxDistance: VERY_CLOSE_DISTANCE, priority: 2 },
  { name: 'Fwd Short Leg', shortName: 'FSL', minAngle: 300, maxAngle: 340, minDistance: 0, maxDistance: VERY_CLOSE_DISTANCE, priority: 2 },

  // === CLOSE CATCHING (slips, gully, leg slip) ===
  { name: 'Slip', shortName: 'Slip', minAngle: 150, maxAngle: 180, minDistance: KEEPER_DISTANCE, maxDistance: CLOSE_DISTANCE, priority: 3 },
  { name: 'Leg Slip', shortName: 'LS', minAngle: 180, maxAngle: 200, minDistance: KEEPER_DISTANCE, maxDistance: CLOSE_DISTANCE, priority: 3 },
  { name: 'Gully', shortName: 'Gly', minAngle: 130, maxAngle: 150, minDistance: 0, maxDistance: CLOSE_DISTANCE, priority: 3 },

  // === OFF SIDE - BEHIND SQUARE ===
  { name: 'Third Man', shortName: '3M', minAngle: 130, maxAngle: 170, minDistance: DEEP_DISTANCE, maxDistance: 100, priority: 5 },
  { name: 'Deep Bwd Point', shortName: 'DBP', minAngle: 115, maxAngle: 140, minDistance: DEEP_DISTANCE, maxDistance: 100, priority: 5 },
  { name: 'Backward Point', shortName: 'BP', minAngle: 115, maxAngle: 140, minDistance: CLOSE_DISTANCE, maxDistance: DEEP_DISTANCE, priority: 4 },
  { name: 'Deep Point', shortName: 'DP', minAngle: 90, maxAngle: 115, minDistance: DEEP_DISTANCE, maxDistance: 100, priority: 5 },
  { name: 'Point', shortName: 'Pt', minAngle: 90, maxAngle: 120, minDistance: CLOSE_DISTANCE, maxDistance: MID_DISTANCE, priority: 4 },

  // === OFF SIDE - IN FRONT OF SQUARE ===
  { name: 'Deep Cover', shortName: 'DC', minAngle: 55, maxAngle: 80, minDistance: DEEP_DISTANCE, maxDistance: 100, priority: 5 },
  { name: 'Cover', shortName: 'Cov', minAngle: 50, maxAngle: 80, minDistance: MID_DISTANCE - 10, maxDistance: DEEP_DISTANCE, priority: 4 },
  { name: 'Short Cover', shortName: 'SC', minAngle: 50, maxAngle: 80, minDistance: CLOSE_DISTANCE, maxDistance: MID_DISTANCE - 10, priority: 4 },
  { name: 'Deep Extra Cov', shortName: 'DEC', minAngle: 35, maxAngle: 55, minDistance: DEEP_DISTANCE, maxDistance: 100, priority: 5 },
  { name: 'Extra Cover', shortName: 'XC', minAngle: 35, maxAngle: 55, minDistance: CLOSE_DISTANCE, maxDistance: DEEP_DISTANCE, priority: 4 },
  { name: 'Long Off', shortName: 'LO', minAngle: 10, maxAngle: 40, minDistance: DEEP_DISTANCE, maxDistance: 100, priority: 5 },
  { name: 'Mid-Off', shortName: 'MO', minAngle: 10, maxAngle: 40, minDistance: CLOSE_DISTANCE, maxDistance: DEEP_DISTANCE, priority: 4 },

  // === STRAIGHT ===
  { name: 'Long Off', shortName: 'LO', minAngle: 0, maxAngle: 15, minDistance: DEEP_DISTANCE, maxDistance: 100, priority: 5 },
  { name: 'Long On', shortName: 'LN', minAngle: 345, maxAngle: 360, minDistance: DEEP_DISTANCE, maxDistance: 100, priority: 5 },
  { name: 'Straight', shortName: 'Str', minAngle: 350, maxAngle: 10, minDistance: CLOSE_DISTANCE, maxDistance: DEEP_DISTANCE, priority: 4 },

  // === LEG SIDE - IN FRONT OF SQUARE ===
  { name: 'Long On', shortName: 'LN', minAngle: 320, maxAngle: 350, minDistance: DEEP_DISTANCE, maxDistance: 100, priority: 5 },
  { name: 'Mid-On', shortName: 'MN', minAngle: 320, maxAngle: 350, minDistance: CLOSE_DISTANCE, maxDistance: DEEP_DISTANCE, priority: 4 },
  { name: 'Deep Mid-Wkt', shortName: 'DMW', minAngle: 285, maxAngle: 325, minDistance: DEEP_DISTANCE, maxDistance: 100, priority: 5 },
  { name: 'Mid-Wicket', shortName: 'MW', minAngle: 285, maxAngle: 325, minDistance: CLOSE_DISTANCE, maxDistance: DEEP_DISTANCE, priority: 4 },
  { name: 'Fwd Sq Leg', shortName: 'FSq', minAngle: 265, maxAngle: 290, minDistance: CLOSE_DISTANCE, maxDistance: MID_DISTANCE, priority: 4 },

  // === LEG SIDE - BEHIND SQUARE ===
  { name: 'Deep Square', shortName: 'DSq', minAngle: 250, maxAngle: 280, minDistance: DEEP_DISTANCE, maxDistance: 100, priority: 5 },
  { name: 'Square Leg', shortName: 'SqL', minAngle: 250, maxAngle: 280, minDistance: CLOSE_DISTANCE, maxDistance: DEEP_DISTANCE, priority: 4 },
  { name: 'Deep Bwd Sq', shortName: 'DBS', minAngle: 210, maxAngle: 255, minDistance: DEEP_DISTANCE, maxDistance: 100, priority: 5 },
  { name: 'Bwd Sq Leg', shortName: 'BSq', minAngle: 210, maxAngle: 255, minDistance: CLOSE_DISTANCE, maxDistance: DEEP_DISTANCE, priority: 4 },
  { name: 'Fine Leg', shortName: 'FL', minAngle: 195, maxAngle: 225, minDistance: DEEP_DISTANCE, maxDistance: 100, priority: 5 },
  { name: 'Short Fine', shortName: 'SF', minAngle: 195, maxAngle: 225, minDistance: CLOSE_DISTANCE, maxDistance: DEEP_DISTANCE, priority: 4 },
]

/**
 * Convert field coordinates (percentage) to polar coordinates from batter.
 * @param x - X position (0-100, 0 = left, 100 = right)
 * @param y - Y position (0-100, 0 = top, 100 = bottom)
 * @param isLeftHanded - If true, mirror for left-handed batter
 */
function toPolar(x: number, y: number, isLeftHanded: boolean = false): PolarPosition {
  // Calculate relative position from batter
  let dx = x - BATTER_X  // Positive = right of batter (leg side for RH)
  const dy = y - BATTER_Y  // Positive = below batter (toward bowler)

  // Mirror for left-handed batter
  if (isLeftHanded) {
    dx = -dx
  }

  // Calculate distance
  const distance = Math.sqrt(dx * dx + dy * dy)

  // Calculate angle (0° = toward bowler, clockwise)
  // atan2(-dx, dy) gives us: 0° down, 90° left, 180° up, 270° right
  let angle = Math.atan2(-dx, dy) * (180 / Math.PI)

  // Normalize to 0-360
  if (angle < 0) angle += 360

  return { angle, distance }
}

/**
 * Check if angle is within a range, handling wraparound at 0°/360°.
 */
function isAngleInRange(angle: number, min: number, max: number): boolean {
  // Normalize all angles to 0-360
  angle = ((angle % 360) + 360) % 360
  min = ((min % 360) + 360) % 360
  max = ((max % 360) + 360) % 360

  if (min <= max) {
    return angle >= min && angle <= max
  } else {
    // Range wraps around 0° (e.g., 350° to 10°)
    return angle >= min || angle <= max
  }
}

/**
 * Find the zone for a given position.
 */
function findZone(x: number, y: number, isLeftHanded: boolean = false): Zone | null {
  const polar = toPolar(x, y, isLeftHanded)

  // Sort zones by priority
  const sortedZones = [...ZONES].sort((a, b) => a.priority - b.priority)

  for (const zone of sortedZones) {
    if (
      isAngleInRange(polar.angle, zone.minAngle, zone.maxAngle) &&
      polar.distance >= zone.minDistance &&
      polar.distance <= zone.maxDistance
    ) {
      return zone
    }
  }

  return null
}

/**
 * Fielder with position and calculated zone.
 */
export interface FielderWithZone {
  id: string
  x: number
  y: number
  zoneName: string
  shortName: string
  isKeeper: boolean
}

/**
 * Calculate zones for all fielders and handle numbering for duplicates.
 * Slips are numbered by distance from keeper position.
 * Other duplicate zones get sequential numbers.
 */
export function calculateFielderZones(
  fielders: Array<{ id: string; x: number; y: number }>,
  isLeftHanded: boolean = false
): FielderWithZone[] {
  // First pass: assign zones to all fielders
  const fieldersWithZones = fielders.map(f => {
    const zone = findZone(f.x, f.y, isLeftHanded)
    return {
      id: f.id,
      x: f.x,
      y: f.y,
      zone,
      zoneName: zone?.name ?? 'Fielder',
      shortName: zone?.shortName ?? 'F',
      isKeeper: zone?.name === 'Keeper',
    }
  })

  // Find keeper position (for slip numbering)
  const keeper = fieldersWithZones.find(f => f.isKeeper)
  const keeperX = keeper?.x ?? BATTER_X
  const keeperY = keeper?.y ?? (BATTER_Y + 10)

  // Group by zone name
  const zoneGroups: Record<string, typeof fieldersWithZones> = {}
  for (const f of fieldersWithZones) {
    if (!zoneGroups[f.zoneName]) {
      zoneGroups[f.zoneName] = []
    }
    zoneGroups[f.zoneName].push(f)
  }

  // Second pass: handle duplicates
  const result: FielderWithZone[] = []

  for (const f of fieldersWithZones) {
    const group = zoneGroups[f.zoneName]
    let finalName = f.zoneName
    let finalShort = f.shortName

    if (group.length > 1) {
      if (f.zoneName === 'Slip') {
        // Number slips by distance from keeper (closest = 1st)
        const sortedSlips = [...group].sort((a, b) => {
          const distA = Math.sqrt((a.x - keeperX) ** 2 + (a.y - keeperY) ** 2)
          const distB = Math.sqrt((b.x - keeperX) ** 2 + (b.y - keeperY) ** 2)
          return distA - distB
        })
        const slipNumber = sortedSlips.findIndex(s => s.id === f.id) + 1
        const ordinal = slipNumber === 1 ? '1st' : slipNumber === 2 ? '2nd' : slipNumber === 3 ? '3rd' : `${slipNumber}th`
        finalName = `${ordinal} Slip`
        finalShort = `${slipNumber}Slp`
      } else if (f.zoneName === 'Keeper') {
        // Only one keeper allowed - keep first, others become generic
        const idx = group.findIndex(g => g.id === f.id)
        if (idx > 0) {
          finalName = 'Fielder'
          finalShort = 'F'
        }
      } else {
        // Number other duplicates
        const idx = group.findIndex(g => g.id === f.id)
        if (idx > 0) {
          finalName = `${f.zoneName} ${idx + 1}`
          finalShort = `${f.shortName}${idx + 1}`
        }
      }
    }

    result.push({
      id: f.id,
      x: f.x,
      y: f.y,
      zoneName: finalName,
      shortName: finalShort,
      isKeeper: f.isKeeper && finalName.includes('Keeper'),
    })
  }

  return result
}

/**
 * Get a short display name for a fielder (for UI labels).
 */
export function getFielderDisplayName(fielder: FielderWithZone): string {
  return fielder.shortName
}

/**
 * Create default fielders with initial positions.
 * Returns 10 outfielders (keeper is typically one of them).
 */
export function createDefaultFielders(): Array<{ id: string; x: number; y: number }> {
  return [
    { id: '1', x: 50, y: 72 },   // Keeper position
    { id: '2', x: 58, y: 70 },   // 1st slip area
    { id: '3', x: 64, y: 67 },   // 2nd slip area
    { id: '4', x: 72, y: 58 },   // Gully area
    { id: '5', x: 75, y: 42 },   // Point area
    { id: '6', x: 78, y: 28 },   // Cover area
    { id: '7', x: 55, y: 18 },   // Mid-off area
    { id: '8', x: 32, y: 30 },   // Mid-wicket area
    { id: '9', x: 25, y: 50 },   // Square leg area
    { id: '10', x: 30, y: 78 },  // Fine leg area
  ]
}

// Field presets with just positions (zones calculated dynamically)
export const FIELD_PRESET_POSITIONS: Record<string, Array<{ id: string; x: number; y: number }>> = {
  'Standard Pace': [
    { id: '1', x: 50, y: 72 },   // Keeper
    { id: '2', x: 58, y: 70 },   // 1st slip
    { id: '3', x: 64, y: 67 },   // 2nd slip
    { id: '4', x: 72, y: 58 },   // Gully
    { id: '5', x: 75, y: 42 },   // Point
    { id: '6', x: 78, y: 28 },   // Cover
    { id: '7', x: 55, y: 18 },   // Mid-off
    { id: '8', x: 32, y: 30 },   // Mid-wicket
    { id: '9', x: 25, y: 50 },   // Square leg
    { id: '10', x: 30, y: 78 },  // Fine leg
  ],
  'Spin Attack': [
    { id: '1', x: 50, y: 70 },   // Keeper (closer)
    { id: '2', x: 56, y: 68 },   // Slip
    { id: '3', x: 45, y: 55 },   // Short leg
    { id: '4', x: 55, y: 52 },   // Silly point
    { id: '5', x: 42, y: 52 },   // Bat-pad leg
    { id: '6', x: 85, y: 30 },   // Deep cover
    { id: '7', x: 55, y: 12 },   // Long off
    { id: '8', x: 25, y: 32 },   // Deep mid-wicket
    { id: '9', x: 15, y: 55 },   // Deep square
    { id: '10', x: 35, y: 12 },  // Long on
  ],
  'T20 Death': [
    { id: '1', x: 50, y: 72 },   // Keeper
    { id: '2', x: 30, y: 10 },   // Long on
    { id: '3', x: 70, y: 10 },   // Long off
    { id: '4', x: 12, y: 45 },   // Deep square
    { id: '5', x: 60, y: 25 },   // Cover
    { id: '6', x: 88, y: 45 },   // Deep point
    { id: '7', x: 22, y: 88 },   // Fine leg
    { id: '8', x: 12, y: 70 },   // Deep backward square
    { id: '9', x: 78, y: 32 },   // Deep cover
    { id: '10', x: 38, y: 32 },  // Mid-wicket
  ],
  'Defensive': [
    { id: '1', x: 50, y: 72 },   // Keeper
    { id: '2', x: 28, y: 12 },   // Long on
    { id: '3', x: 72, y: 12 },   // Long off
    { id: '4', x: 10, y: 45 },   // Deep square leg
    { id: '5', x: 90, y: 45 },   // Deep point
    { id: '6', x: 25, y: 88 },   // Fine leg
    { id: '7', x: 75, y: 88 },   // Third man
    { id: '8', x: 85, y: 25 },   // Deep cover
    { id: '9', x: 88, y: 38 },   // Deep extra cover
    { id: '10', x: 18, y: 32 },  // Deep mid-wicket
  ],
}
