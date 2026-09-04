# Codebase Map

The narrative companion to the generated [[Codebase Inventory]]. The inventory
is re-derived from source by `python3 tools/codebase_map.py --write` and cannot
go stale; this page explains what the inventory cannot: why each piece exists,
which invariants it protects, and where the traps are. Update it when the
shape of the system changes, not when a line does.

Written 2026-09-03 as part of the [[Review Playbook]].

---

## 1. Three layers, one live

The repo reads as one system. It is three, and only the first runs:

| Layer | What | Runs today? | Mechanical check |
|---|---|---|---|
| **1 Live** | Phone app (React, scoring in localStorage) + `simulate_shot` on the Pi + radar record/stream + 5 systemd units | Yes | `codebase_map.py` "live path" = message types the phone sends AND the server handles |
| **2 Next** | `radar/detector.py` -> kinematics -> engine (`shot_result`) | No. Wired only to `tools/replay_jsonl.py`. Blocked on mount calibration. | `shot_result` is emitted by no live handler |
| **3 No consumer** | Server-side persistence: `db/repository.py`, 4 migrations, `session_summaries`, `server/rest_api.py` | No. The phone sends none of the scoring messages. | nine "dormant" handlers in the inventory |

**Severity is graded on the live path.** A bug in layer 3 is real but latent
until a decision ([[0002 - shotEvent + central processing (deferred)]]) wires
it in. Hardening layer 3 while layer 1 ships scoring bugs is the mistake the
2026-07 review made and the 2026-08 review corrected.

---

## 2. Runtime topology

See [[System Overview]] for the picture. In one paragraph: the Pi runs
`cricket-server` (WebSocket :5002, Python, owns the radar UART), `cricket-ui`
(static dist/ on :5173), `cricket-radar` (one-shot: sends the profile to the
IWR6843 at boot), `cricket-health` (watchdog) and `cricket-autohotspot`
(starts the AP if no known WiFi). The phone loads the UI **from the Pi**
(`http://<pi>:5173`), connects to `ws://<same host>:5002`, and keeps every
score in its own localStorage. A Vercel copy of the UI exists but, being
https, can never open the Pi's ws:// socket - it is a demo, not the field UI.

---

## 3. Directory map

### `src/` - the phone app (TypeScript/React, Vite 8)

| File | Responsibility | Invariants / traps | Tests |
|---|---|---|---|
| `App.tsx` | The whole UI: scoreboard, over tracker, field editor, wagon wheel, modals. Owns React state, the undo stack, the wagon wheel. | Undo history, wagon wheel and last-ball are **shared per-innings display state** reset on batter switch; entries carry `profileId` and a foreign entry is discarded, never applied (the 2026-08 P0). `activeProfileIdRef` guards the async simulate path. Fielder names sent to the engine are de-duplicated ("Short Leg 2"). | None directly - the logic that can be tested was extracted below |
| `scoring.ts` | **The scoring law**, pure: extras, over rollover on 6 legal balls, wickets, strike rate, undo snapshots. | Behaviour-preserving extraction from App.tsx (2026-09). | `__tests__/scoring.test.ts` (20) |
| `settings.ts` | Field layout / batter hand / difficulty persisted to localStorage, validated on load. | Malformed storage degrades to defaults. | (covered via App; parse is pure) |
| `gameEngine.ts` | The TypeScript engine (browser/offline). | **Bit-identical outcomes to `engine/game_engine.py` for the same inputs and seed.** All constants from `engine/engine_params.json`; PRNG is mulberry32; exports `ENGINE_LIMITS`, `getBoundaryDistanceAtAngle`, `normalizeAngle`. | `__tests__/gameEngine.test.ts`, `tools/parity/` |
| `fieldZones.ts` | Screen% <-> metres, zone naming (Voronoi over seeds), the four presets. | Takes batter offset and pitch length from engine params. `DEFAULT_BOUNDARY_RADIUS = 70` lives here (a session choice, not an engine constant). Left-handers: the UI mirrors X before conversion. | `__tests__/parityFields.test.ts` pins the presets against the parity suite |
| `hooks/useServerSimulation.ts` | Connection lifecycle (generation token), discovery, heartbeat, simulate-with-local-fallback, generic request/response. | Every failure path (timeout, disconnect, **server error reply**) resolves the shot with the local engine and the same seed. `disconnect()` releases the single-flight latch. | `__tests__/useServerSimulation.test.tsx` (fake WebSocket) |
| `api/config.ts` | Server URL sources and discovery order. | Order: `?server=` > saved > **same-origin host** > last-working > mDNS names. Same-origin only over http from a non-localhost host. | `__tests__/config.test.ts` |
| `components/RecordingModal.tsx` | 15s clips by type. | Shows the MOCK warning like the data-gathering modal. | - |
| `components/DataGatheringModal.tsx` | Long labelled captures; wagon-wheel taps -> `add_annotation {direction_deg}` (0 = bowler, **+90 = leg**). | Direction sign is the OPPOSITE of the engine's angle (+off). Distinguishes "hit max duration" from "write failed" using `last_error`. | - |
| `components/RadarVisualizer.tsx` | Live point cloud on a canvas, fed by `radar-frame` window events. | Draws (x, y) as a top-down view - this is the **sensor** frame; it is not the field. | - |
| `components/ServerConfig.tsx` | Manual server address. | Default host `cricketradar.local`. | - |
| `main.tsx`, `index.html`, `public/sw.js` | Entry, PWA shell, service worker (network-first shell, cache-first hashed assets). | Google Fonts are render-blocking online; cached after first load. | - |

