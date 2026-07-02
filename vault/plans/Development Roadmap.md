# Development Roadmap

Phased plan. We are at **Phase 1**. Everything stays on the Pi for now ([[0001 - Keep everything on the Pi for now]]).

## Phase 0 — Stabilise (DONE)
- [x] Fix P0/P1 bugs (secret, wicket/extras stats, fabricated manual data).
- [x] Migration 003 deployed to Pi; DB consistent.
- [ ] Rotate Pi password + scrub git history (owner action).

## Phase 1 — Gather shot data (NOW)  → [[Data Gathering Mode]]
Goal: capture real radar data at the nets, labelled, to understand the ball signature and tune the system.
- [ ] **Unblock radar** — get `/dev/ttyUSB0/1` enumerating (see [[Pi Deployment and Ops]]).
- [ ] Build data gathering mode (longer capture + per-ball labels + crash-safe save).
- [ ] Field session: capture **ball-only**, **bat-only**, **both** (per existing test plan).
- [ ] Log findings in `sessions/`.
- **Exit:** we know what a ball looks like in the data and have a labelled dataset.

## Phase 2 — Ball detection + kinematics
- [ ] Offline analysis tool over recordings → characterise ball vs bat/noise (velocity, SNR, cluster size, trajectory).
- [ ] Implement detection (rule-based first; ML only if signatures overlap).
- [ ] Extract exit speed + horizontal/vertical angle from the ball track.
- [ ] Feed kinematics → existing game engine → outcome.
- **Exit:** >~70% of balls auto-detected with believable kinematics.

## Phase 3 — Close the loop on-device
- [ ] Radar → detection → engine → DB → UI, live, end-to-end at the nets.
- [ ] Manual-input fallback for missed balls (already correct post-bugfix).
- **Exit:** a real net session scores itself.

## Phase 4 — Tune & polish
- [ ] Tune engine constants against gathered ground truth.
- [ ] Profile-switching UX, session history/analytics.

## Phase 5 — Transition to central (Option B)
- [ ] Define `shotEvent` boundary ([[0002 - shotEvent + central processing (deferred)]]).
- [ ] Central server: engine + DB + API; Pi store-and-forward sync.

## Cross-cutting cleanups (from code review, P3)
- [ ] Deduplicate `TLVParser` (`radar/recorder.py` + `radar/streamer.py`) into `radar/tlv.py`.
- [ ] Pin Python deps (`requirements.txt`); `websockets` import path breaks on v14+.
- [ ] Replace boilerplate `README.md`.
