#!/usr/bin/env python3
"""
Recover the tap lag AND the mount yaw together, from noisy real data.

The operator taps AFTER the delivery, by a lag nobody measured. The detector
also emits ghosts. So neither "which event belongs to which tap" nor "what is
the mount rotation" can be settled on its own - and matching on a guessed
window, then fitting a yaw to whatever it caught, silently fits the ghosts.

Both fall out together if the balls are real and the mount is fixed:

    for a REAL ball,  tap_time - event_time  ~ the operator's lag (constant-ish)
                      sensor_dir - truth_dir ~ the mount yaw   (constant)

so every real pairing lands in the SAME small region of (lag, offset) space,
while ghost pairings scatter uniformly. A dense cluster is therefore evidence
of real detections; no cluster means the events are noise, whatever the
recall figure says.

This is a 2-D vote (a coarse Hough transform). It cannot manufacture signal:
with pure ghosts the densest bin holds about as many votes as chance predicts,
which is reported alongside so the two can be compared.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.detector import DetectorParams  # noqa: E402
from radar.geometry import MountCalibration, wrap_deg  # noqa: E402
from radar.profile_cfg import DEFAULT_PROFILE_PATH, load_profile  # noqa: E402
from radar.tuning import detect, load_recording  # noqa: E402

# The ball precedes the tap. Allow a little negative in case of an early tap.
LAG_MIN_MS, LAG_MAX_MS = -1000, 6000
LAG_BIN_MS = 500
YAW_BIN_DEG = 20


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("recordings", type=Path, nargs="+")
    p.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    p.add_argument("--lag-min", type=int, default=LAG_MIN_MS)
    p.add_argument("--lag-max", type=int, default=LAG_MAX_MS)
    args = p.parse_args()

    overrides = {}
    for item in args.set:
        k, v = item.split("=", 1)
        cur = getattr(DetectorParams(), k)
        overrides[k] = int(v) if isinstance(cur, int) and not isinstance(cur, bool) else float(v)

    profile = load_profile(DEFAULT_PROFILE_PATH) if DEFAULT_PROFILE_PATH.exists() else None
    params = (DetectorParams.from_profile(profile, **overrides) if profile
              else DetectorParams(**overrides))

    pairs = []          # (lag_ms, offset_deg)
    n_taps = n_events = 0
    for path in args.recordings:
        rec = load_recording(path)
        if rec.is_mock:
            continue
        events = detect(rec, params, MountCalibration())
        taps = rec.labelled
        n_taps += len(taps)
        n_events += len(events)
        for tap in taps:
            t = tap.get("t_ms", 0)
            truth = float(tap["direction_deg"])
            for ev in events:
                lag = t - ev.t_end_ms          # positive = tap came after the ball
                if args.lag_min <= lag <= args.lag_max:
                    pairs.append((lag, wrap_deg(ev.direction_sensor_deg - truth)))

    print(f"{n_taps} taps, {n_events} detected events, "
          f"{len(pairs)} candidate pairings in the lag window")
    if not pairs:
        print("No events fall anywhere near the taps - the detector is not seeing the balls.")
        return 1

    # Vote in (lag, offset). Offset wraps, so bin it circularly.
    votes: dict[tuple[int, int], list] = defaultdict(list)
    for lag, off in pairs:
        votes[(int(lag // LAG_BIN_MS), int((off + 180) // YAW_BIN_DEG))].append((lag, off))

    n_lag_bins = (args.lag_max - args.lag_min) / LAG_BIN_MS
    n_yaw_bins = 360 / YAW_BIN_DEG
    expected = len(pairs) / (n_lag_bins * n_yaw_bins)

    ranked = sorted(votes.items(), key=lambda kv: -len(kv[1]))
    print(f"\nExpected votes per bin if this were pure noise: {expected:.1f}")
    print(f"\n{'lag (s)':>12} {'yaw (deg)':>14} {'votes':>6} {'x noise':>8}")
    for (lb, yb), members in ranked[:8]:
        lag_lo = lb * LAG_BIN_MS
        yaw_lo = yb * YAW_BIN_DEG - 180
        ratio = len(members) / expected if expected else 0
        print(f"{lag_lo/1000:>6.1f}-{(lag_lo+LAG_BIN_MS)/1000:<5.1f} "
              f"{yaw_lo:>6.0f}..{yaw_lo+YAW_BIN_DEG:<5.0f} "
              f"{len(members):>6} {ratio:>7.1f}x")

    best_members = ranked[0][1]
    print("\n=== VERDICT ===")
    if len(best_members) < 5 or len(best_members) < 3 * expected:
        print("NO CLUSTER. The pairings are spread evenly, which is what ghosts do.")
        print("The detector is not finding the balls - tune sensitivity before")
        print("trusting any fit from this data.")
        return 2

    lags = [m[0] for m in best_members]
    offs = [m[1] for m in best_members]
    mean_lag = sum(lags) / len(lags)
    s = sum(math.sin(math.radians(o)) for o in offs)
    c = sum(math.cos(math.radians(o)) for o in offs)
    mean_off = math.degrees(math.atan2(s, c))
    spread = math.sqrt(sum(wrap_deg(o - mean_off) ** 2 for o in offs) / len(offs))
    print(f"CLUSTER FOUND: {len(best_members)} pairings, {len(best_members)/expected:.1f}x noise")
    print(f"  operator lag : {mean_lag/1000:.2f}s  (tap comes this long after the ball)")
    print(f"  mount yaw    : {mean_off:+.1f} deg  (spread {spread:.1f} deg)")
    print(f"  covers {len(best_members)} of {n_taps} taps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
