#!/usr/bin/env python3
"""Generate the canonical shot set for the cross-engine parity suite.

Deterministic: same output every run. Covers angle quadrants (including
wraparound), speed extremes, elevation extremes, boundary-edge distances and
degenerate inputs. Run once to (re)create shots.json; the runners consume it.
"""

import json
from pathlib import Path

FIELD = [
    {"x": 0, "y": -3, "name": "wicketkeeper"},
    {"x": -5, "y": -4, "name": "first slip"},
    {"x": -8, "y": 2, "name": "gully"},
    {"x": -15, "y": 15, "name": "point"},
    {"x": -20, "y": 30, "name": "cover"},
    {"x": -5, "y": 35, "name": "mid-off"},
    {"x": 5, "y": 35, "name": "mid-on"},
    {"x": 20, "y": 25, "name": "midwicket"},
    {"x": 15, "y": 10, "name": "square leg"},
    {"x": 45, "y": 45, "name": "deep midwicket"},
    {"x": -40, "y": 40, "name": "deep cover"},
]

shots = []
seed_counter = 1000

angles = [-180, -170, -135, -90, -60, -45, -15, 0, 15, 30, 45, 60, 90, 135, 170, 190]
speeds = [0, 20, 45, 65, 80, 100, 120, 160, 200]
elevations = [0, 2, 8, 15, 30, 45, 60, 90]

for angle in angles:
    for speed in speeds:
        for elevation in elevations:
            seed_counter += 1
            shots.append({
                "exit_speed": speed,
                "horizontal_angle": angle,
                "vertical_angle": elevation,
                "boundary_distance": 70.0,
                "difficulty": ["easy", "medium", "hard"][seed_counter % 3],
                "seed": seed_counter,
            })

# Degenerate / adversarial inputs (NaN is not valid JSON; runners add those)
for extra in [
    {"exit_speed": 300, "horizontal_angle": 720, "vertical_angle": 120},
    {"exit_speed": -50, "horizontal_angle": -540, "vertical_angle": -10},
]:
    seed_counter += 1
    shots.append({**extra, "boundary_distance": 70.0, "difficulty": "medium", "seed": seed_counter})

out = Path(__file__).parent / "shots.json"
out.write_text(json.dumps({"field": FIELD, "shots": shots}, indent=1))
print(f"wrote {len(shots)} shots to {out}")
