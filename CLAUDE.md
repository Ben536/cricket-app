# Cricket App - Claude Code Instructions

## Start here

**Reviewing or refactoring this codebase?** Read `vault/Review Playbook.md`
first - it is the method three full reviews have converged on. Then
`vault/architecture/Codebase Map.md` (what everything is, the invariants,
the traps) and the generated `vault/architecture/Codebase Inventory.md`.
The latest findings are in `vault/plans/2026-09 Review — Findings & Plan.md`.

**Toolchain** (this Mac has no Homebrew; `gh`, `node`, `uv` live in `~/.local`):
```bash
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
npm ci
npm run check          # EVERY gate: pytest, drift, tsc, eslint, vitest, build, parity, shellcheck
python3 tools/codebase_map.py --write   # refresh the inventory after structural changes
```
Do not start changing code until `npm run check` is green. Commit in logical
units with the gates green at each commit. The Pi runs its system python3
(3.9 on bullseye, 3.11 on bookworm) - CI tests both; keep code 3.9-compatible
(`from __future__ import annotations`, no `match`).

## Deployment

The field UI is served **by the Pi** (`cricket-ui.service`, `http://<pi>:5173`),
not by Vercel. A Vercel copy exists for convenience but, being https, can
never open the Pi's `ws://` socket - it is a demo, not the product.

To ship a change to the device: `./scripts/deploy_to_pi.sh [pi-address]`. It
builds `dist/`, rsyncs code + dist, checks Python deps, installs the systemd
units, backs up and migrates the DB, restarts the server and verifies it.
Pushing to `master` runs CI (and updates the Vercel demo).

## Project Structure

