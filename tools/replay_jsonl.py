#!/usr/bin/env python3
"""
Replay a data-gathering JSONL recording through the ball detector and score
it against the wagon-wheel ground truth annotations.

This is how detection gets tuned WITHOUT hardware time: record at the nets
once, then iterate on DetectorParams offline against the same recordings.

Usage:
    python3 tools/replay_jsonl.py recordings/both/2026-07-05_10-00-00.jsonl
    python3 tools/replay_jsonl.py <file> --min-doppler 5 --min-snr 8 --json

Output: every detected ball event (speed, direction, hits), every ground-
truth annotation, and the matching between them (events within the match
window of a tap). Detected direction is in the RADAR frame and ground truth
in the FIELD frame - the report pairs them so the mount-calibration offset
can be fitted once enough matches exist.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.detector import BallDetector, DetectorParams  # noqa: E402
from radar.tlv import RadarFrame, RadarPoint  # noqa: E402

MATCH_WINDOW_MS = 1500  # annotation tap within this window of an event = match


def load_recording(path: Path):
    frames, annotations, meta = [], [], {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # truncated line from a crash
            kind = obj.get("type")
            if kind == "meta":
                meta = obj
            elif kind == "frame":
                t_ms = obj.get("t_ms", obj.get("timestamp_ms", 0))
                frames.append((t_ms, RadarFrame(
                    frame_number=obj.get("frame_number", 0),
                    cpu_time_ms=obj.get("cpu_time_ms", obj.get("timestamp_ms", 0)),
                    num_points=obj.get("num_points", len(obj.get("points", []))),
                    points=[
                        RadarPoint(x=p["x"], y=p["y"], z=p["z"],
                                   doppler=p.get("doppler", p.get("v", 0.0)),
                                   snr=p.get("snr", 0.0), noise=p.get("noise", 0.0))
                        for p in obj.get("points", [])
                    ],
                )))
            elif kind == "annotation":
                annotations.append(obj)
    return meta, frames, annotations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("recording", type=Path)
    parser.add_argument("--min-doppler", type=float, default=None)
    parser.add_argument("--min-snr", type=float, default=None)
    parser.add_argument("--min-hits", type=int, default=None)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    params = DetectorParams()
    if args.min_doppler is not None:
        params.min_doppler = args.min_doppler
    if args.min_snr is not None:
        params.min_snr = args.min_snr
    if args.min_hits is not None:
        params.min_track_hits = args.min_hits

    meta, frames, annotations = load_recording(args.recording)

    if meta.get("mock"):
        print("WARNING: this recording is flagged MOCK - the radar was absent; "
              "frames are fabricated and useless for tuning.", file=sys.stderr)

    detector = BallDetector(params)
    events = []
    for t_ms, frame in frames:
        events.extend(detector.process_frame(frame, t_ms))
    events.extend(detector.flush())

    # Match events to ground-truth taps (nearest in time within the window)
    matches = []
    unmatched_events = list(events)
    for ann in annotations:
        ann_t = ann.get("t_ms", 0)
        best, best_dt = None, MATCH_WINDOW_MS + 1
        for ev in unmatched_events:
            dt = abs(ev.t_end_ms - ann_t)
            if dt < best_dt:
                best, best_dt = ev, dt
        if best is not None and best_dt <= MATCH_WINDOW_MS:
            unmatched_events.remove(best)
            matches.append({"annotation": ann, "event": best.to_dict(), "dt_ms": best_dt})

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
        "matches": matches,
        "unmatched_events": [e.to_dict() for e in unmatched_events],
    }

    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(f"Recording: {report['recording']}  ({report['session_type']}, "
              f"{report['frames']} frames{', MOCK' if report['mock'] else ''})")
        print(f"Events detected: {report['events_detected']}   "
              f"Ground-truth taps: {report['annotations']}   Matched: {report['matched']}")
        if report["recall"] is not None:
            print(f"Recall: {report['recall']:.0%}   "
                  f"Precision: {report['precision'] if report['precision'] is not None else 'n/a'}")
        print()
        for m in matches:
            ev, ann = m["event"], m["annotation"]
            truth = ann.get("direction_deg")
            print(f"  MATCH  tap@{ann['t_ms']}ms"
                  + (f" truth={truth:+.1f}deg" if truth is not None else "")
                  + f"  ->  event {ev['t_start_ms']}-{ev['t_end_ms']}ms "
                    f"speed={ev['speed_kmh']}km/h (radial {ev['radial_speed_kmh']}) "
                    f"dir(radar)={ev['direction_deg']:+.1f}deg hits={ev['n_hits']}")
        for ev in report["unmatched_events"]:
            print(f"  EXTRA  event {ev['t_start_ms']}-{ev['t_end_ms']}ms "
                  f"speed={ev['speed_kmh']}km/h hits={ev['n_hits']} (no tap nearby)")
        missed = report["annotations"] - report["matched"]
        if missed:
            print(f"  MISSED {missed} tap(s) had no detected event within "
                  f"{MATCH_WINDOW_MS}ms - lower --min-doppler/--min-snr/--min-hits?")

    return 0


if __name__ == "__main__":
    sys.exit(main())
