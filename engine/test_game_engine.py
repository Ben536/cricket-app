#!/usr/bin/env python3
"""
Test suite for the cricket shot outcome simulator.

Run with: python test_game_engine.py
"""

import random
from collections import Counter
from game_engine import simulate_delivery


# =============================================================================
# Standard Field Configuration
# =============================================================================

def get_standard_field() -> list[dict]:
    """
    Returns a realistic field configuration with 11 fielders.

    Coordinate system:
    - Batter at origin (0, 0)
    - Negative Y = toward bowler
    - Positive X = off side (for right-hander)
    - Negative X = leg side

    Positions are in metres from the batter.
    """
    return [
        # Keeper
        {'x': 0, 'y': 3, 'name': 'wicketkeeper'},

        # Slip cordon
        {'x': 5, 'y': 4, 'name': 'first slip'},
        {'x': 7, 'y': 5, 'name': 'second slip'},

        # Close catchers
        {'x': 8, 'y': -2, 'name': 'gully'},

        # Inner ring - off side
        {'x': 15, 'y': -15, 'name': 'point'},
        {'x': 20, 'y': -30, 'name': 'cover'},
        {'x': 5, 'y': -35, 'name': 'mid-off'},

        # Inner ring - leg side
        {'x': -5, 'y': -35, 'name': 'mid-on'},
        {'x': -20, 'y': -25, 'name': 'midwicket'},
        {'x': -15, 'y': -10, 'name': 'square leg'},

        # Boundary
        {'x': -45, 'y': -45, 'name': 'deep midwicket'},
    ]


def get_attacking_field() -> list[dict]:
    """Attacking field with more slips and close catchers."""
    return [
        {'x': 0, 'y': 3, 'name': 'wicketkeeper'},
        {'x': 4, 'y': 4, 'name': 'first slip'},
        {'x': 6, 'y': 5, 'name': 'second slip'},
        {'x': 8, 'y': 6, 'name': 'third slip'},
        {'x': 10, 'y': 4, 'name': 'gully'},
        {'x': 12, 'y': -8, 'name': 'point'},
        {'x': 18, 'y': -25, 'name': 'cover'},
        {'x': 5, 'y': -30, 'name': 'mid-off'},
        {'x': -5, 'y': -30, 'name': 'mid-on'},
        {'x': -18, 'y': -20, 'name': 'midwicket'},
        {'x': -12, 'y': -8, 'name': 'square leg'},
    ]


def get_defensive_field() -> list[dict]:
    """Defensive field spread to the boundary."""
    return [
        {'x': 0, 'y': 3, 'name': 'wicketkeeper'},
        {'x': 5, 'y': 4, 'name': 'first slip'},
        {'x': 20, 'y': -15, 'name': 'point'},
        {'x': 35, 'y': -35, 'name': 'cover'},
        {'x': 50, 'y': -40, 'name': 'deep cover'},
        {'x': 10, 'y': -45, 'name': 'long-off'},
        {'x': -10, 'y': -45, 'name': 'long-on'},
        {'x': -35, 'y': -35, 'name': 'deep midwicket'},
        {'x': -50, 'y': -20, 'name': 'deep square leg'},
        {'x': -40, 'y': 20, 'name': 'fine leg'},
        {'x': 40, 'y': 20, 'name': 'third man'},
    ]


# =============================================================================
# Test Shots
# =============================================================================

