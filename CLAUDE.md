# Cricket App - Claude Code Instructions

## Deployment Reminder

**IMPORTANT**: This app is deployed via Vercel from GitHub. Changes are NOT visible in the UI until:

1. Code changes are committed to git
2. Changes are pushed to GitHub (`git push origin master`)
3. Vercel automatically deploys (takes ~1-2 minutes)

After making changes to `src/gameEngine.ts` or any other file, always:
```bash
npm run build  # Verify no TypeScript errors
git add -A && git commit -m "description" && git push origin master
```

## Project Structure

- `src/gameEngine.ts` - TypeScript game engine (runs in browser)
- `src/App.tsx` - React UI components
- `src/App.css` - Styling
- `src/fieldZones.ts` - Fielder position and zone calculations
- `engine/game_engine.py` - Python game engine (reference implementation)

## Coordinate System

- Batter at origin (0, 0)
- **+Y = toward bowler** (down the pitch)
- **+X = leg side** (for right-handed batter)
- **-X = off side**
- Bowler's end stumps at (0, +20.12) - PITCH_LENGTH meters toward bowler
- Boundary at 70m radius

## Key Constants

```typescript
// Timing
TIME_FOR_FIRST_RUN = 3.5      // seconds for first run
TIME_FOR_EXTRA_RUN = 2.5      // seconds for each additional run
FIELDER_REACTION_TIME = 0.25  // seconds before fielder starts moving
FIELDER_ACCEL_TIME = 0.5      // seconds to reach max speed

// Physics
GROUND_FRICTION = 0.05        // exponential decay factor for rolling
THROW_SPEED = 30.0            // m/s (~108 km/h)
FIELDER_RUN_SPEED = 6.0       // m/s (~21.6 km/h)

// Fielding ranges
GROUND_FIELDING_RANGE = 3.0   // metres static reach for ground balls
FIELDER_STATIC_RANGE = 1.5    // metres catch without moving
FIELDER_DIVE_RANGE = 1.0      // metres diving catch/stop reach

// Collection times
COLLECTION_TIME_DIRECT = 0.5  // ball straight to fielder
COLLECTION_TIME_MOVING = 1.0  // fielder moves to collect
COLLECTION_TIME_DIVING = 1.5  // diving stop, recover, release
PICKUP_TIME_STOPPED = 0.4     // picking up stationary ball
```

---

## Ball Trajectory Physics

### Aerial Phase
Ball leaves bat at 1m height with horizontal and vertical velocity components:
```
vHorizontal = speed * cos(verticalAngle)
vVertical = speed * sin(verticalAngle)
```

Flight time calculated from projectile motion:
- Time up to apex: `tUp = vVertical / g`
- Apex height: `1 + vVertical² / (2g)`
- Time down from apex: `tDown = sqrt(2 * apexHeight / g)`
- Total flight time: `tFlight = tUp + tDown`

Aerial distance: `aerialDistance = vHorizontal * tFlight`

### Rolling Phase
After landing, ball rolls with exponential speed decay:
```
v = v0 * e^(-GROUND_FRICTION * distance)
```

Landing speed retention depends on impact angle (steeper = more energy lost):
```
impactRetention = 0.85 - 0.8 * sin(verticalAngle)
landingSpeed = horizontalSpeed * impactRetention
```

Rolling distance (ball stops at ~1.5 m/s):
```
rollingDistance = ln(landingSpeed / 1.5) / GROUND_FRICTION
```

**Total distance = aerial distance + rolling distance**

---

## Fielder Movement Model

Fielders accelerate linearly over 0.5s to max speed:

**During acceleration (t ≤ 0.5s):**
```
distance = 0.5 * (FIELDER_RUN_SPEED / 0.5) * t² = 6 * t²
```

**At max speed (t > 0.5s):**
```
distance = 1.5m + 6 m/s * (t - 0.5)
```

After 0.25s reaction time, fielders accelerate to 6 m/s over 0.5s.

---

## Fielder Selection (Weighted Priority Scoring)

When multiple fielders can reach the ball, they're ranked by weighted priority score:

| Weight | Factor | Description |
|--------|--------|-------------|
| **50%** | Alignment | Perpendicular distance from fielder to ball path (0 = directly in line) |
| **25%** | Collection Difficulty | How rushed the fielder is (time ratio) |
| **25%** | Intercept Distance | Normalized distance to intercept point |

