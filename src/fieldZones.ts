/**
 * Field Zone System - Voronoi-based
 *
 * Uses nearest-neighbor to seed positions to determine fielding zones.
 * Coordinate system: batter at (0,0), +y toward bowler, +x toward leg side (right-hander)
 */

// Field dimensions in meters
const FIELD_DIAMETER = 140
const FIELD_RADIUS = 70
// Field center is offset from batter - batter is ~8.85m below field center
const FIELD_CENTER_Y = 8.85

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

// Batter position on screen (percentage coordinates)
// Batter is at TOP of pitch, above field center
// Field center = 50%, batter offset = -8.84m = -6.3% of field diameter
const BATTER_SCREEN_X = 50
const BATTER_SCREEN_Y = 36

/**
 * Convert screen coordinates (0-100%) to field coordinates (meters from batter)
 * Screen: Y increases downward. Field: Y increases toward bowler (also down).
 */
function screenToField(screenX: number, screenY: number): { x: number; y: number } {
  const scale = FIELD_DIAMETER / 100
  return {
    x: (screenX - BATTER_SCREEN_X) * scale,
    y: (screenY - BATTER_SCREEN_Y) * scale
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
  const distFromCenter = Math.sqrt(x * x + (y - FIELD_CENTER_Y) * (y - FIELD_CENTER_Y))
  if (distFromCenter > FIELD_RADIUS) return null

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
  // Batter at (50, 34). Behind batter = smaller Y, toward bowler = larger Y
  return [
    { id: '1', x: 50, y: 28 },   // Keeper (behind batter)
    { id: '2', x: 47, y: 27 },   // 1st slip
    { id: '3', x: 44, y: 27 },   // 2nd slip
    { id: '4', x: 37, y: 29 },   // Gully
    { id: '5', x: 30, y: 34 },   // Point
    { id: '6', x: 30, y: 50 },   // Cover (toward bowler)
    { id: '7', x: 41, y: 60 },   // Mid-off
    { id: '8', x: 60, y: 50 },   // Mid-wicket
    { id: '9', x: 70, y: 34 },   // Square leg
    { id: '10', x: 62, y: 18 },  // Fine leg (behind batter)
  ]
}

export const FIELD_PRESET_POSITIONS: Record<string, Array<{ id: string; x: number; y: number }>> = {
  'Standard Pace': [
    { id: '1', x: 50, y: 28 },   // Keeper (behind batter)
    { id: '2', x: 47, y: 27 },   // 1st slip
    { id: '3', x: 44, y: 27 },   // 2nd slip
    { id: '4', x: 37, y: 29 },   // Gully
    { id: '5', x: 30, y: 34 },   // Point
    { id: '6', x: 30, y: 50 },   // Cover
    { id: '7', x: 41, y: 60 },   // Mid-off
    { id: '8', x: 60, y: 50 },   // Mid-wicket
    { id: '9', x: 70, y: 34 },   // Square leg
    { id: '10', x: 62, y: 18 },  // Fine leg
  ],
  'Spin Attack': [
    { id: '1', x: 50, y: 30 },   // Keeper (closer)
    { id: '2', x: 47, y: 29 },   // Slip
    { id: '3', x: 55, y: 35 },   // Short leg
    { id: '4', x: 45, y: 37 },   // Silly point
    { id: '5', x: 54, y: 37 },   // Silly mid-on
    { id: '6', x: 15, y: 50 },   // Deep cover
    { id: '7', x: 45, y: 85 },   // Long off
    { id: '8', x: 75, y: 50 },   // Deep mid-wicket
    { id: '9', x: 85, y: 34 },   // Deep square
    { id: '10', x: 60, y: 85 },  // Long on
  ],
  'T20 Death': [
    { id: '1', x: 50, y: 28 },   // Keeper
    { id: '2', x: 60, y: 90 },   // Long on
    { id: '3', x: 40, y: 90 },   // Long off
    { id: '4', x: 88, y: 34 },   // Deep square
    { id: '5', x: 35, y: 50 },   // Cover
    { id: '6', x: 12, y: 34 },   // Deep point
    { id: '7', x: 70, y: 10 },   // Fine leg
    { id: '8', x: 85, y: 20 },   // Deep backward square
    { id: '9', x: 20, y: 55 },   // Deep cover
    { id: '10', x: 65, y: 50 },  // Mid-wicket
  ],
  'Defensive': [
    { id: '1', x: 50, y: 28 },   // Keeper
    { id: '2', x: 62, y: 88 },   // Long on
    { id: '3', x: 38, y: 88 },   // Long off
    { id: '4', x: 90, y: 34 },   // Deep square leg
    { id: '5', x: 10, y: 34 },   // Deep point
    { id: '6', x: 68, y: 8 },    // Fine leg
    { id: '7', x: 32, y: 8 },    // Third man
    { id: '8', x: 15, y: 58 },   // Deep cover
    { id: '9', x: 12, y: 50 },   // Deep extra cover
    { id: '10', x: 78, y: 52 },  // Deep mid-wicket
  ],
}
