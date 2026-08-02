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
- [[2026-08 Review — Findings & Plan]] — **current**: full re-review, verified bugs + prioritised fixes (nothing executed yet)
- [[2026-07 Hardening Plan]] — previous full review; all phases executed 2026-07-03

### 🧠 Learnings
- [[Database Migrations and SQLite]]
- [[Pi Deployment and Ops]]

### 📓 Field Sessions
- `sessions/` — one note per data-gathering trip (see [[Data Gathering Mode]] for the template)

## How to use this vault
- **Decisions** are append-only ADRs. When we change our mind, write a new one that supersedes the old (don't delete history).
- **Learnings** capture non-obvious gotchas so we don't relearn them.
- **Sessions** record what we did at the nets and what the data showed — the raw material for tuning.

## Current status (2026-08-02)
- 📋 **Second full-codebase review done → [[2026-08 Review — Findings & Plan]]. Nothing executed yet.** Six parallel subsystem audits, findings verified by reproduction. Key structural finding: the repo is **three layers and only one is live** — the frontend sends no scoring messages (all scoring is `localStorage`), so the whole server-side persistence stack and the detector are built but unexercised. Live P0s found: undo fabricates runs across players; the health monitor reports a frozen server as healthy and can reboot-loop the Pi; the Python engine carries a test hook into production that forks it from the TS engine on steep shots. All existing gates (parity, pytest, vitest, build) are green *with every one of these present*.
- ⛔ **Do not go to the nets until Tier 0 is fixed.** The recorder is already corrupting frames (garbage floats present in the existing recordings, and in the regression fixture), and it silently drops every frame once a scene exceeds ~350 points — an empty room already averages 60. Data gathered now would be poisoned and the detector would be tuned against it.
- 🔓 The leaked Pi password is **still in this public repo's history** (three commits confirmed). Rotate + scrub still outstanding since 2026-06-27.

### Previous (2026-07-03)
- ✅ Full-codebase review + hardening program executed (all phases) → [[2026-07 Hardening Plan]]. Highlights: single-owner radar reader (record+stream concurrently), disconnect lifecycle + async DB on the server, engines unified with a 1,154-shot parity suite in CI, ball-detection pipeline + offline replay harness, dead code purged, contracts re-synced, pytest/vitest/CI green.
- ✅ [[Data Gathering Mode]] built (crash-safe JSONL, wagon-wheel ground truth, mock flagging).
- ✅ **Pi fully deployed and verified live (2026-07-03)**: radar enumerated and streaming REAL data (`mock: false`), migrations 001–004 applied to the real DB, all 5 services enabled, current frontend served on :5173, live point cloud confirmed in the browser.
- ✅ First real tuning iteration done: an empty-room recording exposed multipath/aliasing ghosts (33 false balls); three physics-consistency checks in the detector now silence them (frozen as a regression fixture). Note: ghost doppler clusters at ~26 m/s — check the profile's true unambiguous-velocity limit vs the cfg's "145 km/h" comment.
- 📋 The Vercel (https) app cannot reach the Pi's ws:// — by browser design. Field UI = http://cricketradar.local:5173 (deploys now keep its build current).
- 🎯 Next (superseded — see [[2026-08 Review — Findings & Plan]] for the corrected sequence): nets session → record `bowling` + `racket`/`racket_foil` + `both` with wagon-wheel taps → tune detection offline (`tools/replay_jsonl.py`) → fit the radar→field direction calibration → wire detector to the live reader → emit `shot_result`. **Blocked on Tier 0** (recorder corruption, packet-size cap, fsync/disk) and the detector geometry fixes in Tier 2 — tuning against the current detector would fit around a wrong-plane direction and a −15% speed bias.