TEST_SHOTS = [
    # Format: (name, kwargs, expected_outcomes)
    # expected_outcomes is a list of acceptable outcomes

    # === BOUNDARIES ===
    (
        "Straight drive along the ground beating mid-off for four",
        {
            'exit_speed': 110.0,
            'horizontal_angle': 5.0,  # Slightly off-side of straight
            'vertical_angle': 2.0,    # Along the ground
            'landing_x': 8.0,
            'landing_y': -70.0,
            'projected_distance': 70.0,
            'max_height': 0.8,
        },
        ['4'],
    ),
    (
        "Lofted shot over mid-on for six",
        {
            'exit_speed': 105.0,
            'horizontal_angle': -10.0,  # Leg side of straight
            'vertical_angle': 35.0,     # Well lofted
            'landing_x': -15.0,
            'landing_y': -75.0,
            'projected_distance': 76.0,
            'max_height': 25.0,         # High trajectory
        },
        ['6'],
    ),
    (
        "Sweep beating fine leg for four",
        {
            'exit_speed': 85.0,
            'horizontal_angle': -110.0,  # Fine leg area
            'vertical_angle': 5.0,
            'landing_x': -55.0,
            'landing_y': 30.0,
            'projected_distance': 68.0,
            'max_height': 2.0,
        },
        ['4'],
    ),
    (
        "Cover drive piercing the field for four",
        {
            'exit_speed': 100.0,
            'horizontal_angle': 35.0,
            'vertical_angle': 3.0,
            'landing_x': 45.0,
            'landing_y': -55.0,
            'projected_distance': 71.0,
            'max_height': 1.2,
        },
        ['4'],
    ),
    (
        "Pull shot to deep midwicket boundary",
        {
            'exit_speed': 95.0,
            'horizontal_angle': -60.0,
            'vertical_angle': 8.0,
            'landing_x': -50.0,
            'landing_y': -45.0,
            'projected_distance': 67.0,
            'max_height': 4.0,
        },
        ['4'],
    ),

    # === CATCHES ===
    (
        "Firm cut straight to point (catching chance)",
        {
            'exit_speed': 90.0,
            'horizontal_angle': 55.0,
            'vertical_angle': 12.0,
            'landing_x': 20.0,
            'landing_y': -18.0,
            'projected_distance': 27.0,
            'max_height': 3.5,
        },
        ['caught', 'dropped', '1', '2'],  # Could be caught at point
    ),
    (
        "Lofted drive toward mid-off (catch chance)",
        {
            'exit_speed': 75.0,
            'horizontal_angle': 8.0,
            'vertical_angle': 25.0,
            'landing_x': 8.0,
            'landing_y': -38.0,
            'projected_distance': 39.0,
            'max_height': 8.0,
        },
        ['caught', 'dropped', '1', '2', '3'],
    ),
    (
        "Edge flying toward second slip",
        {
            'exit_speed': 95.0,
            'horizontal_angle': 140.0,  # Behind square, off side
            'vertical_angle': 15.0,
            'landing_x': 8.0,
            'landing_y': 6.0,
            'projected_distance': 10.0,
            'max_height': 2.5,
        },
        ['caught', 'dropped', '1', '4'],
    ),
    (
        "Top edge on pull going toward fine leg",
        {
            'exit_speed': 70.0,
            'horizontal_angle': -130.0,
            'vertical_angle': 45.0,
            'landing_x': -25.0,
            'landing_y': 30.0,
            'projected_distance': 39.0,
            'max_height': 20.0,
        },
        ['caught', 'dropped', '1', '2', '3', '4', 'misfield'],  # Fine leg not in standard field, keeper might be involved
    ),
    (
        "Mistimed pull to square leg",
        {
            'exit_speed': 65.0,
            'horizontal_angle': -85.0,
            'vertical_angle': 30.0,
            'landing_x': -18.0,
            'landing_y': -12.0,
            'projected_distance': 22.0,
            'max_height': 10.0,
        },
        ['caught', 'dropped', 'misfield', '1', '2', 'dot'],  # Various outcomes at square leg
    ),

    # === DOT BALLS ===
    (
        "Gentle push straight to cover (dot ball)",
        {
            'exit_speed': 45.0,
            'horizontal_angle': 30.0,
            'vertical_angle': 1.0,
            'landing_x': 18.0,
            'landing_y': -28.0,
            'projected_distance': 33.0,
            'max_height': 0.5,
        },
        ['dot', '1', 'misfield'],
    ),
    (
        "Defensive push to mid-on",
        {
            'exit_speed': 35.0,
            'horizontal_angle': -8.0,
            'vertical_angle': 0.0,
            'landing_x': -5.0,
            'landing_y': -32.0,
            'projected_distance': 32.0,
            'max_height': 0.3,
        },
        ['dot', '1', 'misfield'],
    ),
    (
        "Back foot defense to point",
        {
            'exit_speed': 40.0,
            'horizontal_angle': 50.0,
            'vertical_angle': 2.0,
            'landing_x': 14.0,
            'landing_y': -14.0,
            'projected_distance': 20.0,
            'max_height': 0.6,
        },
        ['dot', '1', 'misfield'],
    ),

    # === RUNNING BETWEEN WICKETS ===
    (
        "Push into gap at cover for a single",
        {
            'exit_speed': 55.0,
            'horizontal_angle': 25.0,
            'vertical_angle': 3.0,
            'landing_x': 12.0,
            'landing_y': -25.0,
            'projected_distance': 28.0,
            'max_height': 1.0,
        },
        ['1', '2', 'dot', 'misfield'],
    ),
    (
        "Worked off the pads for a single",
        {
            'exit_speed': 50.0,
            'horizontal_angle': -45.0,
            'vertical_angle': 2.0,
            'landing_x': -12.0,
            'landing_y': -15.0,
            'projected_distance': 19.0,
            'max_height': 0.7,
        },
        ['1', '2', 'dot', 'misfield'],  # Could find a gap for 2
    ),
    (
        "Pull shot into gap for two",
        {
            'exit_speed': 80.0,
            'horizontal_angle': -55.0,
            'vertical_angle': 6.0,
            'landing_x': -35.0,
            'landing_y': -30.0,
            'projected_distance': 46.0,
            'max_height': 3.0,
        },
        ['2', '3', '1', 'misfield', 'caught', 'dropped'],  # Aerial shot could be caught
    ),
    (
        "Drive through extra cover for two",
        {
            'exit_speed': 85.0,
            'horizontal_angle': 28.0,
            'vertical_angle': 4.0,
            'landing_x': 28.0,
            'landing_y': -42.0,
            'projected_distance': 50.0,
            'max_height': 2.0,
        },
        ['1', '2', '3', 'caught', 'dropped', 'misfield'],  # Cover in range, various outcomes
    ),
    (
        "Glance fine for two",
        {
            'exit_speed': 70.0,
            'horizontal_angle': -125.0,
            'vertical_angle': 5.0,
            'landing_x': -30.0,
            'landing_y': 20.0,
            'projected_distance': 36.0,
            'max_height': 2.5,
        },
        ['1', '2', '3'],  # No fine leg in standard field
    ),
    (
        "Cut behind point for two",
        {
            'exit_speed': 75.0,
            'horizontal_angle': 70.0,
            'vertical_angle': 3.0,
            'landing_x': 38.0,
            'landing_y': -15.0,
            'projected_distance': 41.0,
            'max_height': 1.5,
        },
        ['2', '3', '1', 'dot', 'misfield'],  # Gully might intercept
    ),

    # === EDGES AND MISCUES ===
    (
        "Thick outside edge past gully",
        {
            'exit_speed': 88.0,
            'horizontal_angle': 100.0,
            'vertical_angle': 8.0,
            'landing_x': 30.0,
            'landing_y': 5.0,
            'projected_distance': 30.0,
            'max_height': 3.0,
        },
        ['caught', 'dropped', 'misfield', '1', '2', '3', '4'],  # Slip cordon might misfield
    ),
    (
        "Inside edge past the keeper",
        {
            'exit_speed': 75.0,
            'horizontal_angle': -140.0,
            'vertical_angle': 5.0,
            'landing_x': -10.0,
            'landing_y': 8.0,
            'projected_distance': 13.0,
            'max_height': 1.5,
        },
        ['1', '2', '4', 'caught', 'dropped', 'misfield'],  # Keeper might misfield
    ),
    (
        "Leading edge lobbing toward mid-off",
        {
            'exit_speed': 50.0,
            'horizontal_angle': 15.0,
            'vertical_angle': 40.0,
            'landing_x': 6.0,
            'landing_y': -30.0,
            'projected_distance': 31.0,
            'max_height': 15.0,
        },
        ['caught', 'dropped', 'dot', 'misfield', '1', '2', '3'],  # Various outcomes possible
    ),
    (
        "Skied pull toward midwicket",
        {
            'exit_speed': 60.0,
            'horizontal_angle': -50.0,
            'vertical_angle': 55.0,
            'landing_x': -22.0,
            'landing_y': -26.0,
            'projected_distance': 34.0,
            'max_height': 22.0,
        },
        ['caught', 'dropped', 'dot', 'misfield', '1', '2', '3'],  # Various outcomes - midwicket area
    ),

    # === POWER SHOTS ===
    (
        "Slog sweep over deep midwicket for six",
        {
            'exit_speed': 115.0,
            'horizontal_angle': -65.0,
            'vertical_angle': 32.0,
            'landing_x': -55.0,
            'landing_y': -50.0,
            'projected_distance': 74.0,
            'max_height': 28.0,
        },
        ['6'],
    ),
    (
        "Lofted inside-out drive over cover",
        {
            'exit_speed': 100.0,
            'horizontal_angle': 40.0,
            'vertical_angle': 28.0,
            'landing_x': 50.0,
            'landing_y': -55.0,
            'projected_distance': 74.0,
            'max_height': 22.0,
        },
        ['6'],
    ),
    (
        "Helicopter shot over long-on",
        {
            'exit_speed': 110.0,
            'horizontal_angle': -15.0,
            'vertical_angle': 38.0,
            'landing_x': -20.0,
            'landing_y': -72.0,
            'projected_distance': 75.0,
            'max_height': 30.0,
        },
        ['6'],
    ),
]