### `engine/` - the Python engine (the Pi's copy)

| File | Responsibility | Invariants / traps |
|---|---|---|
| `game_engine.py` | `simulate_delivery(...)` and `_calculate_trajectory(...)`; the reference implementation. | Parity with the TS twin (see section 5). Input sanitisation is identical in both trajectory functions AND in simulate. No test hooks in production (the 2026-08 max-height rescale is gone). |
| `engine_params.json` | **The only place a constant may be tuned.** 37 keys, all read by both engines (the inventory checks this). | Literals that shadow a param fork the engines silently - the TS engine had three until 2026-09. |
| `prng.py` | mulberry32, bit-identical to the TS one. | Pinned to golden vectors in `tests/test_engine.py` and `src/__tests__/gameEngine.test.ts`. |

### `server/` - the Pi's WebSocket server (Python, websockets legacy API)

| File | Responsibility | Invariants / traps |
|---|---|---|
| `websocket_server.py` | `serve()`, per-connection loop, heartbeat (30s `connection_status` to every client, concurrently), reaper (60s inbound-silence), startup reconciliation of `active_sessions`. | The heartbeat loop IS the reaper: it must never block on one client. Uses the **legacy** websockets API on purpose: the Pi's apt `python3-websockets` is 10.x, which lacks the asyncio API; the pin is `<16`. |
| `message_router.py` | Envelope + type-specific validation, dispatch. | **Validation never raises** - every malformed payload gets an `error` frame (E30xx) and the connection stays up. Every comparison is preceded by a type check. `VALID_CLIENT_TYPES` must equal the registered handlers (inventory checks). |
| `handlers.py` | One method per message type; `cleanup_client` on disconnect; the live radar stream fan-out. | Radar frames: one bounded queue (5) + one drain task per client, cancelled on stop/disconnect. `start_recording`'s `is_recording` pre-check is a fast path only - the recorder's lock is the guard. |
| `connection_manager.py` | Client registry, per-session groups, sends. | `send_to_client` is bounded (5s) and **evicts** on timeout; broadcasts fan out with `gather`. |
| `session_manager.py` | In-memory session state for layer 3. | Dormant. Note extras score 0 runs here (the phone scores 1) - fix WITH the wiring, not before. |
| `rest_api.py` | :5003 REST over the DB. | No systemd unit, no client. Decision pending. |

### `radar/` - capture, parsing, detection

