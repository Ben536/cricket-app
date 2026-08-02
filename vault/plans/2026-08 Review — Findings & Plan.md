# 2026-08 Full Review — Findings & Plan

> **Status: IN PROGRESS.** Done so far: **T1.1, T1.1b** (undo/scoring correctness) and
> the watchdog cluster **T1.4 + T1.5 + T1.6**, landed together because fixing the health
> check alone would have made the reboot loop *easier* to trigger. Everything else below
> is still proposed. Items are marked ✅ DONE inline as they land.
>
> Source: full-codebase review, 2026-08-02,
> six parallel subsystem audits (dual engines, radar pipeline, server core, DB layer,
> frontend, ops/deploy/contracts). Successor to [[2026-07 Hardening Plan]], whose
> phases were all executed on 2026-07-03.
>
> Every finding below was verified against code that was **read or run**, not
> speculated. Where an audit reproduced a bug, the repro is cited. Findings that
> could not be confirmed are quarantined in "Needs the Pi" at the bottom — they are
> not in the plan.

## Method

Six auditors, one subsystem each, all read-only. Each was told the 2026-07 audit had
already fixed the shallow bugs and was tasked with finding what it missed — with
extra weight on code committed *after* it (the detector, ghost rejection, deploy
frontend sync). Each had to produce a concrete failure scenario with real numbers,
not a code smell. ~80 findings came back; this plan keeps the ones that survived
verification and drops the rest.

The 1,154-shot parity suite, 35 pytest tests and 47 vitest tests are all green, and
`npm run build` succeeds. **None of the P0s below are caught by any of them** — that
is the point.

---

## The finding that reframes everything: three layers, one is live

The repo reads as one system. It is actually three, and only one of them runs.

| Layer | What it is | Status |
|---|---|---|
| **1. Live** | Phone app (React) + `simulate_shot` + radar record/stream + the 5 systemd units | **Running today** |
| **2. Dormant — next up** | `radar/detector.py` → kinematics → engine | Built, wired to nothing but `tools/replay_jsonl.py` |
| **3. Dormant — no consumer** | Server-side persistence: `db/repository.py`, 4 migrations, `session_summaries`, REST API :5003 | Built, hardened, **never written to** |

Verified mechanically:

```
server registers:  start_session end_session manual_input undo select_profile
                   create_profile update_profile set_field set_difficulty  (+ radar/recording)
frontend sends:    start_recording stop_recording get_recording_status
                   start_radar_stream stop_radar_stream add_annotation  (+ simulate_shot)
```

The frontend sends **none** of the scoring messages. All scoring lives in
`localStorage` (`src/App.tsx:123,141`); nothing in `src/` calls the REST API. So the
phone is the system of record, `db/cricket.db` holds no deliveries, and :5003 has no
systemd unit *and* no client.

**Why this matters for prioritisation:** the 2026-07 plan spent its Phase 1 and much
of Phase 2 hardening layer 3 — atomic ball numbers, optimistic locking, WAL, UNIQUE
constraints. All correct, all currently unexercised. Meanwhile layer 1 shipped a P0
scoring bug and layer 2's geometry is systematically wrong. This plan rebalances
toward layers 1 and 2.

Severity below is graded **on the live path**, not in the abstract.

---

## Tier 0 — Do before the next nets session

The next milestone on the [[Development Roadmap]] is a data-gathering trip. **Going
now wastes the trip**: the recorder corrupts frames, drops them silently when the
scene gets busy, and the detector's geometry is wrong, so anything tuned against the
resulting data is tuned against noise.

### T0.1 — The recorder is corrupting the data it exists to collect · P1
`radar/tlv.py:148-192`, root cause `radar/reader.py:125-134`

When bytes are lost mid-packet, `len(buffer) >= total_length` is satisfied using the
*next* packet's bytes. The parser emits a frame whose tail is arbitrary bytes decoded
as float32. Nothing validates `numTLVs` (header[24:28] is never read), that the TLV
walk consumed the packet, or that coordinates are finite.

Already in the data. In `recordings/bowling/2026-07-03_09-09-36.jsonl`, 6 of 60
frames are affected (frames 4031, 4046, 4065, 4070, 4085 …), each followed by a
dropped frame number — the corrupt frame ate its successor. I confirmed it
independently across all recordings: 3,102 clean points, but `y` ranges to
**−509,726 m**, and 5 NaNs are present. `tests/fixtures/real_static_clutter.jsonl`
— the regression fixture — contains two corrupt frames.

**Fix:** after the TLV walk require `tlv_start == len(packet)` and
`tlvs_parsed == numTLVs`; drop the frame otherwise. Add a physical filter
(`isfinite`, `|x|,|y|,|z| ≤ max_range`, `|doppler| ≤ v_max`). Count frame-number
discontinuities as lost frames.

### T0.2 — Synchronous fan-out stalls the UART, which is what causes T0.1 · P1
`radar/reader.py:125-134`; offending sink `radar/recorder.py:213-219`

`_dispatch` calls every subscriber inline on the reader thread. `recorder._on_frame`
does `json.dumps` + `write` + `flush` under a lock per frame — a blocking SD-card
write. Nothing drains the tty meanwhile, the kernel buffer overflows, bytes are lost
mid-packet. Measured: 788 reads in 0.5 s with a fast subscriber, **18** after adding
a 50 ms subscriber (−98%).

**Fix:** reader thread does `read()` → `parser.add_data()` → bounded
`queue.put_nowait()` only; a separate dispatch thread runs subscribers. Drop-oldest
on overflow and count drops, so backpressure loses *frames* (visible) not *bytes*
(silent corruption).