# =============================================================================
# Test Runner
# =============================================================================

def run_shot_tests():
    """Run all test shots and display results."""
    print("=" * 80)
    print("CRICKET SHOT OUTCOME SIMULATOR - TEST SUITE")
    print("=" * 80)
    print()

    field = get_standard_field()

    print("FIELD CONFIGURATION:")
    print("-" * 40)
    for f in field:
        print(f"  {f['name']:20} ({f['x']:+6.1f}, {f['y']:+6.1f})")
    print()

    print("=" * 80)
    print("SHOT TESTS")
    print("=" * 80)
    print()

    passed = 0
    failed = 0

    for name, kwargs, expected in TEST_SHOTS:
        # Set field and difficulty
        kwargs['field_config'] = field
        kwargs['boundary_distance'] = 65.0
        kwargs['difficulty'] = 'medium'

        result = simulate_delivery(**kwargs)

        # Check if outcome matches expected
        outcome_ok = result['outcome'] in expected

        status = "PASS" if outcome_ok else "FAIL"
        if outcome_ok:
            passed += 1
        else:
            failed += 1

        print(f"[{status}] {name}")
        print(f"       Input: speed={kwargs['exit_speed']}km/h, "
              f"h_angle={kwargs['horizontal_angle']}°, "
              f"v_angle={kwargs['vertical_angle']}°, "
              f"dist={kwargs['projected_distance']}m")
        print(f"       Result: {result['outcome']} - {result['description']}")
        if result['fielder_involved']:
            print(f"       Fielder: {result['fielder_involved']}")
        if not outcome_ok:
            print(f"       Expected one of: {expected}")
        print()

    print("=" * 80)
    print(f"RESULTS: {passed} passed, {failed} failed out of {len(TEST_SHOTS)} tests")
    print("=" * 80)
    print()


