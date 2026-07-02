# 0002 - shotEvent + central processing (deferred)

- **Status:** Proposed (deferred — see [[0001 - Keep everything on the Pi for now]])
- **Date:** 2026-06-27

## Idea
The Pi becomes a thin sensor. On each ball it emits a `shotEvent` (kinematics, not an outcome). A central server runs the game engine, owns the database, computes stats, and serves the UI.

```
Pi: radar → detect ball → shotEvent ──► Server: engine → persist → push to UI
```

### Draft shotEvent contract
```
shotEvent {
  device_id, session_id, timestamp,
  exit_speed, horizontal_angle, vertical_angle,   // kinematics
  bowling_speed?, detection_confidence?, raw_frames?
}
```

## Why defer
The Pi-local loop isn't proven yet, and centralizing requires connectivity + sync work. Build the core first ([[0001 - Keep everything on the Pi for now]]).

## Chosen target shape: Option B (offline-first + sync)
Pi keeps recording locally and emits shotEvents to the server **when online** (store-and-forward). Server is the source of truth; sessions survive with no internet.

## Open questions (revisit at transition)
1. Does the **game engine** run only centrally, or also on the Pi for offline play?
2. "Central server" = small VPS vs laptop?
3. Multi-device: several Pis feeding one shared dataset / leaderboards?
4. Conflict/dedup model for synced shotEvents.
