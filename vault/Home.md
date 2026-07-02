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

## Current status (2026-06-27)
- ✅ P0/P1 bug fixes deployed to Pi (migration 003 applied).
- ✅ Architecture decision: stay Pi-local now, move to central server later → [[0001 - Keep everything on the Pi for now]].
- 🔧 Building [[Data Gathering Mode]].
- ⛔ Blocker: radar not currently enumerated on the Pi (no `/dev/ttyUSB*`) — see [[Pi Deployment and Ops]].