### T0.3 — The system goes blind exactly when the scene gets busy · P2
`radar/tlv.py:36` (`MAX_PACKET_LENGTH = 8192`), enforced `tlv.py:142-145`

With `guiMonitor -1 1 1 1 0 0 1` the frame carries point cloud + range profile
(512 B) + noise profile (512 B) + stats + side info. Past ~350 points the packet
exceeds 8192, the length check fails, the parser treats it as a false magic and
resyncs. Every such frame is lost, at DEBUG level only.

The *empty-room* recording already averages 60 points/frame. A net, bat, batter,
bowler and ground clutter reach 400+ → 9,128 bytes → **every frame dropped**, live
view frozen, no shot ever detected, nothing above DEBUG in the log.

**Fix:** raise the cap to the SDK's real bound (~64 KB), log rejections at WARNING
with rate limiting. Separately turn off `logMagRange`/`noiseProfile` in `guiMonitor`
— 1,024 unused bytes per frame, ~48% of the packet.

### T0.4 — No fsync, no disk check · P2
`radar/recorder.py:213-219`, `:40`

`flush()` moves bytes to the page cache, not the card; the realistic failure is a
battery cut, losing ~30 s. Measured 8,711 bytes/frame at 20 Hz = 0.63 GB/hour, so a
2 h `MAX_GATHERING_SECONDS` session writes **1.25 GB** with no free-space check. A
full card makes `write()` raise inside `_on_frame`, which `_dispatch` swallows at
ERROR — recording silently stops while the UI still says "recording".

**Fix:** `os.fsync` every ~1 s; `shutil.disk_usage` check before starting, abort with
a clear error.

### T0.5 — Settle the mount geometry with a 5-minute test
Before writing any detector fix, record 10 s with the radar mounted overhead and a
ball rolled along a known ground line. If `y` stays ≈ mount height while `x` and `z`
sweep, T2.1 is confirmed outright and the fix can be written with confidence.

Also settle **whether `extendedMaxVelocity` is actually engaging** (point at a
known-speed object at 60-80 km/h). This is the single most consequential open
question in the project: if it is not, real v_max is ±12.97 m/s (46.7 km/h) and
*every cricket shot aliases*.

---

## Tier 1 — Bugs on the live path

### T1.1 — Undo fabricates runs and pastes one batter's innings onto another · P0
`src/App.tsx:166` (state), `:534-539` (push), `:601-630` (`undoLastBall`), `:989`

`sessionHistory` is one **global** stack, but `undoLastBall` writes the top entry
into **`activeProfileId`'s** session. An entry pushed while Player 1 batted gets
applied to Player 3. The Undo button's `disabled` test is also global, so it stays
enabled for a batter who has faced nothing.

Reproduced end-to-end in jsdom against the real `App.tsx`:

```
P1 done             Player 1  14-0 off 6, 1×4, SR 233.33, 1.0 ov
new player          Player 3   0-0 off 0            Undo disabled? false
after 1             Player 3   1-0 off 1
undo #1 (correct)   Player 3   0-0 off 0
undo #2  <-- BUG    Player 3  13-0 off 5, 1×4, SR 260.00, 0.5 ov, over [1 4 • 2 6 -]
Player 1 unchanged? Player 1  14-0 off 6      <- 13 runs and 5 balls invented
```

Also reachable via the Select Player buttons and after `deleteProfile`, which leaves
a dead profile's snapshots on the stack.

**Fix — ✅ DONE 2026-08-02.** One `useEffect` on `[activeProfileId]` clearing all
three (`src/App.tsx:229-245`). All three reset together deliberately: each history
entry's `wagonWheelLength` is an index into the *shared* `wagonWheelShots`, so
per-player undo history would need a per-player wagon wheel to stay meaningful.
This also fixed T1.2.

Verified: `tsc --noEmit`, `eslint src`, `vitest` (47/47), `npm run build` all exit 0;
the fix was tested against a control run of the pre-fix file, which reproduced the
bug exactly (13-0 off 5, SR 260.00). Six regression cases pass — undo of a wide
still doesn't eat the preceding boundary's wagon-wheel line, undo across an over
boundary returns 1.0 → 0.5 ov, and switching away and back preserves the returning
batter's score. Zero extra render commits on mount.

**Trade-off accepted:** switching batter now drops the returning player's undo stack
and wagon wheel (the score itself survives in `profiles`). For alternating strike
that means the wagon wheel resets on each change of strike — better than showing the
wrong player's shots, but a known cost.

### T1.1b — The same bug survives on the async simulate path · P1 · ✅ DONE 2026-08-02
`src/App.tsx:529-539` (push after the await), `:593-598`

`simulateShot` awaits the Pi, then calls `addRuns`, which pushes to the **global**
`sessionHistory`/`wagonWheelShots` *after* the await. Switch batter inside that
window and the runs correctly land on the old player (the render closure holds the
old `activeProfileId`) while the undo entry and wagon-wheel line land on the **new**
one. Reproduced end to end with a stubbed in-flight WebSocket:

```
P2 after shot   0-0 off 0 | undoDisabled=false | wagon [dot]   <- entry from P1's shot
P2 after Undo  14-0 off 6 | SR 233.33                          <- P1's innings pasted onto P2
P1 afterwards  14-0 off 7 | 1.1 ov                             <- the dot went to P1, correctly
```

