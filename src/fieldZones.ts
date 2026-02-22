/**
 * Field Zone System - Voronoi-based
 *
 * Uses nearest-neighbor to seed positions to determine fielding zones.
 * Coordinate system: batter at (0,0), +y toward bowler, +x toward leg side (right-hander)
 *
 * VISUAL UI ALIGNMENT:
 * The .cricket-field div is a circle (border-radius: 50%) with:
 * - Center at screen (50%, 50%)
 * - Radius of 50% (fills the container)
 * - White border = boundary rope
 *
 * FIELD GEOMETRY (in meters, from batter at origin):
 * - Batter: (0, 0)
 * - Bowler: (0, 19)
 * - Field center: (0, 8.85) - batter is NOT at center of field
 * - Field radius: 70m
 *
 * SCREEN MAPPING:
 * - Visual circle center: (50%, 50%)
 * - Visual circle radius: 50%
 * - Scale: 50% screen = 70m, so 1% = 1.4m
 * - Batter is 8.85m from center, so 8.85/70*50 = 6.3% above center
 * - Therefore batter is at (50%, 43.7%)
 */

// ============================================
// VISUAL UI CONSTANTS (aligned to CSS)
// ============================================
// The visual field circle as rendered by CSS
const SCREEN_FIELD_CENTER_X = 50  // Center of .cricket-field circle
const SCREEN_FIELD_CENTER_Y = 50  // Center of .cricket-field circle
const SCREEN_FIELD_RADIUS = 50    // The circle fills 0-100%

// ============================================
// REAL WORLD DIMENSIONS (in meters)
// ============================================
const FIELD_RADIUS_METERS = 70
const FIELD_CENTER_OFFSET_FROM_BATTER = 8.85  // Field center is 8.85m toward bowler from batter

// Scale: 1% screen = how many meters
const METERS_PER_PERCENT = FIELD_RADIUS_METERS / SCREEN_FIELD_RADIUS  // 70/50 = 1.4

// ============================================
// DERIVED BATTER POSITION
// ============================================
// Batter is 8.85m from field center (toward the back/top of screen)
// In screen terms: 8.85m / 1.4 (m/%) = 6.32%
const BATTER_OFFSET_FROM_CENTER = FIELD_CENTER_OFFSET_FROM_BATTER / METERS_PER_PERCENT
const BATTER_SCREEN_X = SCREEN_FIELD_CENTER_X
const BATTER_SCREEN_Y = SCREEN_FIELD_CENTER_Y - BATTER_OFFSET_FROM_CENTER  // 50 - 6.32 = 43.68%

// ============================================
// PITCH DIMENSIONS
// ============================================
const PITCH_LENGTH_METERS = 20.12  // Standard cricket pitch length
const PITCH_VISUAL_SCALE = 1.5     // Scale up length for visibility
const PITCH_LENGTH_SCREEN = (PITCH_LENGTH_METERS / METERS_PER_PERCENT) * PITCH_VISUAL_SCALE  // ~21.6%
const PITCH_WIDTH_SCREEN = 6       // Fixed width for visibility

// Exported geometry for UI components
export const SCREEN_GEOMETRY = {
  // Visual field circle (matches CSS)
  fieldCenterX: SCREEN_FIELD_CENTER_X,
  fieldCenterY: SCREEN_FIELD_CENTER_Y,
  fieldRadius: SCREEN_FIELD_RADIUS,

  // Batter position (derived from field geometry)
  batterX: BATTER_SCREEN_X,
  batterY: BATTER_SCREEN_Y,

  // Pitch (centered on field)
  pitchCenterX: SCREEN_FIELD_CENTER_X,
  pitchCenterY: SCREEN_FIELD_CENTER_Y,
  pitchWidth: PITCH_WIDTH_SCREEN,
  pitchLength: PITCH_LENGTH_SCREEN,

  // Scale factor
  scale: METERS_PER_PERCENT,
}

