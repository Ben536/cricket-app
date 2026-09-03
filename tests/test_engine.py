"""Game engine: coordinate conventions, determinism, sanitization.

Cross-engine parity is covered by tools/parity/ (both engines over 1,154
canonical shots); these tests pin the Python engine's own invariants.
"""

import math

from engine.game_engine import (
    _get_throw_distance,
    get_boundary_distance_at_angle,
    simulate_delivery,
)
from engine.prng import mulberry32

FIELD = [
    {"x": 20, "y": 30, "name": "mid-off"},
    {"x": -25, "y": 20, "name": "midwicket"},
    {"x": 0, "y": -3, "name": "keeper"},
]


def test_throw_targets_bowlers_end_up_pitch():
    """Bowler's stumps are at (0, +PITCH_LENGTH). The old y+PITCH_LENGTH sign
    bug put them behind the keeper, inflating throws by up to ~20m."""
    assert math.isclose(_get_throw_distance(0, 30), 30 - 20.12, abs_tol=0.01)
    assert math.isclose(_get_throw_distance(0, -15), 15.0, abs_tol=0.01)


def test_boundary_depends_on_angle():
    straight = get_boundary_distance_at_angle(0, 70)
    square = get_boundary_distance_at_angle(90, 70)
    behind = get_boundary_distance_at_angle(180, 70)
    assert straight > square > behind
    assert math.isclose(straight, 78.84, abs_tol=0.01)
    assert math.isclose(behind, 61.16, abs_tol=0.01)


def test_same_seed_same_outcome():
    kwargs = dict(exit_speed=90, horizontal_angle=20, vertical_angle=15,
                  landing_x=30, landing_y=45, projected_distance=60,
                  max_height=8, field_config=FIELD)
    r1 = simulate_delivery(**kwargs, seed=42)
    r2 = simulate_delivery(**kwargs, seed=42)
    assert r1 == r2
    assert r1["seed"] == 42


def test_result_always_carries_seed_and_boundary():
    r = simulate_delivery(exit_speed=50, horizontal_angle=0, vertical_angle=5,
                          landing_x=0, landing_y=30, projected_distance=40,
                          max_height=2, field_config=FIELD, seed=7)
    assert r["seed"] == 7
    assert r["boundary_distance"] > 70  # straight shot: boundary is further


def test_nan_inputs_do_not_crash():
    r = simulate_delivery(exit_speed=float("nan"), horizontal_angle=float("inf"),
                          vertical_angle=-5, landing_x=float("nan"), landing_y=0,
                          projected_distance=1e9, max_height=-2,
                          field_config=FIELD, seed=1)
    assert r["outcome"]  # produced a result, didn't blow up


def test_prng_golden_vectors():
    """Pin the PRNG to exact values. These are the SAME vectors the TypeScript
    twin is pinned to in src/__tests__/gameEngine.test.ts - change one and the
    engines have forked. The previous version of this test asserted only
    0 <= v < 1 and that two identical seeds agree, which random.Random would
    also have passed."""
    golden = {
        42: [0.6011037519201636, 0.44829055899754167, 0.8524657934904099,
             0.6697340414393693, 0.17481389874592423],
        0: [0.26642920868471265, 0.0003297457005828619, 0.2232720274478197,
            0.1462021479383111, 0.46732782293111086],
        4294967295: [0.8964226141106337, 0.189478256739676, 0.7156526781618595,
                     0.9440599093213677, 0.8452364315744489],
        2342376404: [0.6776549476198852, 0.0221342071890831, 0.9222554524894804,
                     0.3933766789268702, 0.21716754604130983],
    }
    for seed, expected in golden.items():
        r = mulberry32(seed)
        assert [r() for _ in range(5)] == expected, seed


def test_boundary_radicand_is_clamped():
    """R < 8.84*|sin(theta)| used to raise ValueError (shot lost); TS returned
    NaN. Both now return the tangent-point distance."""
    d = get_boundary_distance_at_angle(90, 5)
    assert math.isfinite(d)
    assert math.isclose(d, 8.84 * math.cos(math.radians(90)), abs_tol=1e-9)
    r = simulate_delivery(exit_speed=100, horizontal_angle=90, vertical_angle=10,
                          landing_x=-40, landing_y=0, projected_distance=60,
                          max_height=3, field_config=FIELD, boundary_distance=5, seed=3)
    assert r["outcome"]


def test_trajectory_sanitises_like_typescript():
    """_calculate_trajectory feeds the handler and the parity runner. It clamped
    only the elevation, so speeds above 200 produced a longer trajectory here
    than in the browser - the last input path on which the engines disagreed."""
    from engine.game_engine import _calculate_trajectory

    capped = _calculate_trajectory(200, 20, 15)
    assert _calculate_trajectory(300, 20, 15) == capped
    assert _calculate_trajectory(1e9, 20, 15) == capped
    for bad in (float("nan"), float("inf"), float("-inf")):
        t = _calculate_trajectory(bad, 30, 10)
        assert t.projected_distance == 0.0
        t = _calculate_trajectory(100, bad, bad)
        assert math.isfinite(t.landing_x) and math.isfinite(t.max_height)
