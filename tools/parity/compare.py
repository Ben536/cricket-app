#!/usr/bin/env python3
"""Compare the two engines' results for the canonical shot set.

Exact match required on the discrete outcome fields (outcome, runs,
boundary/aerial flags, fielder, seed). Floats compared with a small
tolerance - cross-language libm (sin/cos/exp/log) can differ in the last ulp,
which is noise, but anything above tolerance is a real formula divergence.

Exit code 0 = engines agree; 1 = divergence (printed per shot).
"""

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
TOL = 1e-6

shots_path = HERE / "shots.json"
shots_sha = hashlib.sha256(shots_path.read_bytes()).hexdigest()
shots = json.loads(shots_path.read_text())["shots"]


def load(name: str) -> list:
    """
    Load a runner's results, refusing anything not produced from the CURRENT
    shots.json. results_*.json are gitignored, so a stale file can sit on disk
    for weeks; without this check compare.py will happily grade a fresh run of
    one engine against a months-old run of the other and print PARITY OK.
    """
    try:
        blob = json.loads((HERE / name).read_text())
    except FileNotFoundError:
        sys.exit(f"{name} missing - run both engine runners first")
    if not isinstance(blob, dict) or "shots_sha" not in blob:
        sys.exit(f"{name} predates the input-hash stamp - re-run both engine runners")
    if blob["shots_sha"] != shots_sha:
        sys.exit(
            f"{name} was produced from a DIFFERENT shots.json "
            f"({blob['shots_sha'][:12]} != {shots_sha[:12]}) - re-run both engine runners"
        )
    return blob["results"]


py = load("results_py.json")
ts = load("results_ts.json")

assert len(py) == len(ts) == len(shots), "result counts differ"

EXACT = ["outcome", "runs", "is_boundary", "is_aerial", "fielder_involved", "seed"]
# end_x/end_y are load-bearing: the steep-band sweep detects the high-apex
# regression through those two fields alone (the discrete fields agree on those
# shots). Removing them, or loosening TOL by ~3 orders of magnitude, drops the
# guard from 11 shots to 1.
FLOAT = ["end_x", "end_y", "fielding_time", "boundary_distance"]

failures = 0
for i, (p, t, s) in enumerate(zip(py, ts, shots)):
    diffs = []
    for k in EXACT:
        if p.get(k) != t.get(k):
            diffs.append(f"{k}: py={p.get(k)!r} ts={t.get(k)!r}")
    for k in FLOAT:
        pv, tv = p.get(k), t.get(k)
        if pv is None and tv is None:
            continue
        if (pv is None) != (tv is None):
            diffs.append(f"{k}: py={pv!r} ts={tv!r}")
            continue
        scale = max(abs(pv), abs(tv), 1.0)
        if abs(pv - tv) / scale > TOL:
            diffs.append(f"{k}: py={pv} ts={tv}")
    if diffs:
        failures += 1
        if failures <= 20:
            print(f"SHOT {i} (speed={s['exit_speed']}, angle={s['horizontal_angle']}, "
                  f"elev={s['vertical_angle']}, seed={s['seed']}):")
            for d in diffs:
                print(f"  {d}")

if failures:
    print(f"\nPARITY FAILED: {failures}/{len(shots)} shots diverge")
    sys.exit(1)
print(f"PARITY OK: {len(shots)} shots identical across both engines")
