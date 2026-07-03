# 2026-07 Full Review — Findings & Hardening Plan

Source: full-codebase review (5 parallel subsystem audits: server core, DB layer, dual game engines, frontend, radar/ops), 2026-07-03. Everything below was verified against code, not speculated.

## Showstoppers (the product is broken in these ways TODAY)

1. **Deploys migrate the wrong database.** `deploy_to_pi.sh` runs `MigrationRunner(Path("cricket.db"))` (repo root) but the server uses `db/cricket.db` (`db/repository.py:29`). The systemd unit's `ReadWritePaths` only covers `db/`. So migration 003 has likely **never run against the live DB** — manual wickets/wides/no-balls (`W`/`wd`/`nb`) still fail the old CHECK in production, the exact bug 003 was written to fix.
2. **The Vercel PWA can never reach the Pi.** All discovery URLs are `ws://` (`src/api/config.ts`); browsers block insecure WebSockets from an https origin. Installed from Vercel, the app is permanently "Offline" with no explanation. Only the Pi-served UI (:5173) works.
3. **Recorder and streamer can open `/dev/ttyUSB1` simultaneously** — no exclusivity, no shared reader. Recording while watching the live view (the intended field workflow) makes each `read(4096)` steal bytes from the other; both TLV streams corrupt silently.
4. **Python engine throws to the wrong end of the pitch.** `game_engine.py:1139` puts bowler's stumps at (0, −20.12); spec and TS say (0, +20.12). Verified: up to ~20m of phantom throw distance → phantom runs, server-side only. The Python tests pass *because* they use the retired coordinate convention.
5. **Deploy races systemd.** `pkill` + `nohup` in step 6 vs `cricket-server.service` with `Restart=on-failure` → duplicate/unmanaged servers, unbounded `server.log`.

## Major themes

- **No disconnect lifecycle.** `SessionManager.cleanup_client` has zero callers; sessions/profiles/`active_sessions` rows/stream callbacks all leak on disconnect. Heartbeat reaping can never fire (outbound sends reset `last_activity`; 30s broadcast < 60s timeout). Radar stream callbacks from dead clients run at 10Hz forever and hold the serial port.
- **Everything blocks the event loop.** Sync sqlite (30s lock timeout, no WAL), 2s thread-joins, blocking serial open, directory globs per status poll — one slow SD write stalls every client.
- **Engine parity is broken** (~17 divergences): boundary geometry (offset model vs flat 70m), misfield penalty 2.5 vs 2.0, hard-catch 1.15 vs 1.10, whole fallback-fielding path diverged, angle wraparound vs clamp, no shared constants, unseedable RNG, zero parity tests. Same shot → different result depending on WiFi.
- **Contract drift everywhere:** 15 implemented message types missing from `websocket_protocol.json`; E6001–E6005 reused with contradictory meanings; `contracts/database_schema.sql` still has the pre-003 CHECK; frontend subscribes to `shot_result`/`wagon_wheel_update` which are never sent.
- **~4,000+ lines of dead code:** entire `src/api/` client layer (tests test a third, fake implementation), legacy `db/database.py`+`db/api.py` stack, users/auth repository functions, `engine/api.py`, reconnection machinery, dead response builders.
- **Toolchain broken:** `package-lock.json` missing vitest/jsdom (`npm ci` fails), eslint config imports packages not in package.json, no CI, tests excluded from type-check.
- **Fragile radar parsing:** no `total_length` sanity check (0 → infinite busy-loop; huge → unbounded buffer), TLV parser duplicated in recorder+streamer, hardware frame timing discarded on record.
- **Ops gaps:** health monitor probes the UART open/close without the HUPCL guard (the exact failure that burned us before), references nonexistent `self.restart_count` (summary log has never printed), no disk-space check (2h JSONL ≈ 0.5–1GB), `profile_cricket.cfg` not in the repo or deploys, `cricket-ui.service` installed by nothing.
- **Durability:** no WAL, no backups, no fsync cadence; optimistic-lock UPDATEs never check rowcount; ball_number has a read-then-insert race and no UNIQUE constraint; three timestamp formats in the same columns.

