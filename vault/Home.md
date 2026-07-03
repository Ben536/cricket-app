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
- ⚠️ NOTE: migrations may never have reached the live Pi DB (deploy script migrated the wrong file — now fixed). On next Pi contact run the deploy script and check `python3 -m db.migrate --status`.
- ⛔ Blocker: radar not currently enumerated on the Pi (no `/dev/ttyUSB*`) — see [[Pi Deployment and Ops]]. Recording/streaming now clearly flag MOCK data until fixed.
- 🎯 Next: nets session → real recordings → tune detection offline (`tools/replay_jsonl.py`) → wire detector to the live reader → emit `shot_result`.
