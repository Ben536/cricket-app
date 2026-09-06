#!/usr/bin/env python3
"""
Sweep DetectorParams against wagon-wheel ground truth and report the best.

`replay_jsonl.py` scores ONE parameter set. There are two dozen tunable
fields, so hand-tuning by repeated --set is impractical; this sweeps them and
ranks the results.

Usage:
    python3 tools/tune_detector.py recordings/both/*.jsonl
    python3 tools/tune_detector.py <files> --grid full
    python3 tools/tune_detector.py <files> --min-recall 0.8   # best precision subject to recall
    python3 tools/tune_detector.py <files> --json

The parameters split into two families that pull against each other:

  SENSITIVITY   min_doppler, min_snr, min_track_hits, cluster_eps,
                max_ball_cluster_points, max_coast_frames
                -> loosen to raise RECALL (see more balls)

  GHOST REJECT  min_motion_ratio, min_straightness, min_moving_fraction,
                min_doppler_consistency, max_speed_disagreement,
                min_doppler_samples
                -> tighten to raise PRECISION (fewer false balls)

The sweep exists to find where that trade sits on YOUR data. For mount
calibration prefer precision: a clean set of true pairs fits a better yaw
than a larger set containing ghosts, which is what --min-recall is for.

Scoring is pooled across all the files given, so a winner has to work on more
than one capture rather than overfitting a single lucky recording.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, replace
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.detector import DetectorParams  # noqa: E402
from radar.geometry import DEFAULT_MOUNT_PATH, MountCalibration  # noqa: E402
from radar.profile_cfg import DEFAULT_PROFILE_PATH, load_profile  # noqa: E402
from radar.tuning import (  # noqa: E402
    Recording,
    detect,
    direction_residual,
    fit_from_matches,
    load_recording,
    match_events,
)

# Candidate values per parameter. Curated rather than linspace: these are the
# ranges that are physically meaningful for a cricket ball under an overhead
# mount. Every key is checked against DetectorParams at startup, so adding or
# renaming a field surfaces here instead of being silently un-swept.
SWEEP: dict[str, list] = {
    # --- sensitivity -------------------------------------------------------
    "min_doppler": [2.0, 3.0, 4.0, 5.0, 6.0, 8.0],
    "min_snr": [3.0, 4.0, 6.0, 8.0, 10.0],
    "min_track_hits": [2, 3, 4],
    "cluster_eps": [0.3, 0.45, 0.6, 0.8, 1.0],
    "max_ball_cluster_points": [2, 3, 4, 6, 8],
    "max_coast_frames": [1, 2, 3],
    "association_base": [0.3, 0.5, 0.8],
    "association_slack": [0.3, 0.5, 0.8],
    "max_bridge_streak": [1, 2, 3],
    # --- ghost rejection ---------------------------------------------------
    "min_motion_ratio": [0.15, 0.3, 0.45, 0.6],
    "min_straightness": [0.5, 0.7, 0.85, 0.92],
    "min_moving_fraction": [0.4, 0.6, 0.8],
    "min_doppler_samples": [1, 2, 3],
    "min_doppler_consistency": [0.3, 0.5, 0.7],
    "max_speed_disagreement": [0.15, 0.25, 0.4],
    "min_cos_theta": [0.15, 0.25, 0.4],
}

# The four that move the numbers most, for the bounded cross-product.
# 6 * 5 * 3 * 4 = 360 combinations.
FULL_GRID_KEYS = ["min_doppler", "min_snr", "min_track_hits", "min_motion_ratio"]


@dataclass
class Result:
    params: DetectorParams
    matched: int
    taps: int
    events: int
    fit: Optional[tuple]      # (yaw_deg, mirror, rms_deg)
    rms_deg: Optional[float]
    elapsed_s: float

    @property
    def recall(self) -> float:
        return self.matched / self.taps if self.taps else 0.0

    @property
    def precision(self) -> float:
        return self.matched / self.events if self.events else 0.0

    @property
    def f1(self) -> float:
        r, p = self.recall, self.precision
        return 2 * r * p / (r + p) if (r + p) else 0.0

    @property
    def ghosts(self) -> int:
        return self.events - self.matched


def evaluate(recs: list[Recording], params: DetectorParams,
             calibration: MountCalibration) -> Result:
    """Pooled score over every recording."""
    t0 = time.perf_counter()
    matched = taps = events = 0
    all_matches = []
    for rec in recs:
        truth = rec.labelled
        found = detect(rec, params, calibration)
        sc = match_events(found, truth)
        matched += len(sc.matches)
        taps += sc.n_taps
        events += sc.n_events
        all_matches.extend(sc.matches)
    fit = fit_from_matches(all_matches)
    return Result(
        params=params, matched=matched, taps=taps, events=events,
        fit=fit, rms_deg=direction_residual(all_matches, fit),
        elapsed_s=time.perf_counter() - t0,
    )


def rank_key(r: Result, min_recall: Optional[float]):
    """Sort key, best first.

    Without --min-recall: F1, then a tighter direction fit, then fewer ghosts.
    With it: recall becomes a gate and precision the objective, because a
    calibration fit wants clean pairs more than it wants many pairs.
    """
    rms = r.rms_deg if r.rms_deg is not None else 999.0
    if min_recall is None:
        return (-r.f1, rms, r.ghosts)
    return (0 if r.recall >= min_recall else 1, -r.precision, rms, -r.recall)


def changed_fields(params: DetectorParams, base: DetectorParams) -> dict:
    return {f.name: getattr(params, f.name)
            for f in dataclass_fields(DetectorParams)
            if getattr(params, f.name) != getattr(base, f.name)}


def set_flags(params: DetectorParams, base: DetectorParams) -> str:
    diff = changed_fields(params, base)
    return " ".join(f"--set {k}={v}" for k, v in diff.items()) or "(defaults)"


def coordinate_sweep(recs, base_params, calibration, min_recall, verbose):
    """Vary one parameter at a time from the baseline, keep what helps.

    Two passes: the first finds each parameter's best value independently,
    the second re-sweeps on top of the accumulated winner because these
    parameters interact (loosening min_doppler changes which min_snr is
    best). Far cheaper than a cross product and, on this kind of scoring
    surface, close to as good.
    """
    results = []
    current = base_params
    best = evaluate(recs, current, calibration)
    results.append(best)

    for pass_no in (1, 2):
        improved = False
        for name, values in SWEEP.items():
            for value in values:
                if getattr(current, name) == value:
                    continue
                cand_params = replace(current, **{name: value})
                r = evaluate(recs, cand_params, calibration)
                results.append(r)
                if rank_key(r, min_recall) < rank_key(best, min_recall):
                    best, current, improved = r, cand_params, True
                    if verbose:
                        print(f"  pass {pass_no}: {name}={value} -> "
                              f"F1 {r.f1:.3f} recall {r.recall:.0%} "
                              f"precision {r.precision:.0%}", file=sys.stderr)
        if not improved:
            break
    return results, best


def full_grid(recs, base_params, calibration, min_recall, verbose):
    """Bounded cross-product of the four most influential parameters."""
    from itertools import product

    combos = list(product(*(SWEEP[k] for k in FULL_GRID_KEYS)))
    results = []
    for i, values in enumerate(combos, 1):
        cand = replace(base_params, **dict(zip(FULL_GRID_KEYS, values)))
        results.append(evaluate(recs, cand, calibration))
        if verbose and i % 25 == 0:
            print(f"  {i}/{len(combos)} combinations", file=sys.stderr)
    best = min(results, key=lambda r: rank_key(r, min_recall))
    return results, best


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("recordings", type=Path, nargs="+")
    p.add_argument("--grid", choices=["quick", "full"], default="quick",
                   help="quick = coordinate sweep (default); full = 360-combo cross product")
    p.add_argument("--min-recall", type=float, default=None, metavar="R",
                   help="require recall >= R, then maximise precision (use for calibration)")
    p.add_argument("--top", type=int, default=8, help="how many results to show")
    p.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    p.add_argument("--no-profile", action="store_true")
    p.add_argument("--mount", type=Path, default=DEFAULT_MOUNT_PATH)
    p.add_argument("--allow-mock", action="store_true",
                   help="tune on fabricated frames (meaningless; for testing this tool only)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    verbose = not args.json

    # --- load ---------------------------------------------------------------
    recs, skipped = [], []
    for path in args.recordings:
        rec = load_recording(path)
        if rec.is_mock and not args.allow_mock:
            skipped.append(path)
            continue
        recs.append(rec)

    partial = [r for r in recs if r.partial_mock]
    if partial:
        print(f"WARNING: {len(partial)} recording(s) lost the radar part-way through. Those "
              f"stretches are fabricated and will drag the score down:", file=sys.stderr)
        for r in partial:
            pct = 100.0 * r.mock_frame_count / max(1, len(r.frames))
            print(f"  {r.path}: {r.mock_frame_count}/{len(r.frames)} frames ({pct:.0f}%) fabricated",
                  file=sys.stderr)

    if skipped:
        print(f"Skipped {len(skipped)} MOCK recording(s) - the radar was absent and the "
              f"frames are fabricated, so tuning on them is meaningless:", file=sys.stderr)
        for s in skipped[:5]:
            print(f"  {s}", file=sys.stderr)
        if len(skipped) > 5:
            print(f"  ... and {len(skipped) - 5} more", file=sys.stderr)

    if not recs:
        print("\nNo real recordings to tune on. Capture a 'both' session with a wagon-wheel "
              "tap on every ball, then run this against it.", file=sys.stderr)
        return 2

    total_taps = sum(len(r.labelled) for r in recs)
    if total_taps < 2:
        print(f"\nOnly {total_taps} labelled tap(s) across {len(recs)} recording(s). Tuning needs "
              f"ground truth: record a 'both' session and tap the wheel for every ball.",
              file=sys.stderr)
        return 2

    # Guard against SWEEP drifting from the dataclass.
    known = {f.name for f in dataclass_fields(DetectorParams)}
    unknown = set(SWEEP) - known
    if unknown:
        print(f"SWEEP references fields that no longer exist on DetectorParams: "
              f"{sorted(unknown)}", file=sys.stderr)
        return 3

    profile = None
    if not args.no_profile and args.profile.exists():
        profile = load_profile(args.profile)
        base_params = DetectorParams.from_profile(profile)
    else:
        base_params = DetectorParams()
    calibration = MountCalibration.load(args.mount) if args.mount.exists() else MountCalibration()

    if verbose:
        print(f"Tuning on {len(recs)} recording(s), "
              f"{sum(len(r.frames) for r in recs)} frames, {total_taps} labelled taps")
        if profile:
            print(f"Profile: {profile.summary()}")
        print(f"Grid: {args.grid}"
              + (f"   constraint: recall >= {args.min_recall:.0%}" if args.min_recall else ""))
        print()

    # --- sweep --------------------------------------------------------------
    t0 = time.perf_counter()
    runner = coordinate_sweep if args.grid == "quick" else full_grid
    results, best = runner(recs, base_params, calibration, args.min_recall, verbose)
    elapsed = time.perf_counter() - t0

    baseline = evaluate(recs, base_params, calibration)
    results.sort(key=lambda r: rank_key(r, args.min_recall))
    top = results[: args.top]

    report = {
        "recordings": [str(r.path) for r in recs],
        "skipped_mock": [str(s) for s in skipped],
        "partial_mock": {str(r.path): r.mock_frame_count for r in partial},
        "frames": sum(len(r.frames) for r in recs),
        "taps": total_taps,
        "grid": args.grid,
        "combinations_evaluated": len(results),
        "elapsed_s": round(elapsed, 1),
        "min_recall": args.min_recall,
        "baseline": {
            "recall": round(baseline.recall, 3), "precision": round(baseline.precision, 3),
            "f1": round(baseline.f1, 3),
            "rms_deg": None if baseline.rms_deg is None else round(baseline.rms_deg, 1),
        },
        "best": {
            "recall": round(best.recall, 3), "precision": round(best.precision, 3),
            "f1": round(best.f1, 3),
            "rms_deg": None if best.rms_deg is None else round(best.rms_deg, 1),
            "matched": best.matched, "taps": best.taps, "events": best.events,
            "changed": changed_fields(best.params, base_params),
            "set_flags": set_flags(best.params, base_params),
            "fit": None if best.fit is None else {
                "yaw_deg": round(best.fit[0], 1), "mirror": best.fit[1],
                "rms_deg": round(best.fit[2], 1)},
        },
        "top": [
            {"recall": round(r.recall, 3), "precision": round(r.precision, 3),
             "f1": round(r.f1, 3),
             "rms_deg": None if r.rms_deg is None else round(r.rms_deg, 1),
             "changed": changed_fields(r.params, base_params)}
            for r in top
        ],
    }

    if args.json:
        print(json.dumps(report, indent=1, default=str))
        return 0

    print(f"Evaluated {len(results)} parameter sets in {elapsed:.1f}s\n")
    print(f"{'recall':>7} {'prec':>6} {'F1':>6} {'rms':>7}  changed from defaults")
    print(f"{'-'*7} {'-'*6} {'-'*6} {'-'*7}  {'-'*40}")
    rms_s = "n/a" if baseline.rms_deg is None else f"{baseline.rms_deg:.1f}d"
    print(f"{baseline.recall:>6.0%} {baseline.precision:>6.0%} {baseline.f1:>6.3f} "
          f"{rms_s:>7}  BASELINE (current defaults)")
    for r in top:
        rms_s = "n/a" if r.rms_deg is None else f"{r.rms_deg:.1f}d"
        diff = changed_fields(r.params, base_params)
        label = ", ".join(f"{k}={v}" for k, v in diff.items()) or "(defaults)"
        print(f"{r.recall:>6.0%} {r.precision:>6.0%} {r.f1:>6.3f} {rms_s:>7}  {label[:60]}")

    print(f"\nBest: {best.matched}/{best.taps} taps found, "
          f"{best.ghosts} false detection(s) out of {best.events} events")
    if best.fit:
        print(f"Mount fit: yaw_deg={best.fit[0]:+.1f} mirror={best.fit[1]} "
              f"rms={best.fit[2]:.1f}deg")
        if best.fit[2] <= 10:
            print("  -> good fit. Put these in radar/mount.json with the measured "
                  "mount_height_m and \"calibrated\": true")
        else:
            print("  -> RMS above 10deg: taps and detections are not lining up well. "
                  "Check the taps were made for the right balls before trusting this.")
    else:
        print("Mount fit: not enough matched taps with a direction to fit.")

    print(f"\nReproduce:\n  python3 tools/replay_jsonl.py <file> {set_flags(best.params, base_params)}")
    if best.f1 <= baseline.f1 and args.min_recall is None:
        print("\nNothing beat the defaults - they are already the best on this data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