// Seed positions for each fielding position (x, y in meters from batter)
// x: negative = off side, positive = leg side
// y: negative = behind batter, positive = toward bowler
const ZONE_SEEDS: Array<{ name: string; x: number; y: number }> = [
  { name: "Point", x: -27.6, y: -0.1 },
  { name: "Wicketkeeper", x: 0, y: -7.5 },
  { name: "First Slip", x: -3.8, y: -8.5 },
  { name: "Second Slip", x: -7.2, y: -8.1 },
  { name: "Third Slip", x: -10.8, y: -6.6 },
  { name: "Leg Slip", x: 5.7, y: -12.8 },
  { name: "Gully", x: -17.4, y: -7.5 },
  { name: "Backward Point", x: -27.4, y: -6.2 },
  { name: "Forward Point", x: -27.8, y: 5.9 },
  { name: "Deep Backward Point", x: -61.7, y: -16 },
  { name: "Deep Point", x: -64.1, y: -0.3 },
  { name: "Cover", x: -26.1, y: 19.9 },
  { name: "Cover Point", x: -27.4, y: 12 },
  { name: "Extra Cover", x: -21.2, y: 26.5 },
  { name: "Deep Cover Point", x: -64.3, y: 20.3 },
  { name: "Deep Extra Cover", x: -50.1, y: 51.7 },
  { name: "Deep Cover", x: -59, y: 38.1 },
  { name: "Mid-Off", x: -12.3, y: 29.4 },
  { name: "Short Cover", x: -12.3, y: 10.5 },
  { name: "Short Mid-Off", x: -7.4, y: 18.4 },
  { name: "Silly Point", x: -7.6, y: 0.4 },
  { name: "Silly Mid-Off", x: -3.8, y: 7.4 },
  { name: "Deep Mid-Off", x: -12.9, y: 44.1 },
  { name: "Long Off", x: -30.5, y: 68.2 },
  { name: "Wide Long-Off", x: -41.1, y: 61.5 },
  { name: "Straight Long-Off", x: -15.9, y: 73.3 },
  { name: "Straight Hit", x: 0, y: 75.5 },
  { name: "Mid-On", x: 12.5, y: 29.4 },
  { name: "Short Mid-On", x: 7.4, y: 18.8 },
  { name: "Silly Mid-On", x: 3.6, y: 7.4 },
  { name: "Deep Mid-On", x: 13.4, y: 42.8 },
  { name: "Long On", x: 28.4, y: 69.1 },
  { name: "Straight Long-On", x: 14.4, y: 73.8 },
  { name: "Wide Long-On", x: 42.2, y: 60.2 },
  { name: "Mid-Wicket", x: 25, y: 19.2 },
  { name: "Deep Mid-Wicket", x: 61.1, y: 34.9 },
  { name: "Deep Forward Mid-Wicket", x: 53, y: 48.3 },
  { name: "Short Mid-Wicket", x: 11.5, y: 9.5 },
  { name: "Square Leg", x: 27.4, y: -0.1 },
  { name: "Forward Square Leg", x: 27.6, y: 6.7 },
  { name: "Backward Square Leg", x: 27.4, y: -6.6 },
  { name: "Short Leg", x: 7.4, y: -0.1 },
  { name: "Deep Square Leg", x: 66, y: -0.1 },
  { name: "Deep Forward Square Leg", x: 65.8, y: 15 },
  { name: "Deep Backward Square Leg", x: 62.2, y: -16.6 },
  { name: "Leg Gully", x: 16.3, y: -10.7 },
  { name: "Fine Leg", x: 25, y: -31.9 },
  { name: "Short Fine Leg", x: 16.6, y: -19.2 },
  { name: "Deep Fine Leg", x: 33.7, y: -46.3 },
  { name: "Square Fine Leg", x: 40.9, y: -26.2 },
  { name: "Straight Fine Leg", x: 12.3, y: -38 },
  { name: "Long Leg", x: 19.1, y: -53.9 },
  { name: "Backstop", x: 0, y: -55.9 },
  { name: "Third Man", x: -20.8, y: -31.5 },
  { name: "Short Third Man", x: -14.6, y: -18.9 },
  { name: "Deep Third Man", x: -32, y: -48.4 },
  { name: "Fine Third Man", x: -16.3, y: -49.5 },
  { name: "Square Third Man", x: -44.1, y: -30.6 },
]

// Short names for display
const SHORT_NAMES: Record<string, string> = {
  "Point": "Pt", "Wicketkeeper": "WK", "First Slip": "1Slp", "Second Slip": "2Slp",
  "Third Slip": "3Slp", "Leg Slip": "LS", "Gully": "Gly", "Backward Point": "BP",
  "Forward Point": "FP", "Deep Backward Point": "DBP", "Deep Point": "DP",
  "Cover": "Cov", "Cover Point": "CP", "Extra Cover": "XC", "Deep Cover Point": "DCP",
  "Deep Extra Cover": "DXC", "Deep Cover": "DC", "Mid-Off": "MO", "Short Cover": "SC",
  "Short Mid-Off": "SMO", "Silly Point": "SP", "Silly Mid-Off": "SMO",
  "Deep Mid-Off": "DMO", "Long Off": "LO", "Wide Long-Off": "WLO",
  "Straight Long-Off": "SLO", "Straight Hit": "Str", "Mid-On": "MN",
  "Short Mid-On": "SMN", "Silly Mid-On": "SMN", "Deep Mid-On": "DMN",
  "Long On": "LN", "Straight Long-On": "SLN", "Wide Long-On": "WLN",
  "Mid-Wicket": "MW", "Deep Mid-Wicket": "DMW", "Deep Forward Mid-Wicket": "DFMW",
  "Short Mid-Wicket": "SMW", "Square Leg": "SqL", "Forward Square Leg": "FSq",
  "Backward Square Leg": "BSq", "Short Leg": "SL", "Deep Square Leg": "DSq",
  "Deep Forward Square Leg": "DFSq", "Deep Backward Square Leg": "DBSq",
  "Leg Gully": "LG", "Fine Leg": "FL", "Short Fine Leg": "SFL",
  "Deep Fine Leg": "DFL", "Square Fine Leg": "SqFL", "Straight Fine Leg": "StFL",
  "Long Leg": "LL", "Backstop": "BS", "Third Man": "3M", "Short Third Man": "S3M",
  "Deep Third Man": "D3M", "Fine Third Man": "F3M", "Square Third Man": "Sq3M",
}

