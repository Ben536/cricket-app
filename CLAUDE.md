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
TIME_FOR_FIRST_RUN = 3.5    // seconds for first run
TIME_FOR_EXTRA_RUN = 2.5    // seconds for each additional run
GROUND_FRICTION = 0.08      // exponential decay factor
THROW_SPEED = 30.0          // m/s (~108 km/h)
FIELDER_RUN_SPEED = 7.0     // m/s
```

## Run Calculation Logic

Runs are based on total fielding time:
1. Ball travel time = air time + rolling time
2. Collection time (0.5-1.5s depending on lateral distance)
3. Throw time = throw_distance / 30 m/s

If total time < 3.5s → dot ball
If total time >= 3.5s → 1 run
Each additional 2.5s → +1 run (max 3 before boundary)
