# Data Gathering Mode

**Goal:** capture raw radar at the nets, **labelled with ground truth**, to characterise the ball signature and tune detection + kinematics + the game engine. This is [[Development Roadmap|Phase 1]].

## Why the existing recorder isn't enough
`radar/recorder.py` + `RecordingModal` already record raw TLV frames by session type (bowling/batting/both) — good. Gaps for tuning:
1. **15s cap** — a net session is minutes and many balls.
2. **No per-ball labels** — without ground truth you can't tell which frames are a ball, or tune kinematics against a known outcome.
3. **Save only on stop** — a crash/disconnect loses the whole session.

## Design (v1 — what we're building)
Extend the existing recorder/handlers (keeps the radar single-owned by cricket-server). No rewrite of App.tsx.

### Capture
- **Configurable duration**, default long (e.g. 300s) with a hard safety cap; can stop manually.
- **Crash-safe**: append each frame to a `.jsonl` file as it arrives (one JSON object per line). Survives crashes/disconnects.
- Keep SNR + noise per point (already parsed).
- Still tagged by **session type** (bowling / batting / both) per the existing test plan.

### The three capture types (per the test plan)
1. **bowling** — ball goes by, **no batsman** → pure ball signature.
2. **batting** — batsman plays shadow shots, **no ball** → bat/body signature.
3. **both** — ball bowled **and hit** → real shot; needs direction ground truth.

### Labelling (the key addition)
- Operator taps when a ball happens. A tap writes an **annotation** aligned to the recording clock:
  ```
  {type:"annotation", t_ms, label?, direction_deg?, zone?, note?, shot_type?}
  ```
- For **both** (hit ball): capture the **direction the ball went out** — this is the
  ground truth for the horizontal angle the system must later derive from radar.
  UI = a **tappable wagon-wheel / field diagram**; the tap → `direction_deg` (+ normalized x/y, optional zone).
- ⚠️ **Sign convention**: `direction_deg` is 0° = toward bowler, **+90 = leg**, −90 = off
  (RH batter), matching the field coordinate system (+X = leg). But the game engine's
  simulate `angle` input is **+off / −leg** (see CLAUDE.md) — **flip the sign** when
  feeding this ground truth into engine tuning, or the wagon wheel mirrors.
- For **bowling**/**batting**: a single timing mark per ball/swing is enough (no direction).
- Annotations live in the same `.jsonl` stream (interleaved with frames) → trivially aligned in time.
- Backend stores the annotation payload verbatim (free-form), so the UI controls what ground truth is captured.

### Output
One `.jsonl` per session under `recordings/<type>/<timestamp>.jsonl`:
- line 0: `{type:"meta", session_type, start_time, mock, ...}`
- then `{type:"frame", ...}` and `{type:"annotation", ...}` interleaved
- last line: `{type:"end", duration_seconds, frame_count, annotation_count}` (absent ⇒ session crashed; listing recovers counts by full scan).
This is the tuning dataset for Phase 2.

⚠️ **`mock: true` means the radar was NOT detected when recording started** — the
frames are fabricated test data (a sine-wave point), worthless for tuning. The serial
port is opened up-front so `recording_started` / `recording_status` carry the same
`mock` flag and the UI shows a red warning. Never tune against a mock file.

### Field UX (phone)
Big **Start/Stop**, session-type selector, and a row of **outcome buttons** that drop a labelled mark. Live counters: elapsed, frame count, marks. Served from the Pi UI (offline).

## Protocol additions (WebSocket)
- `start_recording` gains `max_duration` (and implicit jsonl mode).
- New `add_annotation` message → `recorder.add_annotation(label)`.
- `recording_status` reports mark count.

## Field session template (copy to `sessions/YYYY-MM-DD.md`)
```
# Net session YYYY-MM-DD
- Setup: radar height/angle, net, who batted/bowled
- Recordings: <files> (type, duration, #balls)
- Observations: what the ball looked like; ball vs bat; noise; misses
- Next: what to change in detection/kinematics
```

## Open choices
- Label granularity today: just **timing marks**, or full **outcome labels**? (Building outcome labels; you can ignore them and just tap one button if rushed.)
- Whether to also log a "bowled" mark separate from "hit". (v1: single mark at contact.)