/**
 * Convert screen coordinates (0-100%) to field coordinates (meters from batter)
 * Screen: Y increases downward (0% = top of screen)
 * Field: Y increases toward bowler (positive Y = toward bowler)
 */
function screenToField(screenX: number, screenY: number): { x: number; y: number } {
  return {
    x: (screenX - BATTER_SCREEN_X) * METERS_PER_PERCENT,
    y: (screenY - BATTER_SCREEN_Y) * METERS_PER_PERCENT
  }
}

/**
 * Convert field coordinates (meters from batter) to screen coordinates (0-100%)
 */
export function fieldToScreen(fieldX: number, fieldY: number): { x: number; y: number } {
  return {
    x: BATTER_SCREEN_X + fieldX / METERS_PER_PERCENT,
    y: BATTER_SCREEN_Y + fieldY / METERS_PER_PERCENT
  }
}

/**
 * Check if a field coordinate is within the circular field boundary
 */
export function isInsideField(fieldX: number, fieldY: number): boolean {
  const distFromCenter = Math.sqrt(
    fieldX * fieldX +
    (fieldY - FIELD_CENTER_OFFSET_FROM_BATTER) * (fieldY - FIELD_CENTER_OFFSET_FROM_BATTER)
  )
  return distFromCenter <= FIELD_RADIUS_METERS
}

/**
 * Check if a screen coordinate is within the VISUAL circular field boundary
 * This checks against the actual rendered circle (centered at 50%, 50% with radius 50%)
 */
export function isInsideFieldScreen(screenX: number, screenY: number): boolean {
  const dx = screenX - SCREEN_FIELD_CENTER_X
  const dy = screenY - SCREEN_FIELD_CENTER_Y
  const dist = Math.sqrt(dx * dx + dy * dy)
  return dist <= SCREEN_FIELD_RADIUS
}

/**
 * Constrain a screen coordinate to stay inside the VISUAL circular field boundary
 * This aligns with the actual rendered circle (centered at 50%, 50% with radius 50%)
 */
export function constrainToField(screenX: number, screenY: number): { x: number; y: number } {
  const dx = screenX - SCREEN_FIELD_CENTER_X
  const dy = screenY - SCREEN_FIELD_CENTER_Y
  const dist = Math.sqrt(dx * dx + dy * dy)

  // Margin from boundary (in screen %)
  const margin = 3

  // If inside field (with margin), return as-is
  if (dist <= SCREEN_FIELD_RADIUS - margin) {
    return { x: screenX, y: screenY }
  }

  // Otherwise, project to the boundary with margin
  const ratio = (SCREEN_FIELD_RADIUS - margin) / dist
  return {
    x: SCREEN_FIELD_CENTER_X + dx * ratio,
    y: SCREEN_FIELD_CENTER_Y + dy * ratio
  }
}

/**
 * Find nearest zone seed to a field position
 */
function findNearestZone(fieldX: number, fieldY: number, isLeftHanded: boolean): string | null {
  // Mirror x-coordinate for left-hander
  const x = isLeftHanded ? -fieldX : fieldX
  const y = fieldY

  // Check if point is within field boundary (measured from field center, not batter)
  const distFromCenter = Math.sqrt(
    x * x +
    (y - FIELD_CENTER_OFFSET_FROM_BATTER) * (y - FIELD_CENTER_OFFSET_FROM_BATTER)
  )
  if (distFromCenter > FIELD_RADIUS_METERS) return null

  let nearest: string | null = null
  let minDist = Infinity

  for (const seed of ZONE_SEEDS) {
    const dx = x - seed.x
    const dy = y - seed.y
    const d = dx * dx + dy * dy
    if (d < minDist) {
      minDist = d
      nearest = seed.name
    }
  }

  return nearest
}

