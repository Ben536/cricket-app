# Review Playbook - how to review and refactor this codebase

Every new model gets the same brief: understand the codebase rigorously, find
what is wrong, make it better. Three full reviews have now been done
(2026-07, 2026-08, 2026-09). Each rediscovered the same facts by hand and
each found P0s that every gate had passed. This page is the method distilled,
so the next reviewer starts where the last one finished instead of where the
first one started.

---

## 0. Ground rules

- **A finding needs a reproduction.** Read code to form a hypothesis; then
  run something that shows the failure with real numbers (a script, a test, a
  probe against the running server). "This looks wrong" is a note, not a
  finding. Every P0 in the 2026-08 review came with a repro; every one that
  did not survive verification was dropped.
- **Grade on the live path.** Use the three-layers table in [[Codebase Map]].
  A bug the phone cannot reach is real but latent; say so.
- **Preserve the UI.** No visual/UX changes unless the task is a UX change.
  Pure-logic extraction from `App.tsx` is fine (scoring.ts was one); moving
  JSX around without a way to verify it visually is not.
- **Both engines or neither.** Any engine behaviour change lands in
  `engine/game_engine.py` AND `src/gameEngine.ts`, with parity re-run.
- **Never rewrite history or push secrets.** The 2026-06 password leak is
  still in history; scrubbing it is an owner action (force-push), not a
  reviewer action.
- **Commit in logical units with the gates green at each commit.** The
  message says what was wrong, what it did to the user, and how it is
  verified - future reviewers read `git log` first.

---

## 1. Bootstrap (15 minutes)

```bash
# Toolchain (this Mac has no Homebrew: gh, node, uv live in ~/.local)
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
uv pip install --python .venv/bin/python shellcheck-py     # optional: local shellcheck
npm ci

# Baseline - every gate, one command. Do NOT start reviewing until this is green.
npm run check            # = scripts/check_all.sh

# Ground truth about the shape of the repo
python3 tools/codebase_map.py --write   # refreshes vault/architecture/Codebase Inventory.md
```

Then read, in this order:
1. `git log --oneline -60` - what the last reviewer did and why.
2. [[Codebase Map]] (narrative) and [[Codebase Inventory]] (generated).
3. The previous review's plan (`vault/plans/<latest> Review ...md`) - its
   "not done" and "needs the Pi" sections are your starting backlog.
4. `CLAUDE.md` - coordinate system, engine spec, invariants.
5. The code itself, in live-path order: `src/App.tsx`, `src/hooks/`,
   `src/api/config.ts`, `src/scoring.ts`, both engines, `server/`,
   `radar/`, `scripts/`, then `db/`, `tools/`, `tests/`.

Budget the reading. The whole codebase is ~15k lines of source; a model can
read all of it. Do so - summaries by a subagent are how the 2026-07 review
missed a test hook shipping in production.

---

## 2. Verify the previous reviewer's claims

Before hunting new bugs, check the last plan's "DONE" items are actually done
in the code (they were, in 2026-09) and that its "not done" items are still
open (most were). This costs an hour and calibrates trust in the notes.

Then run the mechanical cross-checks that expose the shape of the system:

```bash
python3 tools/codebase_map.py            # live vs dormant message types, params, tests
git log --all --diff-filter=A --name-only | sort -u | grep -iE 'secret|password|\.env'
python3 - <<'EOF'                        # which recordings are REAL (mock: false)?
import json,glob
for f in sorted(glob.glob('recordings/*/*.jsonl')):
    m=json.loads(open(f).readline()); print(('REAL' if not m.get('mock') else 'mock'), f)
EOF
```

---

## 3. Subsystem checklists (the adversarial questions)

Ask each of these and answer with evidence. They are the questions that found
the P0s.

**Frontend (live)**
- What state is shared across batters and what is per-batter? Can a shared
  write land after an `await` while the batter changed?
- What happens to a ball on EACH failure path of `simulateAsync` (timeout,
  disconnect, error reply, exception in the local engine)? Is any path lossy?
- What persists across a reload and what silently reverts?
- What does discovery try first when the page was served by the Pi?
- Do two fielders ever share a name the engine reports back?
- Is there a vitest test that exercises the live logic, or only types?

**Engines**
- Run the parity suite. Then run the PREVIOUS commit's Python engine over the
  CURRENT shot set (see 2026-09 for the script) - does the suite actually
  detect the last fix?
- Grep both engines for numeric literals; is any one shadowing a param?
- Feed NaN/Inf/±huge/strings through every entry point of both engines.
- Does either engine do something the other does not (a clamp, a test hook,
  a different branch order)?

