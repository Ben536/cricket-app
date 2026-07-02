# 0001 - Keep everything on the Pi for now

- **Status:** Accepted
- **Date:** 2026-06-27
- **Supersedes:** —

## Context
The long-term vision is a thin edge device emitting `shotEvent`s to a central server that owns data and processing (see [[0002 - shotEvent + central processing (deferred)]]). But the Pi-local system isn't yet proven end-to-end (ball detection is unbuilt), and centralizing now would add sync/connectivity complexity before the core works.

## Decision
Keep **all** components on the Pi (detection, game engine, SQLite, UI) until the full loop works at the nets. Then transition to **Option B**: offline-first edge with store-and-forward sync to a central server.

## Consequences
- ✅ Simplest path to a working prototype; no connectivity dependency for field sessions.
- ✅ Reuse existing on-Pi code as-is.
- ⚠️ DB + engine logic will later need to move/duplicate to the server. Keep the `shotEvent` boundary clean now so the move is mechanical (the Pi already computes kinematics → engine; that seam is the future network boundary).
- The bug fixes in migration 003 / handlers / stats stay on the Pi for now and travel to the server later.

## Transition trigger
Move to Option B once: (1) ball detection is reliable (>~70% per the roadmap), and (2) we want shared data/stats across people or devices.