Strictly better than pre-fix (which needed no race at all), and it requires a live Pi
plus a switch inside the round-trip window — but it is the same failure mode and the
reset effect does not close it.

**Fix — DONE.** `activeProfileIdRef` mirrors the live active profile (same pattern as
the existing `profilesRef`/`wagonWheelRef`). `addRuns` and `simulateShot` each compute
`stillOnStrike = activeProfileIdRef.current === activeProfileId` and guard **only the
shared display writes** — undo push, `lastBall`/flash, and both wagon-wheel appends.
The ball is still **scored** to the closure's profile, so no delivery is lost. History
entries carry `profileId`, and `undoLastBall` discards a foreign entry rather than
applying it.

Verified with an A/B control (a copy with the guards neutralised) to prove the harness
sees the bug:

| | P2 after shot | P2 after Undo | P1 afterwards |
|---|---|---|---|
| guards off | wagon `[dot]`, undo enabled | **14-0 off 6, SR 233.33** | 14-0 off 7 |
| fixed | wagon `[]`, undo disabled | 0-0 off 0 | 14-0 off 7, over `[•]` |

Driven on all three resolution paths (server reply, 5 s timeout → local engine, offline)
and with a six as well as a dot: P1 goes 14-0 → **20-0, 6s 1→2** while P2 stays 0-0.
Toolchain clean; 27/27 harness tests pass; T1.1 and all no-regression cases re-verified,
including that an ordinary simulated shot with no switch still draws its wagon-wheel line.

**The `undoLastBall` discard branch is load-bearing, not belt-and-braces.** A click
commits synchronously but the `[activeProfileId]` reset is a *passive* effect, so there
is a sub-frame window where the new batter is on screen with the old batter's entries
still on the stack and the Undo button enabled. Measured with the branch removed: an
Undo in that window leaves **P2 at 5-0 off 2 permanently**. Do not "simplify" it away.

**Known consequences (accepted):**
- A ball scored mid-flight is unrecoverable — it lands on the original batter with no
  undo entry and no wagon-wheel line. Follows from T1.1's trade-off.
- `setSimResult`, `setCatchDisplayPosition` and `setFieldingDisplayPosition` in
  `simulateShot` are still unguarded: after a mid-flight switch the new batter briefly
  sees the old batter's result panel and a 1.5 s fielder animation. Cosmetic, transient,
  not per-innings state — left deliberately.
- `activeProfileIdRef` is assigned during render (the pre-existing pattern). Safe today;
  under concurrent features a discarded render could leave the ref ahead of committed
  state and the guard would false-negative. Worth knowing if `startTransition`/Suspense
  is ever adopted.

### T1.2 — A new batter inherits the previous batter's wagon wheel · P1
`src/App.tsx:159`, `:171` — reset only in `startNewSession`/`resumeSession`

Same root cause. Switch batter after Player 1 faces `1, 4, •, 2, 6` and the new
player's wagon wheel shows all five lines including the red 4-to-the-rope, with
Last Ball reading `6`. Covered by the same fix.

### T1.3 — The Python engine squashes high shots; the TS engine doesn't · P0
`engine/game_engine.py:893-896`, `:917` (fed by the clamp at `:273,377-379`); no
counterpart in `src/gameEngine.ts:491-575`

`_validate_and_sanitize_inputs` clamps `max_height` to 50 m.
`_find_catchable_intercept` then rescales *every sampled trajectory height* by
`(max_height − BAT_HEIGHT)/(traj.max_height − BAT_HEIGHT)` while flight time and
horizontal speed keep their true values. The comment says it exists so "test cases
[can] override the physics-based trajectory" — **a test hook that is live in
production.** I confirmed the TS twin takes only `(fielderX, fielderY, trajectory)`.

Concrete: `speed=184.91, angle=−19.30, elevation=85.68, easy, seed=2342376404`
(true apex 134.7 m, carry 46 m). Python squashes by 0.366, finds the ball at 1.72 m
— chest height, "optimal" — difficulty 0.279 → **caught, 0 runs**. TypeScript uses
the true profile, finds 0.45 m → difficulty 0.436 → **dropped, 3 runs**. Same shot,
same seed: a wicket if the Pi answered, three runs if the WiFi hiccupped.

Rate: 4.0% of shots in the steep band (speed 110-200, elevation 62-89.5°); 39/6000
across the full domain. Neutralising *only* the rescaling → 0/3000.

**Why the parity suite is green:** `tools/parity/gen_shots.py:31` uses
`elevations = [0, 2, 8, 15, 30, 45, 60, 90]` — nothing between 60 and 89, which is
exactly the affected band (69°-90° at angle 0).

**Fix:** delete the `height_scale` mechanism and the `max_height` parameter; use
`traj.time_of_flight` and the trajectory's own direction so the signature matches TS
exactly. Then add elevations 70/75/80/85 to `gen_shots.py`.

### T1.4 — The watchdog reports a completely frozen server as healthy · P0 · ✅ DONE 2026-08-02
`scripts/health_monitor.py:167-211`

`check_websocket` does `asyncio.open_connection()` and closes — a bare TCP connect
satisfied by the **kernel listen backlog**. It never performs a handshake and never
requires the Python process to be scheduled. Executed against a `SIGSTOP`ped server:

```
health_monitor verdict: HEALTHY  (0ms)   <-- server is SIGSTOPped
real client:            FAILED -> TimeoutError during opening handshake
```

A blocked event loop leaves :5002 bound but unserviceable; every phone hangs on
"Connecting…" and the watchdog takes no action, forever. It also never completes a
handshake against a *healthy* server, logging `connection rejected (400)` twice per
check — ~480 spurious journal lines per 2 h session that read like a client bug.