**Server**
- Send a malformed payload of every message type (null, wrong type, bool,
  huge list). Does the connection survive? Does the session?
- Make one client's socket never drain. Do the others still get heartbeats?
  Does the reaper still run?
- Start two recordings at once from two connections.
- Stream radar to a client and stop reading on its side; count tasks.

**Radar**
- Which axis is vertical under the overhead mount? Check the sign statistics
  of the real recording, not the docs.
- Is v_max derived from the cfg or assumed? Is `extendedMaxVelocity` proven
  on hardware?
- Build a synthetic overhead ball crossing and require speed ±5%, direction
  ±3°. Then feed the real static-clutter fixture: zero events.
- Does the recorder survive a full card honestly (stop + report), or lie?
- Which clock does tracking use: host receive time or the frame counter?

**Ops**
- Does the deploy install what the units need? Does the unit file reference
  files the deploy actually syncs?
- Every directive in every unit: valid for its section? (`tests/test_systemd_units.py`)
- What does a fresh card do with no known WiFi? Time it.
- Does the health monitor's probe distinguish "bound" from "serving"?
- What is bounded (journal, recordings, backups) and what grows forever?

**Database (dormant)**
- Is every migration idempotent? Run it twice on the Pi's schema shape.
- Do the SQL summaries agree with the phone's scoring rules (extras, no-balls)?

---

## 4. Dynamic probes that have worked

Patterns to reuse (all are in the test suites now):
- **Fake WebSocket + React `act`** to drive `useServerSimulation` through
  discovery/reconnect/error (`src/__tests__/useServerSimulation.test.tsx`).
- **Old engine vs new shots**: `git show HEAD~1:engine/game_engine.py` into a
  temp module, run it over `tools/parity/shots.json`, count divergences
  against `results_ts.json`.
- **Fake sockets that never drain** (`tests/test_connection_manager.py`).
- **A fake streamer + slow connection manager** to count queued frames
  (`tests/test_stream_backpressure.py`).
- **Barrier-synchronised threads** against the recorder (`tests/test_radar.py`).
- **A file object whose write() raises ENOSPC** injected into the recorder.
- **`SIGSTOP` the server**, then run the health probe (2026-08).
- **Synthetic overhead-frame ball** with TI doppler sign convention
  (`tests/test_detector.py::overhead_ball`), including wrapped doppler.
- **Real-clutter fixture** (`tests/fixtures/real_static_clutter.jsonl`): the
  only real radar data in the repo; every detector change must keep it at
  zero events.

---

## 5. Recording findings

One page per review in `vault/plans/`, named `<YYYY-MM> Review - Findings &
Plan.md`, in this shape:

```
## Method            what was read, what was run, how findings were verified
## Verification      the previous plan's DONE/NOT-DONE items, checked
## Findings          T<tier>.<n> - title - P<severity> - file:line
                     what happens, with numbers; why it matters on the live path;
                     the repro; the fix; the test that would have caught it
## Executed          what landed, by commit, with the gate results
## Not done          and WHY (blocked on hardware / a decision / out of scope)
## Needs the Pi      one command each
```

Keep the old plans; they are the project's memory. Mark items ✅ inline as
they land. Update [[Home]] status and, if the shape changed, [[Codebase Map]].

---

## 5b. Have the diff reviewed before pushing

Once the work is committed, spawn independent reviewers over the diff
(`git diff <baseline>..HEAD`), one per area (frontend; server + radar;
engines + ops + tools), with the same reproduction bar and the instruction
to modify nothing. In 2026-09 this found seven P2s the author had missed -
among them a stale timer stopping the wrong recording, a tracking clock that
went backwards on a radar restart, and a prototype-chain hole in an input
guard. Fix the P2s, list the P3s with their status in the review page, and
only then push. Budget for the reviewers being cut off by rate limits:
resume them with a message rather than restarting.

## 6. Hand-off checklist

- [ ] `npm run check` green; CI green on the pushed commit
- [ ] `python3 tools/codebase_map.py --write` committed
- [ ] [[Codebase Map]] updated if any file's responsibility or an invariant changed
- [ ] This playbook updated with any new probe or question that found something
- [ ] The review page's "Not done" and "Needs the Pi" sections are honest
- [ ] `vault/Home.md` status updated
- [ ] `CLAUDE.md` "Start here" still true (toolchain, gates, counts)
- [ ] No secrets in the diff (`git diff --cached | grep -iE 'password|secret|token'`)
