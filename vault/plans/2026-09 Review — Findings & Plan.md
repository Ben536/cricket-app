# 2026-09 Full Review — Findings & Plan

> **Status: EXECUTED (code).** Every software item below has landed on
> `master` with all gates green. What remains is hardware and owner work
> (section "Not done"). Successor to [[2026-08 Review — Findings & Plan]].
>
> Source: full-codebase review, 2026-09-03, single reviewer (Claude Fable
> 5.1) reading every source file, then verifying by running probes and
> writing the tests that pin each finding. The method is now written down as
> the [[Review Playbook]]; the structure of the system as the
> [[Codebase Map]] and the generated [[Codebase Inventory]].

## Method

1. Toolchain from scratch (this Mac had Python 3.9 only and no Node; the Pi
   runs system python3). Baseline: all gates green at `c4a0451`.
2. Read all ~15k lines of source. No subagent summaries.
3. Verified the 2026-08 plan's claims: its ✅ items were in the code; its
   open items were all still open (T1.7-T1.13, all of T2, T3.1-T3.20 except
   T3.13's TS side, the security item).
4. Built `tools/codebase_map.py` so the "three layers, one live" finding and
   the live/dormant message split are re-derived mechanically instead of by
   hand, and `scripts/check_all.sh` so "green" means one command.
5. For each finding: a reproduction, a fix in the smallest coherent unit, and
   a test that fails without the fix. 192 pytest (was 73) and 101 vitest (was
   47, all of which tested contract type guards and none of the live code).
6. **The diff itself was then reviewed adversarially by three independent
   agents** (frontend; server + radar; engines + ops + tools), each told to
   report only what they verified by reading the exact code or running it.
   Their P2 findings are listed in section "Review of this review" and were
   all fixed before the push; the P3s are listed with their status.

Every finding below was read AND run. Where a claim could not be verified
without hardware it is in "Needs the Pi", not in the findings.

---

## Verification of the 2026-08 plan

| Claimed | Checked | Result |
|---|---|---|
| T0.1-T0.4 done | `radar/tlv.py`, `reader.py`, `recorder.py`, 23 tier-0 tests | Present and passing |
| T1.1/T1.1b/T1.2 done | `App.tsx` reset effect, `activeProfileIdRef`, `profileId` on history entries | Present |
| T1.3 done | Python `_find_catchable_intercept` signature; parity 2,274 OK | Present |
| T1.4-T1.6 done | `health_monitor.py`, unit file, 17 tests | Present |
| Everything else "still proposed" | each item's file:line | **All still open** - closed below |
| Parity suite detects the T1.3 class | ran the pre-`44aabf5` engine over the new 3,320-shot set | Detects the new boundary-radicand fix (1 shot). The over-limit-speed gap is latent (all such shots are boundaries) - pinned but not detectable by outcome, see N2 |

---

## New findings (not in the 2026-08 list)

### N1 — The frontend had no tests of the live code · P1 · ✅ FIXED
`src/api/__tests__/types.test.ts` was the entire vitest suite: 47 tests of
`contracts/api_types.ts` type guards, a module nothing in `src/` imports.
The scoring rules, the connection hook, discovery order and the TS engine's
runtime guards had zero coverage; the 2026-08 P0 fixes were verified only by
uncommitted jsdom scripts. **Fix:** `src/scoring.ts` (the scoring law
extracted, behaviour-preserving) + 20 tests; hook tests with a fake
WebSocket; engine and discovery tests. 96 tests.

### N2 — Python trajectory did not clamp speed; TypeScript did · P2 (latent) · ✅ FIXED
`_calculate_trajectory` clamped only elevation. For speeds above 200 km/h
the Pi computed a longer trajectory than the browser. Observable effect on
outcomes: **none found** - every such shot is a boundary in both engines and
the end position is the boundary intersection, which agrees. The landing
point echoed to the client differed. Fixed for consistency; the parity suite
now carries the inputs so a future change cannot widen it.

### N3 — 19 of the 20 recordings on this laptop are mock · P1 (process)
Only `recordings/bowling/2026-07-03_09-09-36.jsonl` has `mock: false`. The
2026-08-02 `both` sessions were all captured with no radar attached and are
worthless for tuning (the UI warned; the files were kept). The
[[Codebase Map]] and the playbook now say so; `replay_jsonl.py` prints a
warning. **Action for the owner:** delete or archive the mock files so they
are never mistaken for data.

### N4 — Host-time jitter in recordings corrupts tracking · P2 · ✅ FIXED
`t_ms` is the host receive time; two frames from one serial read batch are
stamped 4 ms apart (frames at 1627/1631 ms in the fixture). A 0.3 m hop over
4 ms is a 75 m/s teleport - it broke a ghost track's segment checks in the
wrong direction (it survived). **Fix:** when the profile's frame period is
known, tracking uses the hardware frame counter; event times stay on the
host clock for annotation matching.

### N5 — 12 npm advisories, 2 critical, all dev tooling · P2 · ✅ FIXED
Vite 5 / Vitest 1 / jsdom 24 and transitive deps. Nothing ships to the phone.
Upgraded to Vite 8 / Vitest 5; `npm audit` reports 0.

### N6 — The detector's plausibility cap let a 226 km/h ghost through · P2 · ✅ FIXED
`max_plausible_speed_kmh = 250` was unrelated to anything. The engine's own
input limit is 200; the fastest measured bat exit speed is ~175. With the
per-point cos correction (T2.2) a real ghost in the fixture corrected to 226.
**Fix:** cap = 200; plus doppler-vs-displacement consistency (below).

### N7 — `deploy_to_pi.sh` shipped no `pyserial` and could not run offline · P1 · ✅ FIXED
(2026-08 T3.2/T3.6 confirmed.) The Pi was never told to install pyserial;
apt without internet aborted the deploy after the code had landed. Now:
apt is attempted, failure tolerated, and an import check of both packages is
fatal BEFORE anything is restarted.

### N8 — `CLAUDE.md` said the app deploys via Vercel · P3 · ✅ FIXED
The Vercel origin is https and can never reach the Pi's ws://. The field UI
is served by the Pi. The instructions now say so, and carry a "Start here".

### N9 — `saveServerUrl` never appended a port to a `wss://` host · P3 · ✅ FIXED
The check looked for any ':' after index 5, which the scheme itself satisfies.

### N10 — Two wagon-wheel lines in one millisecond shared a React key · P3 · ✅ FIXED

### N11 — `RecordingModal` saved mock clips silently · P2 · ✅ FIXED
The data-gathering modal has warned about `mock: true` since 2026-07; the
short-clip modal did not.

### N12 — The cfg comment says "145 km/h"; the profile delivers 140 · P3 · documented
Derived from `profileCfg`/`frameCfg`: v_max_base 13.0 m/s, x3 with
`extendedMaxVelocity` = 39.0 m/s = 140 km/h. The real recording's ghost
doppler at 25.93 m/s is 2 x 12.97 - the aliasing signature. `radar/profile_cfg.py`
now derives these and the detector uses them.

### N13 — A recording that hit a full card kept saying "recording" · P2 · ✅ FIXED
(2026-08 T0.4 checked free space before starting only.) A failed write now
stops the recording, sets `error` on the session and `last_error` in
`recording_status`; the data-gathering modal distinguishes it from "hit max
duration".

---

## 2026-08 items closed in this review

| Item | Fix | Evidence |
|---|---|---|
| T1.7 recording start race | lifecycle lock over check+assign; ms filenames | `test_concurrent_starts_admit_exactly_one` |
| T1.8 discovery ignores serving host | same-origin candidate first | `config.test.ts` |
| T1.9 wedged client blocks broadcast/reaper | 5s send timeout + evict; `gather` fan-out | `test_connection_manager.py` |
| T1.10 unbounded radar fan-out | bounded queue(5) + drain task per client | `test_stream_backpressure.py` |
| T1.11 reconnect during discovery strands | latch released in `disconnect()`; generation-aware finally | `useServerSimulation.test.tsx` |
| T1.12 404 = connection reset | `format % args`; threaded; cache headers | `test_static_server.py` |
| T1.13 malformed payload kills connection | validation typed and inside try | `test_message_router.py` (28 cases) + e2e |
| T2.1 direction in the wrong plane | `radar/geometry.py` ground plane (x, z) | direction round trip -180..180 within 3° |
| T2.2 speed ~15% low | per-point cos correction | speed within 5% at 60/100/140 |
| T2.3 no radar->pitch transform | `MountCalibration` (yaw, mirror, height), refuses until calibrated; `fit_yaw` | `test_fit_yaw_recovers_the_mount_from_taps` |
| T2.4 no de-aliasing, v_max nowhere | `profile_cfg.py`; unwrap against track velocity | `test_doppler_is_dealiased_against_the_track` |
| T2.5 gate sized from radial speed | predict + residual gate from displacement velocity | (all detection tests) |
| T2.6 bat point steals the track | clustering in (x,y,z,doppler); global assignment | `test_bat_tip_next_to_the_ball_does_not_corrupt_its_speed` |
| T2.7 tests in the wrong frame | overhead fixtures, ±5% / ±3° | `tests/test_detector.py` |
| T2.8 harness can't sweep params | `--set name=value` for every param; `--fit-yaw` | `tools/replay_jsonl.py` |
| T3.1 cfg never deployed | `config/` synced; unit uses repo path; diff warning | deploy script |
| T3.2 deps never installed | see N7 | |
| T3.3 installer installs 3/5 units | all units | `test_every_shipped_unit_is_installed_by_both_scripts` |
| T3.4 network-online waits | dropped from radar/server/ui | `test_units_that_bind_all_interfaces_do_not_wait_for_network_online` |
| T3.5 error reply loses the ball | resolve with local fallback | hook test |
| T3.6 non-atomic deploy | fatal checks before restart | |
| T3.7 backup omits WAL | sqlite online backup API | |
| T3.8 rsync ships -wal/-shm | excluded | |
| T3.9 settings lost on reload | `src/settings.ts` | |
| T3.10 nothing bounded | journald cap (200M) | (recording/backup pruning: not done, policy) |
| T3.11 no cache headers | immutable / no-cache | `test_static_server.py` |
| T3.12 duplicate zone names | suffixed | |
| T3.13 literals shadow params | `BAT_HEIGHT`, `CATCH_OPTIMAL_*`; fieldZones from params | inventory + parity |
| T3.14 parity holes | over-limit speeds, boundary radii, all presets both hands; 3,320 | `parityFields.test.ts` |
| T3.15 PRNG golden vectors | both suites | |
| T3.16 CI gaps | build, tsc for tests, shellcheck, unit lint, systemd-analyze, drift check, Python 3.9+3.11 matrix | `.github/workflows/ci.yml` |
| T3.17 never-run engine test | deleted (+ AUDIT.md) | |
| T3.18 unknown difficulty NaN | degrade to medium | `gameEngine.test.ts` |
| T3.19 unguarded sqrt | radicand clamped, both engines | parity shot at boundary=5 |
| T3.20 simulate_shot unvalidated | typed validation | router tests |

---

## Review of this review (independent, adversarial)

Three agents reviewed `c4a0451..3830e47` with no stake in it. Verdicts:
frontend "safe to push"; server/radar and engines/ops "needs fixes". Every
P2 was fixed (commits `4b386a0` and the follow-up); the engines/ops agent
also fuzzed both engines over 22,000 shots (in-contract: 0 divergences;
adversarial: only the three classes below, all fixed).

| # | Finding | Sev | Status |
|---|---|---|---|
| R1 | `simulate_result` with no `simulation` cleared the timeout then threw → promise never settled, ball lost | P2 | fixed + test |
| R2 | `handle_set_field` did `f["name"]` on a fielder the router accepts without a name → KeyError → E3001 | P2 | fixed + e2e test |
| R3 | Recorder auto-stop timer carried no session identity: fired late (manual stop held the lock) it stopped the NEXT recording, silently | P2 | fixed (timer bound to its session) + test |
| R4 | Detector tracking clock followed a frame-counter reset backwards → live tracks neither associated nor expired for minutes | P2 | fixed (monotonic clock, tracks closed at reset) + test |
| R5 | `RadarSource` restart race (pre-existing, newly exercised): a start during a stop cleared the shared stop event → two dispatch threads, potentially two readers on one tty; old thread's `finally` closed the NEW port | P2 | fixed (per-generation events, thread-local serial handle, join before restart) + test |
| R6 | TS difficulty guard used `in` (prototype chain): `'constructor'` passed and indexed a function → NaN catch probability; 41/6000 fuzz diverged | P2 | fixed (`Object.hasOwn`) + test |
| R7 | Deploy rsynced code BEFORE the deps gate and the build, so a failure left new code on disk for the next restart | P2 | fixed (all gates before the first rsync) + test |
| R8 | Python echoed the raw seed (TS echoes `seed >>> 0`); bool passed Python's number check (TS rejects) | P3 | fixed (parity) + tests |
| R9 | Boundary radius ≤ batter offset: sign-of-zero libm difference flipped dot/4 at exactly 8.84 | P3 | fixed: both engines treat ≤ 8.84 as unset (70) |
| R10 | `normalizeWsUrl` appended the port after a path (`192.168.1.5/` → port 80) | P3 | fixed (URL parser) + tests |
| R11 | Router: `max_duration` NaN became a 1s recording; `10**400` overflowed; non-string `type` raised; NaN echoed as bare `NaN` (invalid JSON) | P3 | fixed + tests |
| R12 | Static server: 404 under `/assets/` sent `immutable`; no HEAD; `%00` traceback | P3 | fixed + tests |
| R13 | Recorder: annotation keys could override `type`/`t_ms`; `_start_time` read unlocked; open() failure left a subscriber-less reader | P3 | fixed |
| R14 | `codebase_map.py` regexes fooled by comments / `.get()` / bracket access; `*.service` glob masked an un-enabled unit | P3 | fixed (AST + comment stripping + name check) |
| R15 | CI `systemd-analyze` grep could pass a real error / fail on runner noise | P3 | anchored on parse-error patterns |
| R16 | `install_services.sh` blocked up to 300s on the radar unit with no radar | P3 | `--no-block` |
| R17 | journald drop-in never applied until reboot; volatile journal | P3 | restart journald; `Storage=persistent` |
| R18 | Scoring extraction gated tallies on the tracker symbol (unreachable combos) | P3 | restored input-flag semantics + tests |
| R19 | Settings written on every drag event | P3 | debounced |
| R20 | `websocket_server.stop()` left evict tasks and a live recording | P3 | drains + finishes the recording |
| — | `discoverServer` keeps probing after `reconnect()` (harmless duplicate probes); 5.5s generation test; `test_websocket_server` fixed port | P3 | probes: noted; port: ephemeral |

---

## Not done, and why

- **Rotate the Pi password and scrub `61291bc` from history.** Owner action:
  history rewrite + force-push + rotating a credential are not a reviewer's
  to take. Confirmed still present (19 lines mentioning the password in
  that commit's diff). Six weeks since 2026-07; twelve since the leak.
- **T0.5 hardware measurements** (is `extendedMaxVelocity` engaging; mount
  height). `test_a_ball_reading_as_static...` pins the failure signature to
  look for: real balls, no events.
- **Mount calibration** (`radar/mount.json`): needs a real `both` session
  with taps, then `replay_jsonl.py --fit-yaw`. Until then the detector
  refuses to emit field-frame angles - deliberately.
- **Wiring the detector to the engine** (`shot_result`): blocked on the two
  above.
- **Frontend <-> server scoring** ([[0002 - shotEvent + central processing (deferred)]]):
  an architecture decision. The dormant DB findings (extras score 0, no-ball
  balls-faced, `runs <= 6` CHECK) travel with it.
- **`server/rest_api.py`**: decide with the above.
- **websockets legacy API**: the Pi's apt package is 10.x; the asyncio API
  needs 13+. Stay on legacy, pin `<16`.
- **Recording/backup pruning**: an ops policy the owner should set (disk
  check warns; nothing deletes data automatically - by design).
- **Self-hosted fonts** (offline first paint before the SW has cached them).
- **`App.tsx` FieldEditor extraction**: needs visual verification; the UI
  preservation rule stands.
- **Delete the 19 mock recordings** (N3) - owner's data.

## Needs the Pi to settle

1. `python3 -c "import websockets, serial; print(websockets.__version__, serial.__version__)"` (the deploy now checks this)
2. `systemd-analyze verify /etc/systemd/system/cricket-*.service` (the deploy now prints this)
3. `systemd-analyze blame | head` with no known WiFi - the ~30s should be gone
4. `sqlite3 db/cricket.db '.schema players'` - fresh vs legacy-upgraded
5. `diff ~/profile_cricket.cfg ~/cricket-app/config/profile_cricket.cfg` (the deploy now warns)
6. `extendedMaxVelocity`: point at a known-speed object at 60-80 km/h; then a
   ball; compare `radial_speed_kmh` from `replay_jsonl.py`
7. Mount height (tape measure) into `radar/mount.json`

---

*Written 2026-09-03. All software items executed the same day; see `git log
c4a0451..` for the commits and their gate results.*