## Plan

### Phase 0 — Stop the bleeding (small diffs, do first)
- [ ] Deploy: migrate `db/cricket.db`; replace pkill/nohup with `systemctl restart cricket-server`; install all 5 units + `profile_cricket.cfg`; back up DB before migrating.
- [ ] Engine: fix the stumps sign (`y - PITCH_LENGTH` in `_calculate_throw_distance` + `_calculate_alignment_score`); align misfield 2.0 / hard-catch 1.10.
- [ ] Serial: `exclusive=True` in `open_radar_serial`; reject `start_recording` while streaming and vice versa (interim until single-owner reader).
- [ ] Health monitor: fix `restart_count` AttributeError; stop open/closing the UART (use O_NONBLOCK stat probe); add disk-space check.
- [ ] TLV: validate `total_length` (40..8192), resync past false magics, cap buffer.
- [ ] Frontend: resolve in-flight simulations with the local engine on disconnect; fix `activeProfileId='1'` init; surface mixed-content error ("open http://cricketradar.local:5173").
- [ ] Regenerate `package-lock.json`; fix or drop the eslint script.

### Phase 1 — Resilience core (server)
- [ ] Disconnect lifecycle hook in `_handle_connection` finally: cleanup session, delete `active_sessions` row, deregister stream callbacks; startup reconciliation of stale rows.
- [ ] Fix heartbeat semantics (inbound-only activity) or switch to websockets' native ping/pong; client-side heartbeat too.
- [ ] `asyncio.to_thread` around repository/recorder calls; `PRAGMA journal_mode=WAL` + `synchronous=NORMAL` + short busy_timeout; cache recording counts.
- [ ] Streamer: `finally: _running=False` + reconnect with backoff; single-owner radar reader thread (parse TLV once, fan out to recorder/streamer/detector sinks).
- [ ] DB integrity: UNIQUE(session_id, ball_number) + atomic assignment; rowcount check on optimistic updates; one timestamp format; fix migration-rollback ledger order; periodic `.backup()`.

### Phase 2 — Single source of truth
- [ ] `engine_params.json` shared by both engines; port the fallback path + boundary model to TS; injectable seeded PRNG (same algorithm both sides); golden parity suite (~50 canonical shots) in CI.
- [ ] Re-sync contracts (message types, new E65xx codes, regenerate schema from migrations); delete dead code (src/api/, legacy db stack, engine/api.py, dead builders, reconnection machinery) or wire it properly.
- [ ] CI: tsc + eslint + vitest + pytest (convert test scripts to assertion-based pytest incl. W/wd/nb path, migration idempotency, disconnect-mid-session).
- [ ] Rewrite Python engine test fixtures in the production coordinate convention.

### Phase 3 — The product gap: ball detection
- [ ] `tools/replay_jsonl.py`: feed recorded JSONL through the parser interface offline; score against `direction_deg` annotations.
- [ ] Detection: doppler gating (threshold from foil-ball recordings) → per-frame clustering → velocity-scaled 2–3-frame track association → speed from doppler + cos-θ correction, angle from track direction.
- [ ] Preserve radar frame_number + cpu_time alongside host time in recordings (do before next data-gathering trip).
- [ ] Mount the tuned detector on the single-owner reader; emit `shot_result` (the contract type reserved for exactly this).

### Phase 4 — Frontend structure & PWA
- [ ] Harden `useServerSimulation`: connection generation token, detach handlers before close, clear timers, re-subscribe streams on reconnect.
- [ ] Extract `useScoringSession` reducer + `FieldEditor` from App.tsx (~700 lines); fix undo/wagon-wheel desync, left-handed mirroring, dropped-catch display mapping.
- [ ] `vite-plugin-pwa` precache + self-hosted fonts; decide Pi-origin vs wss story for the installed PWA.

## Decision needed
- Keep dual engines (recommended: shared params + golden tests) vs kill client-side simulation.
- PWA origin: install from Pi (http, simple) vs TLS on the Pi (wss, lets Vercel origin work).