def run_probability_tests():
    """
    Run catching scenarios multiple times to verify probability distributions.
    """
    print("=" * 80)
    print("PROBABILITY DISTRIBUTION TESTS")
    print("=" * 80)
    print()

    field = get_standard_field()

    # A regulation catch scenario - ball hit straight to cover at catchable height
    regulation_catch = {
        'exit_speed': 70.0,
        'horizontal_angle': 32.0,
        'vertical_angle': 18.0,
        'landing_x': 22.0,
        'landing_y': -32.0,
        'projected_distance': 39.0,
        'max_height': 6.0,
        'field_config': field,
        'boundary_distance': 65.0,
    }

    # A hard catch scenario - ball flying fast to the side of a fielder
    hard_catch = {
        'exit_speed': 105.0,
        'horizontal_angle': 38.0,
        'vertical_angle': 15.0,
        'landing_x': 25.0,
        'landing_y': -35.0,
        'projected_distance': 43.0,
        'max_height': 5.0,
        'field_config': field,
        'boundary_distance': 65.0,
    }

    iterations = 100
    random.seed(42)  # For reproducibility

    for scenario_name, scenario in [
        ("Regulation catch to cover", regulation_catch),
        ("Hard catch near cover", hard_catch),
    ]:
        print(f"\n{scenario_name}")
        print("-" * 60)
        print(f"Running {iterations} iterations at each difficulty level...")
        print()

        for difficulty in ['easy', 'medium', 'hard']:
            scenario['difficulty'] = difficulty
            outcomes = Counter()

            for _ in range(iterations):
                result = simulate_delivery(**scenario)
                outcomes[result['outcome']] += 1

            print(f"  {difficulty.upper():8} | ", end="")
            for outcome in ['caught', 'dropped', '1', '2', '3', '4', 'misfield', 'dot']:
                count = outcomes.get(outcome, 0)
                if count > 0:
                    print(f"{outcome}:{count:3} ({count}%)  ", end="")
            print()

        print()

    print("=" * 80)
    print()


