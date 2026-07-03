#!/usr/bin/env python3
"""Compare the two engines' results for the canonical shot set.

Exact match required on the discrete outcome fields (outcome, runs,
boundary/aerial flags, fielder, seed). Floats compared with a small
tolerance - cross-language libm (sin/cos/exp/log) can differ in the last ulp,
which is noise, but anything above tolerance is a real formula divergence.

Exit code 0 = engines agree; 1 = divergence (printed per shot).
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
TOL = 1e-6

py = json.loads((HERE / "results_py.json").read_text())
ts = json.loads((HERE / "results_ts.json").read_text())
shots = json.loads((HERE / "shots.json").read_text())["shots"]

assert len(py) == len(ts) == len(shots), "result counts differ"

EXACT = ["outcome", "runs", "is_boundary", "is_aerial", "fielder_involved", "seed"]
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
