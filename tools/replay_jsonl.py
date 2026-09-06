#!/usr/bin/env python3
"""
Replay a data-gathering JSONL recording through the ball detector and score
it against the wagon-wheel ground truth annotations.

This is how detection gets tuned WITHOUT hardware time: record at the nets
once, then iterate on DetectorParams offline against the same recordings.

Usage:
    python3 tools/replay_jsonl.py recordings/both/2026-07-05_10-00-00.jsonl
    python3 tools/replay_jsonl.py <file> --set min_doppler=5 --set min_snr=8 --json
    python3 tools/replay_jsonl.py <file> --fit-yaw       # fit radar/mount.json from taps
    python3 tools/replay_jsonl.py <file> --no-profile    # ignore config/profile_cricket.cfg

Every DetectorParams field can be swept with --set name=value. The radar
profile (config/profile_cricket.cfg) supplies the unambiguous velocity for
de-aliasing unless --no-profile is given; radar/mount.json supplies the
mount calibration, so field-frame directions appear once it is calibrated.

Output: every detected ball event (speed, direction, hits), every ground-
truth annotation, and the matching between them (events within the match
window of a tap). Detected direction is reported in the SENSOR frame and,
when calibrated, in the FIELD frame next to the tap's truth. --fit-yaw uses
the matches to fit the mount yaw/mirror and prints the values to put in
radar/mount.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.detector import DetectorParams  # noqa: E402
from radar.geometry import DEFAULT_MOUNT_PATH, MountCalibration  # noqa: E402
from radar.profile_cfg import DEFAULT_PROFILE_PATH, load_profile  # noqa: E402
from radar.tuning import (  # noqa: E402
    MATCH_WINDOW_MS,
    detect,
    fit_from_matches,
    load_recording,
    match_events,
)


def parse_overrides(items: list[str]) -> dict:
    """--set name=value, typed from the dataclass field's default."""
    types = {f.name: f.type for f in fields(DetectorParams)}
    defaults = DetectorParams()
    out = {}
    for item in items:
        if "=" not in item:
            sys.exit(f"--set expects name=value, got {item!r}")
        name, value = item.split("=", 1)
        if name not in types:
            sys.exit(f"unknown parameter {name!r}; choose from: {', '.join(sorted(types))}")
        current = getattr(defaults, name)
        if isinstance(current, bool):
            out[name] = value.lower() in ("1", "true", "yes")
        elif isinstance(current, int):
            out[name] = int(value)
        elif value.lower() in ("none", "null"):
            out[name] = None
        else:
            out[name] = float(value)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("recording", type=Path)
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                        help="override any DetectorParams field (repeatable)")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH,
                        help="radar .cfg to derive v_max from")
    parser.add_argument("--no-profile", action="store_true", help="do not de-alias against the profile")
    parser.add_argument("--mount", type=Path, default=DEFAULT_MOUNT_PATH, help="mount calibration json")
    parser.add_argument("--fit-yaw", action="store_true",
                        help="fit mount yaw/mirror from matched taps and print them")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    # Back-compat spellings
    parser.add_argument("--min-doppler", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--min-snr", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--min-hits", type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()

    overrides = parse_overrides(args.set)
    if args.min_doppler is not None:
        overrides["min_doppler"] = args.min_doppler
    if args.min_snr is not None:
        overrides["min_snr"] = args.min_snr
    if args.min_hits is not None:
        overrides["min_track_hits"] = args.min_hits

    profile = None
    if not args.no_profile and args.profile.exists():
        profile = load_profile(args.profile)
        params = DetectorParams.from_profile(profile, **overrides)
    else:
        params = DetectorParams(**overrides)

    calibration = MountCalibration.load(args.mount) if args.mount.exists() else MountCalibration()

    rec = load_recording(args.recording)
    meta, frames, annotations = rec.meta, rec.frames, rec.annotations

    if meta.get("mock"):
        print("WARNING: this recording is flagged MOCK - the radar was absent; "
              "frames are fabricated and useless for tuning.", file=sys.stderr)

    events = detect(rec, params, calibration)
    scoring = match_events(events, annotations)
    matches = [{"annotation": m.annotation, "event": m.event.to_dict(), "dt_ms": m.dt_ms}
               for m in scoring.matches]
    unmatched_events = scoring.unmatched_events

    pairs = [m for m in scoring.matches
             if isinstance(m.annotation.get("direction_deg"), (int, float))]
    fit = fit_from_matches(scoring.matches) if args.fit_yaw else None

    report = {
        "recording": str(args.recording),
        "session_type": meta.get("session_type"),
        "mock": meta.get("mock", False),
        "frames": len(frames),
        "events_detected": len(events),
        "annotations": len(annotations),
        "matched": len(matches),
        "recall": round(len(matches) / len(annotations), 3) if annotations else None,
        "precision": round(len(matches) / len(events), 3) if events else None,
        "params": vars(params),
        "profile": profile.summary() if profile else None,
        "calibration": vars(calibration),
        "fit": None if fit is None else {"yaw_deg": round(fit[0], 1), "mirror": fit[1], "rms_deg": round(fit[2], 1), "pairs": len(pairs)},
        "matches": matches,
        "unmatched_events": [e.to_dict() for e in unmatched_events],
    }

    if args.json:
        print(json.dumps(report, indent=1))
        return 0

    print(f"Recording: {report['recording']}  ({report['session_type']}, "
          f"{report['frames']} frames{', MOCK' if report['mock'] else ''})")
    if profile:
        print(f"Profile:   {profile.summary()}")
    print(f"Mount:     {'CALIBRATED' if calibration.calibrated else 'NOT calibrated'} "
          f"yaw={calibration.yaw_deg:+.1f} mirror={calibration.mirror} height={calibration.mount_height_m}m")
    print(f"Events detected: {report['events_detected']}   "
          f"Ground-truth taps: {report['annotations']}   Matched: {report['matched']}")
    if report["recall"] is not None:
        print(f"Recall: {report['recall']:.0%}   "
              f"Precision: {report['precision'] if report['precision'] is not None else 'n/a'}")
    print()
    for m in matches:
        ev, ann = m["event"], m["annotation"]
        truth = ann.get("direction_deg")
        line = f"  MATCH  tap@{ann['t_ms']}ms"
        if truth is not None:
            line += f" truth={truth:+.1f}deg"
        line += (f"  ->  event {ev['t_start_ms']}-{ev['t_end_ms']}ms "
                 f"speed={ev['speed_kmh']}km/h (doppler {ev['speed_doppler_kmh']}, track {ev['speed_track_kmh']}"
                 f"{', ALIASED' if ev['aliased'] else ''}) "
                 f"dir(sensor)={ev['direction_sensor_deg']:+.1f}deg")
        if ev["field_direction_deg"] is not None:
            line += f" dir(field)={ev['field_direction_deg']:+.1f}deg"
        line += f" elev={ev['vertical_angle_deg']:+.1f}deg hits={ev['n_hits']}"
        print(line)
    for ev in report["unmatched_events"]:
        print(f"  EXTRA  event {ev['t_start_ms']}-{ev['t_end_ms']}ms "
              f"speed={ev['speed_kmh']}km/h dir(sensor)={ev['direction_sensor_deg']:+.1f}deg "
              f"hits={ev['n_hits']} (no tap nearby)")
    missed = report["annotations"] - report["matched"]
    if missed:
        print(f"  MISSED {missed} tap(s) had no detected event within "
              f"{MATCH_WINDOW_MS}ms - try --set min_doppler=... / min_snr=... / min_track_hits=...")
    if args.fit_yaw:
        print()
        if fit is None:
            print(f"  FIT: need at least 2 matched taps with direction_deg (have {len(pairs)})")
        else:
            print(f"  FIT over {len(pairs)} taps: yaw_deg={fit[0]:+.1f} mirror={fit[1]} rms={fit[2]:.1f}deg")
            print(f"  -> set these in {DEFAULT_MOUNT_PATH.relative_to(Path.cwd()) if DEFAULT_MOUNT_PATH.is_relative_to(Path.cwd()) else DEFAULT_MOUNT_PATH}"
                  f" with \"calibrated\": true and the measured mount_height_m")

    return 0


if __name__ == "__main__":
    sys.exit(main())
