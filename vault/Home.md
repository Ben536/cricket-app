# CricketRadar — Engineering Vault

Design decisions, learnings, and plans for the CricketRadar project. Open this folder (`vault/`) as an Obsidian vault.

## Maps of Content

### 🏗 Architecture
- [[System Overview]] — what runs where, today vs target

### 🧭 Decisions (ADRs)
- [[0001 - Keep everything on the Pi for now]]
- [[0002 - shotEvent + central processing (deferred)]]

### 📐 Plans
- [[Development Roadmap]] — phased next steps
- [[Data Gathering Mode]] — today's task: capture shot data to tune the system

### 🧠 Learnings
- [[Database Migrations and SQLite]]
- [[Pi Deployment and Ops]]

### 📓 Field Sessions
- `sessions/` — one note per data-gathering trip (see [[Data Gathering Mode]] for the template)

## How to use this vault
- **Decisions** are append-only ADRs. When we change our mind, write a new one that supersedes the old (don't delete history).
- **Learnings** capture non-obvious gotchas so we don't relearn them.
- **Sessions** record what we did at the nets and what the data showed — the raw material for tuning.

## Current status (2026-07-03)
- ✅ Full-codebase review + hardening program executed (all phases) → [[2026-07 Hardening Plan]]. Highlights: single-owner radar reader (record+stream concurrently), disconnect lifecycle + async DB on the server, engines unified with a 1,154-shot parity suite in CI, ball-detection pipeline + offline replay harness, dead code purged, contracts re-synced, pytest/vitest/CI green.
- ✅ [[Data Gathering Mode]] built (crash-safe JSONL, wagon-wheel ground truth, mock flagging).
- ✅ **Pi fully deployed and verified live (2026-07-03)**: radar enumerated and streaming REAL data (`mock: false`), migrations 001–004 applied to the real DB, all 5 services enabled, current frontend served on :5173, live point cloud confirmed in the browser.
- ✅ First real tuning iteration done: an empty-room recording exposed multipath/aliasing ghosts (33 false balls); three physics-consistency checks in the detector now silence them (frozen as a regression fixture). Note: ghost doppler clusters at ~26 m/s — check the profile's true unambiguous-velocity limit vs the cfg's "145 km/h" comment.
- 📋 The Vercel (https) app cannot reach the Pi's ws:// — by browser design. Field UI = http://cricketradar.local:5173 (deploys now keep its build current).
- 🎯 Next: nets session → record `bowling` + `racket`/`racket_foil` + `both` with wagon-wheel taps → tune detection offline (`tools/replay_jsonl.py`) → fit the radar→field direction calibration → wire detector to the live reader → emit `shot_result`.