| File | Responsibility | Invariants / traps |
|---|---|---|
| `serial_utils.py` | Open the data UART with `exclusive=True` and **HUPCL disabled**. | DTR on this board resets the IWR6843. Never open/close the UART casually (the health monitor only `stat()`s it). |
| `reader.py` | `RadarSource`: the **single owner** of `/dev/ttyUSB1`. Reader thread -> bounded queue (50) -> dispatch thread -> subscribers. Mock frames when the port is absent, flagged `is_mock`. | Subscribers must not block the reader; backpressure drops whole frames (counted), never bytes. Falls back to mock and retries the port every 5s. Each start is a **generation** with its own stop event and a thread-local serial handle; a start joins the previous generation first (a stop/start race used to leave two readers on one tty). |
| `tlv.py` | TI TLV parser with structural + physical validation. | Drops a frame unless numTLVs matches, the walk reaches the packet end (32B padding slack), points are finite and within 100m / 200 m/s. Counts drops, length rejections, frame-counter gaps. Cap 64KB (8KB went blind at ~350 points). |
| `recorder.py` | Crash-safe JSONL (meta / frame / annotation / mode_change / end). | Start is atomic under a lifecycle lock; ms filenames; fsync each 1s; refuses to start without disk; **a failed write stops the recording and reports `error`**. `t_ms` is HOST time (jittery within a serial batch) - `frame_number`/`cpu_time_ms` are the hardware clock. |
| `streamer.py` | Fans frames (as `to_stream_dict`) to WebSocket callbacks. | A subscription, nothing more. |
| `profile_cfg.py` | Parses `config/profile_cricket.cfg`; derives v_max (13.0 m/s base, x3 = 39 m/s with extendedMaxVelocity), v_res, frame rate, range FOV. | The cfg comment's "145 km/h" is 140; without extendedMaxVelocity every cricket shot aliases to ~static. |
| `geometry.py` | **Overhead-mount axes** (y = down/range, ground plane = x/z), launch elevation (gravity-compensated), `MountCalibration` (yaw, mirror, height; refuses field-frame angles until `calibrated`), `fit_yaw` from wagon-wheel taps. | Verified on the one real recording: y is sign-constrained (0.6% negative). Field direction = sensor direction - yaw (mirror flips). Engine angle = -field direction. |
| `mount.json` | The calibration. **Not calibrated yet.** | Until `calibrated: true`, `BallEvent.horizontal_angle_deg` is None and nothing may feed the engine. |
| `detector.py` | Gate -> cluster in (x,y,z,doppler) -> predict-and-gate tracking with global assignment -> per-point cos-corrected, de-aliased doppler speed -> consistency checks -> `BallEvent`. | Uses the hardware frame clock when `frame_period_ms` is set, kept **monotonic across a frame-counter reset** (radar restart closes all live tracks). Rejects tracks whose doppler disagrees with their displacement (the ghost signature). Speed cap = engine limit (200). |

### `db/` - SQLite (layer 3)

`migrate.py` is not transactional (SQLite auto-commits DDL): **every migration
must be idempotent** ([[Database Migrations and SQLite]]). `repository.py`
uses WAL, 5s busy timeout, atomic ball numbers, rowcount-checked optimistic
locking. `contracts/database_schema.sql` is generated by
`tools/regen_schema_contract.py` - never hand-edit.

### `scripts/` - ops

| File | Notes |
|---|---|
| `deploy_to_pi.sh` | rsync (excludes db, -wal, -shm) -> apt deps (offline-tolerant, import-verified) -> units + journald cap -> **online-backup** + migrate -> restart -> verify. Warns if the Pi's `~/profile_cricket.cfg` differs from the repo copy. |
| `install_services.sh` | On-device installer for a fresh card; installs **all** units in `scripts/systemd/`. |
| `systemd/*.service` | Linted by `tests/test_systemd_units.py` (directive-per-section) and `systemd-analyze verify` in CI. No unit that binds 0.0.0.0 or uses USB waits for `network-online.target`. `StartLimit*` live in `[Unit]`. `ReadWritePaths=-` tolerates missing dirs. |
| `health_monitor.py` | Real handshake + ping/pong probe; radar is **never** restarted (a reconfigure can mute the chip); reboot is gated on "server was healthy once" and a persisted 1h cooldown. |
| `configure_radar.py` | Sends the cfg line by line at boot; detects failure only by the literal "Error" in the reply. |
| `static_server.py` | dist/ on :5173, SPA fallback, real 404s, `immutable` for `/assets/*`, `no-cache` for the shell, threaded. |
| `autohotspot.sh` | Boot-only: AP "CricketRadar" (10.42.0.1) if no known WiFi within ~45s. |
| `check_all.sh` | Every gate, one command. `npm run check`. |

### `tools/`

`parity/` (3,320 shots x both engines, CI-gated, see section 5),
`replay_jsonl.py` (offline detection + `--set` any param + `--fit-yaw`),
`codebase_map.py` (the inventory), `regen_schema_contract.py`.

### `contracts/`

`websocket_protocol.json` is complete (the inventory shows every implemented
type declared). `api_types.ts` / `api_types.py` are **partial** (13 live types
missing) and only the TS one has consumers (`src/api/__tests__/types.test.ts`
tests its type guards). Regenerating both from the JSON is the right fix,
deferred until layer 3 is decided.

### `tests/` (pytest, 192) and `src/__tests__/` (vitest, 101)

The inventory lists which test imports which module. Modules with **no
direct test**: `server/rest_api.py`, `server/session_manager.py` (exercised
end-to-end only), `scripts/configure_radar.py`, `radar/serial_utils.py`
(needs hardware).

---

## 4. Data flows

### 4.1 A simulated shot (live)

```
App.simulateShot
  -> fieldConfig (screen% -> metres, mirrored for LH, names de-duplicated)
  -> useServerSimulation.simulateAsync(seed)
       connected?  send simulate_shot{seed} -> router validates -> handlers.handle_simulate_shot
                   -> engine._calculate_trajectory -> simulate_delivery(seed) -> simulate_result
                   (timeout 5s / disconnect / error reply -> runLocal(seed))
       offline?    runLocal(seed): gameEngine.calculateTrajectory + simulateDelivery
  -> wagon-wheel line (only if still on strike) -> addRuns -> scoring.applyDelivery -> localStorage
```
Same seed, same outcome, whichever engine answered - that is what the parity
suite guarantees and what makes the fallback invisible.

