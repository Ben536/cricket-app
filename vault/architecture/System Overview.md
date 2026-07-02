# System Overview

## Today: everything on the Pi (self-contained)

```
 Phone (browser/PWA)
   │  WebSocket :5002  +  static UI :80
   ▼
 Raspberry Pi  ── cricket-server.service (WebSocket + game engine)
   │             ── cricket-ui.service (serves React dist/)
   │             ── cricket-radar.service (configures IWR6843)
   │             ── cricket-health.service (watchdog/auto-recovery)
   ├─ SQLite  (db/cricket.db)
   └─ Radar   IWR6843ISK-ODS over USB serial (ttyUSB0 = config, ttyUSB1 = data @ 921600)
```

- The Pi **is** the system: detection (planned), simulation, persistence, and UI all on-device.
- Rationale: works anywhere (nets, parks) with no internet. See [[0001 - Keep everything on the Pi for now]].
- UI is served **from the Pi** (`~/cricket-app/dist/`), so frontend changes deploy by rebuilding `dist` and copying — no internet/Vercel needed. (A Vercel copy also exists for convenience.)

## Target: thin edge + central server (deferred)

The Pi becomes a sensor that emits a `shotEvent`; a central server runs the engine, owns the DB, serves the UI. Offline-first with store-and-forward sync ("Option B"). Deferred until the Pi-local system works end-to-end. See [[0002 - shotEvent + central processing (deferred)]].

## Key components (current repo)
- `radar/recorder.py` — captures raw TLV frames to JSON (per session type).
- `radar/streamer.py` — streams frames to UI for live visualization.
- `engine/game_engine.py` — physics + fielding simulation (Python). Mirror in `src/gameEngine.ts`.
- `server/` — WebSocket server, handlers, session + connection managers, REST API.
- `db/` — SQLite repository + migrations.

## The missing core
`radar → ball detection → kinematics (exit speed, angles)` is **not built yet**. That is the point of [[Data Gathering Mode]] and the [[Development Roadmap]].