def run_manual_test():
    """Interactive manual testing mode."""
    print("=" * 80)
    print("MANUAL TEST MODE")
    print("=" * 80)
    print()
    print("Enter shot parameters to test the engine.")
    print("Type 'quit' to exit.")
    print()

    field = get_standard_field()

    while True:
        try:
            print("-" * 40)
            inp = input("Exit speed (km/h) [or 'quit']: ").strip()
            if inp.lower() == 'quit':
                break

            exit_speed = float(inp)
            h_angle = float(input("Horizontal angle (degrees): "))
            v_angle = float(input("Vertical angle (degrees): "))
            distance = float(input("Projected distance (m): "))
            max_height = float(input("Max height (m): "))
            difficulty = input("Difficulty [easy/medium/hard]: ").strip() or 'medium'

            # Calculate landing point from angle and distance
            import math
            h_rad = math.radians(h_angle)
            landing_x = distance * math.sin(h_rad)
            landing_y = -distance * math.cos(h_rad)  # Negative = toward bowler

            result = simulate_delivery(
                exit_speed=exit_speed,
                horizontal_angle=h_angle,
                vertical_angle=v_angle,
                landing_x=landing_x,
                landing_y=landing_y,
                projected_distance=distance,
                max_height=max_height,
                field_config=field,
                boundary_distance=65.0,
                difficulty=difficulty,
            )

            print()
            print(f"RESULT: {result['outcome']}")
            print(f"  Runs: {result['runs']}")
            print(f"  Boundary: {result['is_boundary']}")
            print(f"  Aerial: {result['is_aerial']}")
            print(f"  Fielder: {result['fielder_involved']}")
            print(f"  Description: {result['description']}")
            print()

        except ValueError as e:
            print(f"Invalid input: {e}")
        except KeyboardInterrupt:
            break

    print("\nExiting manual test mode.")


# =============================================================================
# Main
# =============================================================================

if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == '--manual':
        run_manual_test()
    else:
        run_shot_tests()
        run_probability_tests()

        print("\nRun with --manual flag for interactive testing.")
