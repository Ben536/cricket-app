#!/usr/bin/env python3
"""Run the canonical parity shots through the PYTHON engine.

Mirrors the production server path exactly (handlers.handle_simulate_shot):
trajectory from engine._calculate_trajectory, then simulate_delivery.
Writes results_py.json.
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from engine.game_engine import _calculate_trajectory, simulate_delivery  # noqa: E402

shots_path = Path(__file__).parent / "shots.json"
shots_sha = hashlib.sha256(shots_path.read_bytes()).hexdigest()
data = json.loads(shots_path.read_text())
fields = data["fields"]

results = []
for shot in data["shots"]:
    traj = _calculate_trajectory(
        shot["exit_speed"], shot["horizontal_angle"], shot["vertical_angle"]
    )
    r = simulate_delivery(
        exit_speed=shot["exit_speed"],
        horizontal_angle=shot["horizontal_angle"],
        vertical_angle=shot["vertical_angle"],
        landing_x=traj.landing_x,
        landing_y=traj.landing_y,
        projected_distance=traj.projected_distance,
        max_height=traj.max_height,
        field_config=fields[shot["field"]],
        boundary_distance=shot["boundary_distance"],
        difficulty=shot["difficulty"],
        seed=shot["seed"],
    )
    results.append({
        "outcome": r["outcome"],
        "runs": r["runs"],
        "is_boundary": r["is_boundary"],
        "is_aerial": r["is_aerial"],
        "fielder_involved": r.get("fielder_involved"),
        "end_x": r["end_position"]["x"],
        "end_y": r["end_position"]["y"],
        "fielding_time": r.get("fielding_time"),
        "boundary_distance": r.get("boundary_distance"),
        "seed": r.get("seed"),
    })

out = Path(__file__).parent / "results_py.json"
# Stamp the input hash: results_*.json are gitignored, so a stale file from a
# previous shots.json can sit on disk indefinitely. compare.py refuses to grade
# results that were not produced from the current shot set.
out.write_text(json.dumps({"shots_sha": shots_sha, "results": results}))
print(f"python engine: {len(results)} results -> {out}")