### 4.2 Radar capture

```
/dev/ttyUSB1 --read(4096)--> RadarSource reader thread --TLVParser--> bounded queue(50)
                                                                        |
                                                   dispatch thread <----+
                                                     |-> RadarRecorder._on_frame  (JSONL, fsync 1s)
                                                     |-> RadarStreamer._on_frame  -> per-client bounded queue(5) -> drain task -> ws send (5s timeout)
                                                     |-> (future) BallDetector.process_frame
```
The reader stops when the last subscriber leaves. `is_mock` is resolved
BEFORE a recording subscribes, so the meta line is honest.

### 4.3 Scoring (phone-local)

`profiles[]` in localStorage is the system of record. `applyDelivery` is the
only way a ball is scored. Undo restores a deep snapshot and truncates the
wagon wheel to the length recorded with that snapshot (wides add no line).

### 4.4 Deploy

`deploy_to_pi.sh` (above). The Pi's checkout is **not a git repo**; rsync is
the transport. Frontend changes reach the phone only via `dist/` on the Pi.

---

## 5. Invariants and contracts (the things a change must not break)

1. **Coordinate system** (CLAUDE.md): batter at origin, +Y bowler, +X leg;
   engine angle +off/-leg; wagon-wheel/annotation direction +leg/-off.
2. **Engine parity**: same inputs + seed -> identical result in both engines.
   Change both engines together; tune only `engine_params.json`; run
   `tools/parity/` (CI regenerates `shots.json` and fails on diff; `compare.py`
   refuses results not produced from the current shot set).
3. **PRNG golden vectors** pinned in both test suites.
4. **Message envelope** `{type, message_id(uuid), timestamp(iso), payload{}}`;
   replies carry `in_reply_to`; every malformed message gets an `error` frame.
5. **Error codes** used in code are documented in `contracts/error_codes.md`
   (inventory checks). E61xx are recording-state; E60xx reserved for hardware.
6. **Migrations are idempotent and forward-only in production**; rollback is
   a dev tool.
7. **One owner of the UART**; HUPCL disabled; nobody else opens the port.
8. **Overhead mount**: sensor y is vertical; ground plane is (x, z); no
   field-frame angle until `radar/mount.json` is calibrated.
9. **The radar is never restarted by software**; a reconfigure can mute the
   chip; only a replug recovers it.
10. **Every systemd directive is valid for its section**; no 0.0.0.0-binding
    or USB unit waits for `network-online.target`.
11. **The phone is the system of record for scores** until ADR 0002 is
    revisited; server-side scoring stays dormant and untuned.
12. **Recordings flagged `mock: true` are never tuning data.** Of the 20
    recordings on this laptop, ONE is real (`bowling/2026-07-03_09-09-36`).

---

## 6. What the gates can and cannot see

| Gate | Catches | Blind to |
|---|---|---|
| pytest (192) | server protocol, router, recorder, TLV, detector mechanics on synthetic overhead data, health monitor, migrations, unit files, deploy ordering | anything needing the radar or the Pi |
| vitest (101) | scoring law, engine guards, PRNG, discovery order, hook lifecycle with a fake socket | rendering, touch, CSS, real WebSocket |
| parity (3,320) | any engine divergence on the sampled inputs | inputs outside the grid; NaN/Inf (not JSON) |
| tsc / eslint | types, unused code | logic |
| `codebase_map.py --check` | message/handler/param/error-code/unit drift | semantics |
| shellcheck, unit lint, systemd-analyze | ops file errors | whether the Pi is actually configured that way |

Every review so far found P0s with all gates green. The bar for a finding is
therefore a **reproduction**, not a reading - see the [[Review Playbook]].

---

## 7. Known unknowns (need the hardware)

Carried from the 2026-08 list, still open:

1. Is `extendedMaxVelocity` engaging? If not, v_max is 13 m/s and every shot
   aliases to ~static (`test_a_ball_reading_as_static...` pins the signature).
2. Mount height and yaw/mirror - fitted from a real `both` session with taps
   (`replay_jsonl.py --fit-yaw`), then committed to `radar/mount.json`.
3. Is the Pi's `db/cricket.db` fresh-migrated or legacy-upgraded
   (`created_at TEXT DEFAULT ''`)?
4. Which python3-websockets version apt installed (legacy API needed).
5. Boot time without known WiFi after dropping `network-online.target`.