**Fix:** real handshake + `ping`/`pong` round-trip with a generous (10 s) timeout.
**Must land together with T1.5**, or a working check makes the reboot loop easier to
trigger.

### T1.5 — The watchdog can reboot-loop the device indefinitely · P0 · ✅ DONE 2026-08-02
`scripts/health_monitor.py:336-346`; `scripts/systemd/cricket-server.service:36-40`

`check_and_recover` treats "server never started" identically to "server died": 2
failed checks → restart, 3 restarts → `sudo systemctl reboot`. `server_restart_count`
is process-local, so every boot restarts the escalation from zero.

The crash-loop guard that should latch the unit `failed` **does not work**:
`StartLimitIntervalSec=` is not a valid key in `[Service]` (it belongs in `[Unit]`),
so systemd ignores it and falls back to `DefaultStartLimitIntervalSec=10s`. With
`RestartSec=3` the unit starts at t=0,3,6,9,12… — never 5 starts in any 10 s window
— so it restarts forever and never latches. The comment at `:34-35` describes
behaviour that does not exist.

Trigger: `recordings/` is gitignored, never rsynced and never `mkdir`ed, while
`cricket-server.service:57` has `ReadWritePaths=…/recordings`. A non-existent
`ReadWritePaths` path with no `-` prefix fails the mount namespace → exit
226/NAMESPACE before Python runs. On a reflashed card the device then **reboots every
~3.5 minutes indefinitely**, and the operator cannot reliably SSH in to diagnose it.

