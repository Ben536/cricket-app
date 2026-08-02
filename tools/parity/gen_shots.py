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
# 65-85 matter: above ~69 degrees at speed a shot's apex exceeds MAX_HEIGHT (50m),
# which is where the engines used to diverge (~4% of shots in that band). The old
# grid jumped 60 -> 90, and 90 is degenerate - the near-vertical branch forces
# landing=(0,0), so every fielder is filtered out before a catch can be evaluated.
# The steep band therefore had zero effective coverage.
elevations = [0, 2, 8, 15, 30, 45, 60, 65, 70, 75, 80, 85, 90]

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

# Steep-band sweep.
#
# The fixed grid above lands on the high-apex regression with exactly ONE shot
# out of 1874, because 16 fixed angle rays rarely pass through a fielder's
# reachable zone. A random fuzz of the same band (speed 110-200, elev 62-89.5)
# diverged on 3.90% of shots before the fix - so the grid alone is a guard that
# a small unrelated change could shake loose while the bug is still present.
#
# This sweep samples the band densely with a hand-rolled LCG rather than
# `random`, so the output is byte-identical on any Python version.
_lcg = 20260802


def _rand() -> float:
    global _lcg
    _lcg = (_lcg * 1103515245 + 12345) % (2 ** 31)
    return _lcg / (2 ** 31)


for _ in range(400):
    seed_counter += 1
    shots.append({
        "exit_speed": 110 + _rand() * 90,          # 110-200: enough apex to exceed MAX_HEIGHT
        "horizontal_angle": -180 + _rand() * 360,
        "vertical_angle": 62 + _rand() * 27.5,     # 62-89.5: the band the grid skipped
        "boundary_distance": 70.0,
        "difficulty": ["easy", "medium", "hard"][seed_counter % 3],
        "seed": seed_counter,
    })

# Degenerate / adversarial inputs. NOTE: NaN/Inf are NOT covered - they are not
# valid JSON and no runner injects them, so the engines' sanitisation paths are
# untested cross-engine. They differ today (Python max(0.0, nan) -> 0.0 vs JS
# Math.max(0, NaN) -> NaN), currently unreachable behind _is_valid_number.
for extra in [
    {"exit_speed": 300, "horizontal_angle": 720, "vertical_angle": 120},
    {"exit_speed": -50, "horizontal_angle": -540, "vertical_angle": -10},
]:
    seed_counter += 1
    shots.append({**extra, "boundary_distance": 70.0, "difficulty": "medium", "seed": seed_counter})

out = Path(__file__).parent / "shots.json"
out.write_text(json.dumps({"field": FIELD, "shots": shots}, indent=1))
print(f"wrote {len(shots)} shots to {out}")