- `src/App.tsx` - React UI (state, undo stack, wagon wheel, field editor)
- `src/scoring.ts` - the scoring rules, pure and tested (extras, overs, wickets)
- `src/settings.ts` - persisted session settings (field, hand, difficulty)
- `src/gameEngine.ts` - TypeScript game engine (runs in browser)
- `src/hooks/useServerSimulation.ts` - connection lifecycle + simulate with local fallback
- `src/api/config.ts` - server discovery (same-origin host first)
- `src/fieldZones.ts` - screen<->field conversion, zone names, presets
- `engine/game_engine.py` - Python game engine (the Pi's copy; same results)
- `engine/engine_params.json` - the ONLY place a constant is tuned
- `server/` - WebSocket server, router (validation), handlers
- `radar/` - TLV parser, single-owner reader, recorder, detector, geometry, profile
- `scripts/` - deploy, systemd units, health monitor, static server, check_all
- `tools/parity/` - 3,320-shot cross-engine golden suite (CI-gated)
- `tools/codebase_map.py` - mechanical inventory + drift check (CI-gated)

## Coordinate System

- Batter at origin (0, 0)
- **+Y = toward bowler** (down the pitch)
- **+X = leg side** (for right-handed batter)
- **-X = off side**
- Bowler's end stumps at (0, +20.12) - PITCH_LENGTH meters toward bowler
- Boundary at 70m radius

## Simulation Input Limits

| Parameter | Min | Max | Description |
|-----------|-----|-----|-------------|
| `angle` | -180 | 180 | Horizontal angle (0=straight, +off, -leg) |
| `elevation` | 0 | 90 | Vertical angle (0=ground, 90=straight up) |
| `speed` | 0 | 200 | Exit speed in km/h |

## All Configurable Parameters

> **Tuning happens in `engine/engine_params.json`** — the single source of
> truth loaded by BOTH engines (`engine/game_engine.py` and
> `src/gameEngine.ts`). Never change a constant in engine source code; that is
> how the engines forked historically (`tools/codebase_map.py --check` fails
> if a param is read by only one engine). After any engine change, run the
> golden parity suite (`tools/parity/`, see its README) — 3,320 canonical
> shots over nine fields must produce identical results in both engines.
> The engine's input limits are exported as `ENGINE_LIMITS` (TS) and
> `MAX_EXIT_SPEED` etc. (Python): speed 0-200 km/h, elevation 0-90°, angle
> normalised to -180..180; non-finite inputs become 0 in both.
>
> **Randomness is seeded**: both engines use the same mulberry32 PRNG. Each
> shot carries a `seed` (echoed in the result); the same seed reproduces the
> same outcome in either engine, which is how the client's local fallback
> stays consistent with the server and how any shot can be replayed.

### Catching
| Parameter | Current | Description |
|-----------|---------|-------------|
| `CATCH_HEIGHT_MIN` | 0.2m | Below this = half-volley, uncatchable |
| `CATCH_HEIGHT_MAX` | 4.0m | Above this = uncatchable (jumping limit) |
| `FIELDER_STATIC_RANGE` | 1.5m | Catch without moving feet |
| `FIELDER_DIVE_RANGE` | 1.0m | Extra reach when diving |

### Fielder Movement
| Parameter | Current | Description |
|-----------|---------|-------------|
| `FIELDER_REACTION_TIME` | 0.25s | Delay before fielder starts moving |
| `FIELDER_RUN_SPEED` | 6.0 m/s | Max sprint speed (21.6 km/h) |
| `FIELDER_ACCEL_TIME` | 0.5s | Time to reach max speed |

### Ground Fielding
| Parameter | Current | Description |
|-----------|---------|-------------|
| `GROUND_FIELDING_RANGE` | 3.0m | Static reach for ground balls |
| `COLLECTION_TIME_DIRECT` | 0.5s | Ball straight to fielder |
| `COLLECTION_TIME_MOVING` | 1.0s | Fielder moves to collect |
| `COLLECTION_TIME_DIVING` | 1.5s | Diving stop + recover |
| `PICKUP_TIME_STOPPED` | 0.4s | Picking up stationary ball |

### Run Calculation
| Parameter | Current | Description |
|-----------|---------|-------------|
| `TIME_FOR_FIRST_RUN` | 3.5s | Time threshold for 1 run |
| `TIME_FOR_EXTRA_RUN` | 2.5s | Additional time per run |
| `THROW_SPEED` | 30.0 m/s | Throw speed (108 km/h) |
| `PITCH_LENGTH` | 20.12m | Distance between stumps |

### Ball Physics
| Parameter | Current | Description |
|-----------|---------|-------------|
| `GROUND_FRICTION` | 0.05 | Rolling deceleration factor |

### Catch Difficulty Weights (must sum to 1.0)
| Parameter | Current | Description |
|-----------|---------|-------------|
| `WEIGHT_REACTION` | 0.25 | Time pressure importance |
| `WEIGHT_MOVEMENT` | 0.35 | Running distance importance |
| `WEIGHT_HEIGHT` | 0.20 | Awkward height penalty |
| `WEIGHT_SPEED` | 0.20 | Ball speed importance |

### Difficulty Setting Probabilities
```
easy:   catch 70%/30%, ground stop 70%
medium: catch 90%/55%, ground stop 85%
hard:   catch 98%/75%, ground stop 95%
```

### Field Geometry
| Parameter | Current | Description |
|-----------|---------|-------------|
| `INNER_RING_RADIUS` | 15.0m | Inner fielding circle |
| `MID_FIELD_RADIUS` | 30.0m | Mid-field boundary |
| `boundaryDistance` | 70.0m | Boundary (passed to simulation) |

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

### Angle-dependent boundary distance
The boundary circle (nominal radius 70m) is centered on the **pitch center**,
not the batter — the batter stands `BATTER_OFFSET_FROM_CENTER` (8.84m) from
it. The actual distance from the batter to the rope therefore depends on
shot angle (ray-circle intersection):

```
actual = offset*cos(θ) + sqrt(R² − offset²·sin²(θ))
```

- Straight to the bowler (0°): ~78.8m
- Square (±90°): ~69.4m
- Behind the keeper (180°): ~61.2m

Both engines apply this everywhere a boundary matters (six check, four check,
fielder intercept limit); results carry the resolved `boundary_distance`.

- **Six**: Ball clears the (angle-adjusted) boundary while aerial (height > 0.5m at boundary)
- **Four**: Ball reaches the boundary along ground (if no fielder intercepts)

### Ground Fielding on Boundary Balls
For shots projected to travel beyond the angle-adjusted boundary:
1. Fielders can intercept BEFORE the boundary
2. If stopped cleanly → runs based on fielding time (usually 2-3)
3. If misfield (ball gets past) → automatic four
4. If no fielder can intercept → four

Boundary intersection calculated by scaling landing point direction to the boundary radius.

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
