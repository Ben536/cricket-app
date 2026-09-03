#!/usr/bin/env python3
"""Generate the canonical shot set for the cross-engine parity suite.

Deterministic: same output every run, byte-identical on every Python version
(no `random`, no dict-order dependence, plain float arithmetic only). Covers
angle quadrants (including wraparound), speed extremes, elevation extremes,
boundary-edge distances, the steep band where the engines have diverged
before, over-limit inputs, non-default boundary radii, and every field preset
the UI ships (in both batting hands). Run once to (re)create shots.json; the
runners consume it. CI regenerates it and fails on any diff.

Layout of shots.json:
    {"fields": {name: [fielder, ...]}, "shots": [{..., "field": name}, ...]}
"""

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------

# The original hand-placed parity field (metres from the batter).
DEFAULT_FIELD = [
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

# The presets the phone actually sends, in SCREEN percent exactly as they
# appear in src/fieldZones.ts FIELD_PRESET_POSITIONS. A vitest test
# (src/__tests__/parityFields.test.ts) asserts this copy matches the source,
# so the two cannot drift silently. The screen->field conversion below is the
# same arithmetic as fieldZones.screenToField, operation for operation, so the
# resulting doubles are bit-identical to what the browser computes.
UI_PRESETS_SCREEN = {
    "Standard Pace": [(50, 36), (47, 35), (44, 35), (37, 37), (30, 42),
                      (30, 58), (41, 68), (60, 58), (70, 42), (62, 26)],
    "Spin Attack":   [(50, 38), (47, 37), (55, 43), (45, 45), (54, 45),
                      (15, 58), (45, 93), (75, 58), (85, 42), (60, 93)],
    "T20 Death":     [(50, 36), (60, 94), (40, 94), (88, 42), (35, 58),
                      (12, 42), (70, 18), (83, 30), (20, 63), (65, 58)],
    "Defensive":     [(50, 36), (62, 92), (38, 92), (90, 42), (10, 42),
                      (67, 19), (33, 19), (15, 66), (12, 58), (78, 60)],
}

# fieldZones.ts geometry, restated with the same operations
_SCREEN_FIELD_RADIUS = 50
_FIELD_RADIUS_METERS = 70
_FIELD_CENTER_OFFSET_FROM_BATTER = 8.84
_METERS_PER_PERCENT = _FIELD_RADIUS_METERS / _SCREEN_FIELD_RADIUS
_BATTER_SCREEN_X = 50
_BATTER_SCREEN_Y = 50 - _FIELD_CENTER_OFFSET_FROM_BATTER / _METERS_PER_PERCENT


def screen_to_field(sx: float, sy: float) -> tuple[float, float]:
    return (sx - _BATTER_SCREEN_X) * _METERS_PER_PERCENT, (sy - _BATTER_SCREEN_Y) * _METERS_PER_PERCENT


def preset_field(name: str, left_handed: bool) -> list[dict]:
    out = []
    for i, (sx, sy) in enumerate(UI_PRESETS_SCREEN[name]):
        # The UI mirrors X for a left-hander before converting (App.tsx)
        if left_handed:
            sx = 100 - sx
        x, y = screen_to_field(sx, sy)
        out.append({"x": x, "y": y, "name": f"P{i + 1}"})
    return out


FIELDS: dict[str, list[dict]] = {"default": DEFAULT_FIELD}
for _name in UI_PRESETS_SCREEN:
    FIELDS[f"{_name} (RH)"] = preset_field(_name, left_handed=False)
    FIELDS[f"{_name} (LH)"] = preset_field(_name, left_handed=True)

# ---------------------------------------------------------------------------
# Shots
# ---------------------------------------------------------------------------

shots = []
seed_counter = 1000


def add(field: str, **kw) -> None:
    global seed_counter
    seed_counter += 1
    shots.append({
        "exit_speed": kw["exit_speed"],
        "horizontal_angle": kw["horizontal_angle"],
        "vertical_angle": kw["vertical_angle"],
        "boundary_distance": kw.get("boundary_distance", 70.0),
        "difficulty": kw.get("difficulty", ["easy", "medium", "hard"][seed_counter % 3]),
        "seed": seed_counter,
        "field": field,
    })


# --- Block A: the original grid ---------------------------------------------
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
            add("default", exit_speed=speed, horizontal_angle=angle, vertical_angle=elevation)

# --- Block B: steep-band sweep ---------------------------------------------
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
    add(
        "default",
        exit_speed=110 + _rand() * 90,          # 110-200: enough apex to exceed MAX_HEIGHT
        horizontal_angle=-180 + _rand() * 360,
        vertical_angle=62 + _rand() * 27.5,     # 62-89.5: the band the grid skipped
    )

# Degenerate / adversarial inputs. NOTE: NaN/Inf are NOT covered - they are not
# valid JSON and no runner injects them; the engines' sanitisation paths are
# pinned separately (tests/test_engine.py and src/__tests__/gameEngine.test.ts).
for extra in [
    {"exit_speed": 300, "horizontal_angle": 720, "vertical_angle": 120},
    {"exit_speed": -50, "horizontal_angle": -540, "vertical_angle": -10},
]:
    add("default", difficulty="medium", **extra)

# --- Block C: over-limit speeds at ORDINARY elevations -----------------------
#
# The two adversarial shots above both collapse to the near-vertical branch,
# so they never exercised the speed clamp. The Python trajectory did not clamp
# speed (only elevation) while the TypeScript one clamped to 200, so any speed
# above 200 at a normal elevation produced different landing points. These
# shots pin that path.
for speed in [201, 250, 300, 1000]:
    for elevation in [5, 20, 40]:
        for angle in [0, 30, -45]:
            add("default", exit_speed=speed, horizontal_angle=angle, vertical_angle=elevation)

# --- Block D: non-default boundary radii ------------------------------------
#
# boundary_distance was 70 in every shot despite a declared 50-100 range. The
# angle-dependent boundary formula, the four/six checks and the intercept limit
# all scale with it.
for boundary in [50.0, 60.0, 85.0, 100.0]:
    for angle in [0, 45, 90, 135, 180, -90]:
        for speed in [60, 90, 130]:
            for elevation in [5, 25]:
                add("default", exit_speed=speed, horizontal_angle=angle,
                    vertical_angle=elevation, boundary_distance=boundary)

# A boundary radius below the batter offset: the radicand in the boundary
# formula goes negative. Python used to raise, TypeScript returned NaN.
for angle in [30, 90]:
    add("default", exit_speed=100, horizontal_angle=angle, vertical_angle=10,
        boundary_distance=5.0, difficulty="medium")

# --- Block E: the shipped UI presets, both hands ----------------------------
#
# The default parity field is hand-placed and unlike anything the phone
# sends. Every preset in src/fieldZones.ts, mirrored for a left-hander the way
# App.tsx mirrors it, so the fielder-selection paths run over real layouts.
for preset in UI_PRESETS_SCREEN:
    for hand in ("RH", "LH"):
        for angle in [-135, -90, -45, -15, 0, 15, 45, 90, 135]:
            for speed in [50, 80, 110, 150]:
                for elevation in [3, 12, 35]:
                    add(f"{preset} ({hand})", exit_speed=speed, horizontal_angle=angle,
                        vertical_angle=elevation)

out = Path(__file__).parent / "shots.json"
out.write_text(json.dumps({"fields": FIELDS, "shots": shots}, indent=1))
print(f"wrote {len(shots)} shots over {len(FIELDS)} fields to {out}")