export interface FielderWithZone {
  id: string
  x: number
  y: number
  zoneName: string
  shortName: string
  isKeeper: boolean
}

/**
 * Calculate zones for all fielders
 */
export function calculateFielderZones(
  fielders: Array<{ id: string; x: number; y: number }>,
  isLeftHanded: boolean = false
): FielderWithZone[] {
  return fielders.map(f => {
    const field = screenToField(f.x, f.y)
    const zoneName = findNearestZone(field.x, field.y, isLeftHanded) ?? 'Fielder'
    const shortName = SHORT_NAMES[zoneName] ?? 'F'

    return {
      id: f.id,
      x: f.x,
      y: f.y,
      zoneName,
      shortName,
      isKeeper: zoneName === 'Wicketkeeper',
    }
  })
}

export function getFielderDisplayName(fielder: FielderWithZone): string {
  return fielder.shortName
}

export function createDefaultFielders(): Array<{ id: string; x: number; y: number }> {
  // Batter at (50, 44). Behind batter = smaller Y, toward bowler = larger Y
  return [
    { id: '1', x: 50, y: 38 },   // Keeper (behind batter)
    { id: '2', x: 47, y: 37 },   // 1st slip
    { id: '3', x: 44, y: 37 },   // 2nd slip
    { id: '4', x: 37, y: 39 },   // Gully
    { id: '5', x: 30, y: 44 },   // Point
    { id: '6', x: 30, y: 60 },   // Cover (toward bowler)
    { id: '7', x: 41, y: 70 },   // Mid-off
    { id: '8', x: 60, y: 60 },   // Mid-wicket
    { id: '9', x: 70, y: 44 },   // Square leg
    { id: '10', x: 62, y: 28 },  // Fine leg (behind batter)
  ]
}

export const FIELD_PRESET_POSITIONS: Record<string, Array<{ id: string; x: number; y: number }>> = {
  'Standard Pace': [
    { id: '1', x: 50, y: 38 },   // Keeper (behind batter)
    { id: '2', x: 47, y: 37 },   // 1st slip
    { id: '3', x: 44, y: 37 },   // 2nd slip
    { id: '4', x: 37, y: 39 },   // Gully
    { id: '5', x: 30, y: 44 },   // Point
    { id: '6', x: 30, y: 60 },   // Cover
    { id: '7', x: 41, y: 70 },   // Mid-off
    { id: '8', x: 60, y: 60 },   // Mid-wicket
    { id: '9', x: 70, y: 44 },   // Square leg
    { id: '10', x: 62, y: 28 },  // Fine leg
  ],
  'Spin Attack': [
    { id: '1', x: 50, y: 40 },   // Keeper (closer)
    { id: '2', x: 47, y: 39 },   // Slip
    { id: '3', x: 55, y: 45 },   // Short leg
    { id: '4', x: 45, y: 47 },   // Silly point
    { id: '5', x: 54, y: 47 },   // Silly mid-on
    { id: '6', x: 15, y: 60 },   // Deep cover
    { id: '7', x: 45, y: 95 },   // Long off
    { id: '8', x: 75, y: 60 },   // Deep mid-wicket
    { id: '9', x: 85, y: 44 },   // Deep square
    { id: '10', x: 60, y: 95 },  // Long on
  ],
  'T20 Death': [
    { id: '1', x: 50, y: 38 },   // Keeper
    { id: '2', x: 60, y: 100 },  // Long on
    { id: '3', x: 40, y: 100 },  // Long off
    { id: '4', x: 88, y: 44 },   // Deep square
    { id: '5', x: 35, y: 60 },   // Cover
    { id: '6', x: 12, y: 44 },   // Deep point
    { id: '7', x: 70, y: 20 },   // Fine leg
    { id: '8', x: 85, y: 30 },   // Deep backward square
    { id: '9', x: 20, y: 65 },   // Deep cover
    { id: '10', x: 65, y: 60 },  // Mid-wicket
  ],
  'Defensive': [
    { id: '1', x: 50, y: 38 },   // Keeper
    { id: '2', x: 62, y: 98 },   // Long on
    { id: '3', x: 38, y: 98 },   // Long off
    { id: '4', x: 90, y: 44 },   // Deep square leg
    { id: '5', x: 10, y: 44 },   // Deep point
    { id: '6', x: 68, y: 18 },   // Fine leg
    { id: '7', x: 32, y: 18 },   // Third man
    { id: '8', x: 15, y: 68 },   // Deep cover
    { id: '9', x: 12, y: 60 },   // Deep extra cover
    { id: '10', x: 78, y: 62 },  // Deep mid-wicket
  ],
}
