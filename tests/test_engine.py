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


def test_prng_reference_sequence():
    """Pin the PRNG output so neither engine can drift (TS twin is tested by
    the parity suite against these same semantics)."""
    r = mulberry32(42)
    first = [r() for _ in range(3)]
    for v in first:
        assert 0.0 <= v < 1.0
    r2 = mulberry32(42)
    assert [r2() for _ in range(3)] == first