**Priority Score** (lower = higher priority):
```
alignmentScore = min(1, perpendicularDistance / 30)
normalizedIntercept = min(1, interceptDistance / projectedDistance)
priorityScore = 0.5 * alignmentScore + 0.25 * collectionDifficulty + 0.25 * normalizedIntercept
```

### Side Exclusion
Fielders on the wrong side of the pitch are excluded:
- Ball going to off side (X < -5) → exclude fielders on leg side (X > 8)
- Ball going to leg side (X > 5) → exclude fielders on off side (X < -8)

---

## Collection Difficulty

Based on time ratio (fielder arrival time / ball arrival time):

| Time Ratio | Difficulty | Description |
|------------|------------|-------------|
| < 0.6 | 0.0 | Routine - fielder arrived early, walking to ball |
| 0.6 - 0.9 | 0.0 - 0.5 | Moderate - had to hustle |
| > 0.9 | 0.5 - 1.0 | Hard - barely made it, diving/stretching |

---

## Ground Fielding Outcomes

Probability of outcomes based on collection difficulty:

### Routine Collection (difficulty < 0.15)
- **100% stopped** - no chance of misfield

### Easy Collection (difficulty 0.15 - 0.3)
Uses base difficulty probabilities (medium: 85% stopped)

### Moderate Collection (difficulty 0.3 - 0.7)
- Stopped probability: base × 0.88
- Misfield (no extra): base + 5%

### Hard Collection (difficulty > 0.7)
- Stopped probability: base × 0.6
- Misfield (no extra): 30%
- Misfield (extra runs): remaining probability

---

## Run Calculation

Total fielding time = ball travel + collection + throw

### Ball Travel Time
```
If intercept before landing:
  ballTime = timeOfFlight * (interceptDistance / aerialDistance)
Else:
  rollingTime = rollingDistance / groundBallSpeed
  ballTime = timeOfFlight + rollingTime
```

### Collection Time
- Direct (lateral < 0.5m): 0.5s
- Moving (lateral < 2.0m): 1.0s
- Diving (lateral ≥ 2.0m): 1.5s

### Throw Time
```
throwDistance = min(distToBattingEnd, distToBowlerEnd)
throwTime = throwDistance / 30 m/s
```

### Runs Awarded
```
If fieldingTime < 3.5s → 0 runs (dot ball)
If fieldingTime ≥ 3.5s → 1 run
Each additional 2.5s → +1 run
Max 3 runs (then boundary)
```

### Misfield Adjustments
- **Misfield (no extra)**: +1.0s to fielding time
- **Misfield (ball gets past)**: +2.0s to fielding time

---

## Catch Analysis

Catches use a multi-factor difficulty score:

| Weight | Factor | Description |
|--------|--------|-------------|
| 25% | Reaction | Time pressure (0.5s or less = hard) |
| 35% | Movement | Distance fielder must cover |
| 20% | Height | Awkwardness of catch height (optimal: 1.0-1.8m) |
| 20% | Speed | Ball speed at fielder |

### Catch Types
- **Regulation** (difficulty < 0.25): Standard catch
- **Hard** (difficulty 0.25 - 0.6): Good catch required
- **Spectacular** (difficulty > 0.6): Outstanding effort

### Catch Probability
```
baseCatchProb = 0.98 - 0.52 * difficulty
```
Modified by game difficulty setting (easy: ×0.85, medium: ×1.0, hard: ×1.10)

### Height Optimization
Fielders run to the BEST catchable position along the trajectory:
- If optimal height (1.0-1.8m) is reachable, no height penalty
- Only penalize height if fielder was rushed and couldn't reach optimal position

---

## Boundary Logic

- **Six**: Ball clears boundary while aerial (height > 0.5m at boundary)
- **Four**: Ball reaches boundary along ground (if no fielder intercepts)

### Ground Fielding on Boundary Balls
For shots projected to travel >= 70m:
1. Fielders can intercept BEFORE the boundary (< 70m)
2. If stopped cleanly → runs based on fielding time (usually 2-3)
3. If misfield (ball gets past) → automatic four
4. If no fielder can intercept → four

Boundary intersection calculated by scaling landing point direction to 70m radius.

---

## Debug Output

Browser console logs comprehensive shot data:
```javascript
{
  input: { speed, angle, elevation },
  trajectory: { aerial_distance, rolling_distance, total_distance, flight_time, max_height },
  fielding: {
    outcome, runs, fielder, fielder_start, intercept_pos,
    fielding_time, collection_difficulty, alignment_score, priority_score
  },
  description
}
```
