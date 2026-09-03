# CricketRadar

A portable cricket training device: a Raspberry Pi with a TI IWR6843ISK-ODS
mmWave radar mounted above the batter, a WebSocket server and game engine on
the Pi, and a phone PWA that scores the session and simulates each shot's
outcome against a fielding setup.

- **Phone app** (`src/`, React + TypeScript, Vite): scoreboard, over tracker,
  field editor, wagon wheel, shot simulator. Scores live in the phone's
  localStorage. Served **by the Pi** at `http://<pi>:5173`.
- **Engines** (`src/gameEngine.ts` and `engine/game_engine.py`): the same
  fielding simulation in two languages, bit-identical for the same inputs and
  seed - the phone uses the Pi's when connected and its own when not.
- **Server** (`server/`): WebSocket on :5002; owns the radar UART; records
  and streams radar frames; runs the Python engine for `simulate_shot`.
- **Radar** (`radar/`): TLV parser, single-owner serial reader, crash-safe
  JSONL recorder, and the ball detector (offline-tuned, not yet wired live).
- **Ops** (`scripts/`): deploy over rsync, five systemd units, a watchdog.

## Where to start

- `CLAUDE.md` - the coordinate system, engine spec and the "Start here"
  section for tooling and gates.
- `vault/` - the engineering vault (open as an Obsidian vault):
  `architecture/Codebase Map.md` (what everything is and the invariants),
  `Review Playbook.md` (how this codebase is reviewed), `plans/` (every
  review's findings), `learnings/` (hard-won Pi facts).

## Develop

```bash
# Python (server, radar, engine)
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt

# Frontend
npm ci
npm run dev          # Vite dev server (the Pi is not at localhost - use ?server=<pi-ip>:5002)

# Every gate in one command (pytest, drift check, tsc, eslint, vitest, build, parity, shellcheck)
npm run check
```

## Deploy to the Pi

```bash
./scripts/deploy_to_pi.sh [pi-address]     # default cricketradar.local
```

Syncs the code and the built frontend, checks Python dependencies, installs
the systemd units, takes an online backup of the database and migrates it,
restarts the server, and verifies it answers. See
`vault/learnings/Pi Deployment and Ops.md`.

## Status (2026-09)

Live: phone scoring, shot simulation on either engine, radar recording and
live view, standalone boot with hotspot. Next: calibrate the overhead mount
from a real nets session (`tools/replay_jsonl.py --fit-yaw`), then wire the
detector to the engine. See `vault/plans/2026-09 Review — Findings & Plan.md`.

## Security note

A Pi login password was committed in 2026-06 and redacted from the tree, but
it remains in this public repository's history. Rotate it on the device and
scrub the history (`git filter-repo`) before relying on it.