The vault already records this class of incident once ("rebooted the Pi after 3 mixed
restart attempts… reboot every ~90s") — fixed for the radar, left intact for the
server.

**Fix:** gate the reboot on the server having been genuinely healthy at least once
this process lifetime (not the `field(default_factory=time.time)` initialiser at
`:56`, which fabricates "healthy at startup"); persist a reboot counter and refuse
twice within an hour; move `StartLimit*` to `[Unit]`; `ReadWritePaths=-…/recordings`
and `mkdir -p` it in the deploy.

### T1.6 — The watchdog can restart the radar into a state only an unplug fixes · P0 · ✅ DONE 2026-08-02
`scripts/health_monitor.py:309-327`; `scripts/configure_radar.py:144-169`

On two failed radar checks with the device node present, it restarts
`cricket-radar`, re-running `sensorStop → reconfig → sensorStart`. [[Pi Deployment
and Ops]] records that this sequence **reports success but leaves the chip silent**,
and that a soft reboot does not fix it because USB power isn't cut.

Trigger is mundane: a foot catches the USB cable for 1-2 s. Check N stats the node →
missing. Check N+1 → still missing, but the sequence then spends up to 5 s inside
`await check_websocket()` before reaching `:311`, by which point the kernel has
re-enumerated → `device_present=True` → restart → chip goes mute. `configure_radar.py`
only detects failure by scanning the reply for the literal `"Error"` (`:154`), so a
mute chip reports success; `check_radar()` only `stat()`s the node, so the monitor
then reports the dead radar as healthy for the rest of the session.

**Fix:** delete the radar-restart action — the code's own comment at `:316-319`
already argues a restart cannot fix an absent device, and the vault shows it cannot
fix a present-but-mute one either. Replace with a `radar_degraded` flag surfaced in
`connection_status` so the UI can say "replug the radar".

### T1.7 — Two clients starting a recording corrupt each other's session · P0
`server/handlers.py:1057-1068`; `radar/recorder.py:143`

The `is_recording` guard and the mutation are separated by an await, and
`start_recording` re-checks without holding `_lock`. Two handlers both pass and both
build a session on the same singleton. Reproduced:

```
A -> recording_started 60.0
B -> recording_started 60.0        <- A asked for max_duration=2, was told 60
armed timers: 2                     <- two Timers on one recorder
files: both/2026-08-02_06-56-33.jsonl (1 line, no `end` marker, handle never closed)
```

The second `self._jsonl = open(...)` drops the first file object without closing it;
1-second filename resolution means same-second starts of the same type truncate each
other; whichever timer fires first stops the other client's recording — a 15 s clip
can terminate a 2 h gathering session.

**Fix:** make `RadarRecorder.start_recording` atomic under a state lock covering both
the check and all assignment; demote the handler's pre-check to a fast path. Use
sub-second filenames.

### T1.8 — The UI can't find the Pi that just served it · P1
`src/api/config.ts:17-20`, `:54`, `:127-162`

Discovery tries `ws://cricketradar.local:5002` then `ws://raspberrypi.local:5002` and
never considers `window.location.hostname` — even though the page was served *by the
Pi* at that address. [[Pi Deployment and Ops]] records that mDNS was **not**
resolving in the field. So at the nets each attempt burns a 3 s timeout, then "No
server found", and the operator must type the IP from memory on a phone.

**Fix:** prepend `ws://${window.location.hostname}:5002` to the discovery list when
the origin is not Vercel. One line; removes the manual-IP path entirely in the AP
case.

### T1.9 — One wedged client blocks every broadcast and the reaper · P1
`server/connection_manager.py:337-348`, `:370-373`; `server/websocket_server.py:369-373`

Broadcasts and heartbeats are sequential `await send_to_client(...)` with no
`gather`, no timeout. `websockets.send()` awaits `drain()`, which never completes
while a peer's TCP window is full — and the heartbeat loop *is* the reaper, so the
code that detects dead clients parks on the dead client. A phone walking out of AP
range (half-open socket, ~15 min of kernel retransmits) stops heartbeats for every
other phone and makes reaping impossible for that whole window. Reproduced:
`broadcast_to_session NEVER RETURNED`, `heartbeat pass NEVER COMPLETED`.

**Fix:** `asyncio.wait_for(..., timeout≈5)` per send, fan out with
`gather(..., return_exceptions=True)`, evict on timeout.

### T1.10 — Radar frame fan-out has no backpressure and swallows every error · P1
`server/handlers.py:1262-1266`

`run_coroutine_threadsafe(send_frame(...), main_loop)` is called per frame from the
reader thread and the returned Future is discarded — no queue bound, no way for a
slow socket to slow the producer, and any exception is stored in the dropped Future
and never surfaces. Reproduced: pending tasks grew 5 → 10 → 12 → 18 over 3 s and
**18 remained parked after unsubscribing**; ~72,000/hour per stuck client on a 1 GB
Pi.

**Fix:** bounded `asyncio.Queue` per client fed via `call_soon_threadsafe`, drained
by one long-lived task that is stored, cancelled in `cleanup_client`, and has an
exception callback.

### T1.11 — "Save & Reconnect" during discovery is silently swallowed · P1
`src/hooks/useServerSimulation.ts:282`, `:344-350`, `:353-356`

`reconnect()` calls `disconnect()` (bumps the generation) then `connect()`, but
`disconnect()` never clears `connectingRef` — so if a discovery await is in flight
the new `connect()` returns immediately. The orphaned discovery then bails on its
stale generation, skipping *both* the error message and the 10 s retry timer. Net:
no socket, no error, no retry. Reproduced — stuck at `disconnected` with no error
through t=16 s; only a second tap recovers. (An ordinary discovery failure with no
user interaction *does* self-heal, which is what makes this hard to spot.)

**Fix:** clear `connectingRef` in `disconnect()`, and make the `finally` at `:337-339`
generation-aware.

### T1.12 — A 404 on the UI server returns no HTTP response at all · P1
`scripts/static_server.py:36-38`

`log_message` is overridden as `print(f"[static] {args[0]} {args[1]} {args[2]}")` but
`send_error()` calls `log_error` with only **two** args → `IndexError` before the
error response is written → socket closed with nothing sent. Executed:

```
GET /assets/index-OLDHASH.js  -> curl: (52) Empty reply from server
GET /favicon.ico              -> curl: (52) Empty reply from server
IndexError: tuple index out of range
```

A PWA holding a cached `index.html` that references an asset `rsync --delete` purged
gets a connection reset instead of a clean 404, so the service worker falls to
`Response('Offline', 503)` and the app white-screens. Every occurrence also writes a
~30-line traceback, handled serially by a single-threaded `TCPServer`.

**Fix:** `print("[static]", format % args)`. Also `ThreadingHTTPServer` +
`protocol_version = "HTTP/1.1"`.

### T1.13 — Malformed payloads kill the connection and force-complete the session · P1
`server/message_router.py:549` (validation outside the try), `:433`, `:442`, `:450`

`route()` calls `validate_message()` outside its `try`, and `_validate_type_specific`
does bare `len()`/`<` on untrusted values. A `TypeError` escapes the router and the
`async for`, landing in `_handle_connection`'s catch-all — whose `finally` runs
`cleanup_client`, which marks the session complete in the DB. Reproduced with
`boundary_distance: null`, `"70"`, `fielders: 3`, `create_profile name: 12345` — all
close with code **1000 (normal)** and no error frame.

Graded P1 rather than P0 **only because** the frontend never sends `set_field` or
`create_profile` (see the three-layers finding). It becomes P0 the moment those are
wired.

**Fix:** move validation inside the try; `isinstance` checks before comparisons; send
an `error` frame before closing.

---

## Tier 2 — Unblock Phase 2 (radar → engine)

These do not affect anything today because the detector is wired only to
`tools/replay_jsonl.py`. They all fire on the day it is connected, which is the next
roadmap milestone. **Fix them before tuning, not after** — otherwise the tuning
compensates for the bugs.

### T2.1 — Horizontal direction is computed in the wrong plane · P0
`radar/detector.py:401`

`atan2(disp[0], disp[1])` treats `(x, y)` as the horizontal plane. For the overhead
face-down mount, `y` is the **boresight = vertical** axis; `z` is the second
horizontal axis, and it is **discarded entirely**.

I verified the axis convention independently against all real recordings:

```
clean points: 3102
  x<0:  478 (15.4%)
  y<0:   27 ( 0.9%)     <- sign-constrained => y is boresight/range
  z<0: 1049 (33.8%)
```

Consequence, with correct overhead geometry: a **straight drive back at the bowler
(0°) is reported as +180°**, and shots at +30° and +150° *both* report +113.1°. The
map is 4-to-1 and non-invertible, so **no calibration offset can rescue it**.

**Fix:** `atan2(disp_x, disp_z)` for ground-plane direction, `disp_y` for elevation.
Add an explicit `AXES` constant naming which sensor axis is vertical, so the
assumption is testable rather than implicit.

### T2.2 — Exit speed is systematically ~15% low · P0
`radar/detector.py:344` with `:386-396`

`speed = mean_i(|doppler_i|) / cosθ(midpoint)`. Under an overhead radar cos θ climbs
steeply from ~0 at contact toward 1, so it is strongly convex over the track — by
Jensen's inequality the mean-then-divide form under-reads. The correction must be
per-point.

```
truth   mean|dop|  cosT_mid   code speed   err    per-point corr   err
100km/h    21.22     0.920        83.0k   -17%          101.7k    +2%
120km/h    26.31     0.946       100.1k   -16%          118.5k    -1%
500 random balls (60-140 km/h): median speed error -15.5%
```

A ball hit at 100 km/h returns 82.5 km/h; fed to the engine, a shot that would carry
~62 m carries ~44 m — fours become singles.

**Fix:** `mean_i(|doppler_i| / max(cosθ_i, floor))` using each point's own LOS
vector. Recovers truth to ±2%.

### T2.3 — There is no radar→pitch transform, and the frames have opposite handedness · P0
`radar/detector.py:140`, `:403-412`

`BallEvent` is emitted in raw sensor coordinates. No mount-height constant, no
tilt/rotation calibration, no place to put one, and **no `vertical_angle` field** —
yet `contracts/api_types.py:187-189` requires all three of `exit_speed`,
`horizontal_angle`, `vertical_angle`. Separately the detector's `atan2` is
right-handed while the engine is left-handed (`src/gameEngine.ts:260`,
`engine/game_engine.py:515-516`), so the naive wiring mirrors every shot even after
T2.1 is fixed — a cover drive scores as square leg.

`tools/replay_jsonl.py:16-17` says the mount-calibration offset can be fitted from
wagon-wheel taps. **That fitting code does not exist.**

**Fix:** an explicit transform module (`MOUNT_HEIGHT_M`, `MOUNT_YAW_DEG`) mapping
sensor `(x,y,z)` → pitch `(X=leg, Y=bowler, H=height)`, emitting
`horizontal_angle = atan2(-X, Y)` to match the engine and a real `vertical_angle`.
Fail loudly if the mount constants are unset rather than defaulting to identity.

### T2.4 — No de-aliasing; v_max is nowhere in the code · P1
`radar/detector.py:93`, `:344`

The detector takes `|doppler|` at face value. The profile's unambiguous limit is
±38.90 m/s (140 km/h) by computation from `config/profile_cricket.cfg`;
`max_plausible_speed_kmh = 250.0` is a magic number unrelated to it, and **no code
reads the .cfg at all** — editing `profileCfg`/`chirpCfg`/`extendedMaxVelocity`
silently changes v_max with zero effect on the detector.

A drive at 160 km/h reports **125.6 km/h** (−21%) with no warning, landing squarely
in the plausible band so nothing downstream can tell.

Corroborating: the ghost doppler in the real recording sits at exactly 25.932 m/s =
2 × 12.966 — the textbook `extendedMaxVelocity` mis-assignment of static targets. The
"33 false balls" that motivated the ghost rejection are a predictable artefact of
that config line.

**Fix:** parse the .cfg once at startup, derive `v_max`/`v_res`, flag tracks whose
displacement speed exceeds `v_max` as aliased and unwrap, and derive
`max_plausible_speed` from `v_max` rather than hardcoding.

### T2.5 — The association gate is sized from radial speed · P1
`radar/detector.py:246-254`

`gate = max(|doppler|)·dt·2.5 + 0.5`. Doppler is radial; under an overhead radar it
is a small fraction of true speed right after contact — exactly when the gate
matters. The gate is also a sphere on the *last observed* position, not an
extrapolated one, so it is simultaneously too small along the flight path and too
permissive everywhere else. A 30 m/s ball at 10 Hz moves 3.0 m/frame; radial ~9 m/s
gives a 2.75 m gate → **track breaks, shot never confirmed** (3 hits required).

**Fix:** extrapolate from measured 3-D displacement velocity and gate on the
residual; size the first hop from `max_plausible_speed · dt`.

### T2.6 — Bat-swing points steal the track · P1
`radar/detector.py:204-242`, `:276-297`

`max_ball_cluster_points = 4` only catches large clusters; a single bat-tip return is
a 1-point cluster that competes for the same gate, and at `cluster_eps = 0.6 m` it is
averaged into the ball's centroid. Measured: a bat point 0.4 m away → **one event at
61 km/h** for a 100 km/h shot; at 0.8-3.0 m → **two events per shot** and nothing
downstream knows which is the ball.

**Fix:** cluster on `(x,y,z,doppler)` so returns with incompatible radial velocity
never merge; replace greedy per-track association with global assignment plus a
motion-model residual.

### T2.7 — The detector tests cannot detect T2.1 or T2.2 · P2
`tests/test_detector.py:79-83`, `:30-39`

`assert 70.0 <= ev.speed_kmh <= 130.0` for a 100 km/h truth — ±30% tolerance, which
is why a −15% bias survived. Every fixture also builds trajectories in a
**forward-looking** frame (x/y horizontal, fixed `z=0.8`) — the geometry the product
does not have. All 14 tests pass while the injected-trajectory probe shows −18% speed
and a 180° direction error.

**Fix:** rebuild fixtures in the overhead frame, tighten to ±5%, add a direction
round-trip sweeping azimuth −180…+180.

### T2.8 — The tuning harness can't sweep the parameters it exists to sweep · P3
`tools/replay_jsonl.py:68-80` exposes 3 of 13 `DetectorParams`; the four thresholds
added by `33d1a40` (`min_motion_ratio`, `min_straightness`, `min_moving_fraction`,
`max_plausible_speed_kmh`) are not among them.

---

## Tier 3 — Resilience & efficiency (bounded — take the cheap ones)

Ordered by value per line changed. Everything here is small.

| # | Item | Location | Why |
|---|---|---|---|
| T3.1 | `profile_cricket.cfg` is still never deployed — `config/` is absent from the rsync list, and the missing-file case is a `\|\| echo` warning, not an error | `scripts/deploy_to_pi.sh:41-75`, `:93-94` | After a reflash the radar is never configured; deploy still prints "Complete". The 2026-07 plan believed this fixed. |
| T3.2 | `requirements.txt` is never installed on the Pi; the deploy `apt-get install`s one unrelated package | `deploy_to_pi.sh:80` | `pyserial` is never installed by any automation → `ModuleNotFoundError` → radar unit retries forever. CI is green because it uses pip. |
| T3.3 | `install_services.sh` installs 3 of 5 units — no `cricket-ui`, no `cricket-autohotspot` | `scripts/install_services.sh:24-39` | A Pi set up via the documented installer has nothing serving the app and no hotspot. |
| T3.4 | Drop `Wants=/After=network-online.target` from `cricket-radar` and `cricket-server` | the two unit files, `:6-7` | Both pull `NetworkManager-wait-online`, which blocks its full ~30 s timeout at the nets because nothing connects. The radar needs USB, not IP; the server binds `0.0.0.0`. Removes ~30 s from a boot the design budgets at 60 s and that actually takes 75-90 s. |
| T3.5 | A server `error` reply rejects the shot instead of falling back locally | `useServerSimulation.ts:230-237` | Every other failure path resolves with the same-seed `runLocal()`. This one loses the ball entirely — no over entry, no wagon-wheel line. |
| T3.6 | Deploy is non-atomic with its most fragile step in the middle | `deploy_to_pi.sh:13,80,100-108` | New code lands, `apt-get` fails without internet, `set -e` aborts. The running server keeps serving from memory so nothing looks wrong — until the next power-on starts new code against an unmigrated DB. |
| T3.7 | The pre-migration backup omits the `-wal` file | `deploy_to_pi.sh:100-102` | In WAL mode with a connection open the copy is valid, consistent and **stale**. Measured: 30 live deliveries, **0 recoverable from the backup**. `contracts/migrations.md:239` documents restoring it as *the* rollback procedure. Use `.backup`/`VACUUM INTO`. |
| T3.8 | `rsync --exclude '*.db'` does not exclude `*.db-wal`/`*.db-shm` | `deploy_to_pi.sh:44-45` | A local dev WAL copied next to a different `cricket.db` replays foreign frames on open. Corruption vector. |
| T3.9 | Field layout, batter hand and difficulty are lost on every reload | `src/App.tsx:153,154,158` | The only three session settings not persisted, while `profiles` and `customFields` are. iOS evicting the tab silently reverts to Standard Pace / Right / Medium — and batter hand mirrors the simulated field. |
| T3.10 | No journald cap, no recording pruning, no backup pruning | all 5 units; `health_monitor.py:139-165` | Nothing bounds growth; the disk check is explicitly action-free and logs only to a journal nobody reads at the nets. `E5006 STORAGE_FULL` is documented for exactly this and never emitted. |
| T3.11 | Serve cache headers from `static_server.py` | `scripts/static_server.py` | No `Cache-Control`/`ETag` at all, so browsers apply heuristic freshness (~10% of file age) and the SW's network-first HTML can be served a stale shell without the Pi being asked. `no-cache` for the shell, `immutable` for `/assets/*`. |
| T3.12 | Two field presets contain duplicate zone names | `src/fieldZones.ts:333-344`, `:345-356` | 'Spin Attack' has two "Short Leg", 'T20 Death' two "Cover". The result text is right but the wrong dot animates, and `fielder_involved` can't disambiguate. |
| T3.13 | `engine_params.json` is not actually the single source of truth | `src/gameEngine.ts:349,361,653-658`; `src/fieldZones.ts:38-39,56` | Literals shadow `BAT_HEIGHT`, `CATCH_OPTIMAL_MIN/MAX`. Values coincide today, so parity is green — but retuning `bat_height` 1.0→1.4 in the documented way forks the engines on **115/2500 shots** with no source change. |
| T3.14 | Add the missing parity coverage | `tools/parity/gen_shots.py:29-46` | Four holes: no elevations 61-89 (T1.3 lives there), no NaN/Inf despite a comment saying the runners add them (they don't), one hardcoded field instead of the four shipped presets, `boundary_distance` always 70 despite a declared 50-100 range. |
| T3.15 | Pin the PRNG with golden vectors | `tests/test_engine.py:64-73` | `test_prng_reference_sequence` asserts only `0 ≤ v < 1` and that two identical seeds agree — it would pass against `random.Random`. Real values: `mulberry32(42) → [0.6011037519201636, 0.44829055899754167, 0.8524657934904099, …]`. Assert those, and mirror in vitest. |
| T3.16 | CI doesn't gate what ships | `.github/workflows/ci.yml` | `npm run build` is never run (only `tsc -b`), yet `dist/` is the artefact users receive. No shellcheck, no `systemd-analyze verify` (which would have caught T1.5's `[Service]` bug), no contract check. |
| T3.17 | `engine/test_game_engine.py` (656 lines) has never run | `engine/test_game_engine.py:10`; `pytest.ini:2` | `from game_engine import …` → `ModuleNotFoundError`, and it sits outside `testpaths`. Fix the import and collect it, or delete it — but don't leave 656 lines of assertions that nobody checks. |
| T3.18 | Unknown `difficulty` string: Python degrades, TS produces NaN/throws | `src/gameEngine.ts:725,731` | The union type is erased at runtime and the value comes from a server payload. `NaN` comparison makes **every catch drop**, then `probs.stopped` on `undefined` throws, taking down the local fallback. One-line guard. |
| T3.19 | Boundary `sqrt` has an unguarded discriminant | `game_engine.py:463`; `gameEngine.ts:129` | `R < 8.84·\|sinθ\|` → Python raises `ValueError` (shot lost), TS returns `NaN` (silently poisons every comparison). `simulate_shot` has no boundary range check, unlike `set_field`. Clamp the radicand in both. |
| T3.20 | `simulate_shot` enforces none of its documented input limits | `handlers.py:1344-1349`; `message_router.py:395-479` | `speed 100000 → outcome '6', dist 50,558,606 m`; `elevation -90 → 'dot'`; `difficulty 'god'` accepted. The contract and `CLAUDE.md` both declare the ranges. |

---

## Security — outstanding since 2026-06-27

The Pi login password committed in `61291bc` was redacted from the working tree in
`98d75a1`, but **the historical blobs still resolve in this public repo**:

```
commit 415de69…: CONTAINS a password table row
commit 8eb9c2f…: CONTAINS a password table row
commit 61291bc…: CONTAINS a password table row
```

Both actions from [[2026-07 Hardening Plan]] remain undone after five weeks:
1. **Rotate the Pi password** — assume it is compromised.
2. **Scrub history and force-push** (`git filter-repo`), then invalidate forks/caches.

Note `README.md` is still the stock Vite template, so the "see README → Security"
pointer in `CRICKETRADAR_PLAN.md:653` goes nowhere.

---

## Deliberately not doing

- **App.tsx structural extraction** — still deferred, per the UI preservation rule.
  T1.1/T1.2 are fixed with one `useEffect`, not a refactor.
- **Wiring the frontend to server-side scoring** — a real architectural decision
  (see [[0002 - shotEvent + central processing (deferred)]]), not a bug fix. Until
  it happens, the DB findings below stay latent and low priority: extras runs never
  recorded (`SUM(runs)` is 2 short on a 15-run over), `balls_faced` excluding
  no-balls (SR 185.71 vs a correct 162.50), and the `runs <= 6` CHECK that blocks
  overthrows. Fix them *with* the wiring, not before.
- **Deleting `server/rest_api.py`** — 455 lines with no systemd unit and no client.
  Decide when the persistence question is decided; don't churn it now.
- **`websockets` legacy API migration** — still correctly deferred; the `<16` pin
  holds.
- **Contract regeneration** — `api_types.py`/`api_types.ts` are missing 15 of 32
  message types (including `simulate_shot`/`simulate_result`, the pair actually in
  use, and the load-bearing `seed` field). Worth generating from
  `websocket_protocol.json` eventually — `tools/regen_schema_contract.py` is the
  precedent, and it's why the DB schema is the one contract that is clean. Not
  urgent while layer 3 is dormant.

---

## Needs the Pi to settle

Each of these has one command that resolves it. Worth running on next contact.

1. **Is `db/cricket.db` fresh-migrated or legacy-upgraded?**
   `sqlite3 db/cricket.db '.schema players'` — if `created_at TEXT DEFAULT ''`
   appears, every row created since carries empty timestamps and `delete_profile`
   raises instead of cascading.
2. **Is `StartLimitIntervalSec` actually being ignored?** (T1.5)
   `systemctl show cricket-server -p StartLimitIntervalSec -p StartLimitBurst`
   — expect `10s`/`5`, not `60s`/`5`. Also `journalctl -b | grep "Unknown key name"`.
3. **Does the server fail 226/NAMESPACE without `recordings/`?** (T1.5 trigger)
   `sudo mv ~/cricket-app/recordings{,.bak} && sudo systemctl restart cricket-server && systemctl status cricket-server`
4. **`systemd-analyze verify /etc/systemd/system/cricket-*.service`** — reports every
   ignored directive across all five units at once.
5. **Which Python packages are actually installed?** (T3.2)
   `python3 -c "import serial, websockets; print(serial.__version__, websockets.__version__)"`
6. **Real time-to-ready with no known WiFi** (T3.4) — `systemd-analyze blame | head -20`.
7. **Does `simulate_result` echo the `seed`?** The schema says it does; a grep for
   `"seed"` in `engine/game_engine.py` finds nothing. Send `simulate_shot` with
   `seed: 12345` and print the reply's keys.
8. **Is `extendedMaxVelocity` engaging?** (T0.5) — the highest-stakes unknown in the
   project.

---

## Suggested sequence

1. **T0** — before the nets trip. Without it the trip produces poisoned data.
2. **T1.1** — one line, removes a P0 that invents runs. Do it today.
3. **T1.4 + T1.5 + T1.6 together** — the watchdog is currently able to do more damage
   than the faults it detects. These three must land as one change.
4. **T1.3** — the engine divergence, plus the T3.14 parity elevations that would have
   caught it.
5. **T1.7 – T1.13** — remaining live-path bugs.
6. **T2** — before the detector is wired to the engine, not after.
7. **T3** — opportunistically; each is small and independent.

## Verification for any of this

The existing gates are necessary but demonstrably not sufficient — all green today
with every P0 above present. Any fix should add the test that would have caught it:
parity elevations for T1.3, overhead-frame fixtures at ±5% for T2.1/T2.2, a golden
PRNG vector for T3.15, `systemd-analyze verify` in CI for T1.5.

---

*Written 2026-08-02. Nothing in this plan has been executed.*
