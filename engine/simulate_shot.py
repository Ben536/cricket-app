#!/usr/bin/env python3
"""
Quick shot simulator - test the game engine with specific inputs.

Usage:
    python simulate_shot.py --angle 45 --elevation 5 --speed 25
    python simulate_shot.py -a 45 -e 5 -s 25 --difficulty hard
    python simulate_shot.py -a 0 -e 30 -s 100  # Straight six attempt

Arguments:
    --angle, -a      Horizontal angle in degrees (0=straight, +ve=off side, -ve=leg side)
    --elevation, -e  Vertical angle in degrees above horizontal
    --speed, -s      Exit speed in km/h
    --difficulty, -d Fielding difficulty: easy, medium, hard (default: medium)
    --distance       Override calculated distance (metres)
    --field          Field preset: standard, attacking, defensive (default: standard)
"""

import argparse
import math
import json
from game_engine import simulate_delivery


# Standard field configurations
FIELDS = {
    'standard': [
        {'x': 0, 'y': 3, 'name': 'wicketkeeper'},
        {'x': 5, 'y': 4, 'name': 'first slip'},
        {'x': 7, 'y': 5, 'name': 'second slip'},
        {'x': 8, 'y': -2, 'name': 'gully'},
        {'x': 15, 'y': -15, 'name': 'point'},
        {'x': 20, 'y': -30, 'name': 'cover'},
        {'x': 5, 'y': -35, 'name': 'mid-off'},
        {'x': -5, 'y': -35, 'name': 'mid-on'},
        {'x': -20, 'y': -25, 'name': 'midwicket'},
        {'x': -15, 'y': -10, 'name': 'square leg'},
        {'x': -45, 'y': -45, 'name': 'deep midwicket'},
    ],
    'attacking': [
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
    ],
    'defensive': [
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
    ],
}


def calculate_trajectory(speed_kmh: float, h_angle: float, v_angle: float) -> dict:
    """
    Calculate ball trajectory from speed and angles.

    Uses simplified physics (no air resistance) to estimate:
    - projected_distance
    - max_height
    - landing_x, landing_y
    """
    # Convert to m/s
    speed_ms = speed_kmh / 3.6

    # Convert angles to radians
    h_rad = math.radians(h_angle)
    v_rad = math.radians(v_angle)

    # Initial velocity components
    v_horizontal = speed_ms * math.cos(v_rad)  # Speed along ground
    v_vertical = speed_ms * math.sin(v_rad)    # Speed upward

    # Gravity
    g = 9.81

    # Time of flight (time to return to ground level, starting from bat height ~1m)
    # Using quadratic formula for: -0.5*g*t^2 + v_vertical*t + 1 = 0
    # For simplicity, assume landing at ground level
    if v_vertical > 0:
        # Time to apex + time to fall from apex
        t_up = v_vertical / g
        apex_height = 1 + (v_vertical ** 2) / (2 * g)  # Starting from 1m
        t_down = math.sqrt(2 * apex_height / g)
        t_flight = t_up + t_down
        max_height = apex_height
    else:
        # Ball going downward from start
        t_flight = math.sqrt(2 * 1.0 / g)  # Just falling from 1m
        max_height = 1.0

    # Horizontal distance traveled
    distance = v_horizontal * t_flight

    # Landing coordinates
    # x: negate because +angle = off side, but field coords have +x = leg side
    # y: negative because toward bowler
    landing_x = -distance * math.sin(h_rad)
    landing_y = -distance * math.cos(h_rad)

    return {
        'projected_distance': distance,
        'max_height': max_height,
        'landing_x': landing_x,
        'landing_y': landing_y,
        'time_of_flight': t_flight,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Simulate a cricket shot with the game engine',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Cover drive along the ground
  python simulate_shot.py -a 30 -e 2 -s 90

  # Lofted shot over mid-on
  python simulate_shot.py -a -10 -e 35 -s 100

  # Edge toward slips
  python simulate_shot.py -a 140 -e 15 -s 80

  # Pull shot to leg side
  python simulate_shot.py -a -60 -e 10 -s 95

  # Straight six attempt
  python simulate_shot.py -a 0 -e 40 -s 110

Angle reference:
  0°   = straight back toward bowler
  30°  = cover region
  45°  = point region
  90°  = square (off side)
  -45° = midwicket region
  -90° = square leg
  180° = fine leg / third man
        """
    )

    parser.add_argument('-a', '--angle', type=float, required=True,
                        help='Horizontal angle (degrees): 0=straight, +ve=off, -ve=leg')
    parser.add_argument('-e', '--elevation', type=float, required=True,
                        help='Vertical angle (degrees above horizontal)')
    parser.add_argument('-s', '--speed', type=float, required=True,
                        help='Exit speed (km/h)')
    parser.add_argument('-d', '--difficulty', type=str, default='medium',
                        choices=['easy', 'medium', 'hard'],
                        help='Fielding difficulty (default: medium)')
    parser.add_argument('--distance', type=float, default=None,
                        help='Override calculated distance (metres)')
    parser.add_argument('--field', type=str, default='standard',
                        choices=['standard', 'attacking', 'defensive'],
                        help='Field configuration (default: standard)')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')
    parser.add_argument('-n', '--iterations', type=int, default=1,
                        help='Run multiple times to see probability distribution')

    args = parser.parse_args()

    # Calculate trajectory from physics
    traj = calculate_trajectory(args.speed, args.angle, args.elevation)

    # Allow distance override
    if args.distance is not None:
        scale = args.distance / traj['projected_distance'] if traj['projected_distance'] > 0 else 1
        traj['projected_distance'] = args.distance
        traj['landing_x'] *= scale
        traj['landing_y'] *= scale

    # Get field configuration
    field = FIELDS[args.field]

    if args.iterations == 1:
        # Single shot
        result = simulate_delivery(
            exit_speed=args.speed,
            horizontal_angle=args.angle,
            vertical_angle=args.elevation,
            landing_x=traj['landing_x'],
            landing_y=traj['landing_y'],
            projected_distance=traj['projected_distance'],
            max_height=traj['max_height'],
            field_config=field,
            boundary_distance=65.0,
            difficulty=args.difficulty,
        )

        if args.json:
            output = {
                'input': {
                    'speed_kmh': args.speed,
                    'horizontal_angle': args.angle,
                    'vertical_angle': args.elevation,
                    'difficulty': args.difficulty,
                },
                'trajectory': traj,
                'result': result,
            }
            print(json.dumps(output, indent=2))
        else:
            print("\n" + "=" * 60)
            print("SHOT SIMULATION")
            print("=" * 60)
            print(f"\nInput:")
            print(f"  Speed:      {args.speed} km/h")
            print(f"  H. Angle:   {args.angle}° ({'off side' if args.angle > 0 else 'leg side' if args.angle < 0 else 'straight'})")
            print(f"  Elevation:  {args.elevation}°")
            print(f"  Difficulty: {args.difficulty}")
            print(f"  Field:      {args.field}")
            print(f"\nCalculated trajectory:")
            print(f"  Distance:   {traj['projected_distance']:.1f}m")
            print(f"  Max height: {traj['max_height']:.1f}m")
            print(f"  Landing:    ({traj['landing_x']:.1f}, {traj['landing_y']:.1f})m")
            print(f"\nResult:")
            print(f"  Outcome:    {result['outcome']}")
            print(f"  Runs:       {result['runs']}")
            print(f"  Boundary:   {result['is_boundary']}")
            print(f"  Aerial:     {result['is_aerial']}")
            if result['fielder_involved']:
                print(f"  Fielder:    {result['fielder_involved']}")
            print(f"\n  → {result['description']}")
            print("=" * 60 + "\n")

    else:
        # Multiple iterations - show distribution
        from collections import Counter
        outcomes = Counter()

        for _ in range(args.iterations):
            result = simulate_delivery(
                exit_speed=args.speed,
                horizontal_angle=args.angle,
                vertical_angle=args.elevation,
                landing_x=traj['landing_x'],
                landing_y=traj['landing_y'],
                projected_distance=traj['projected_distance'],
                max_height=traj['max_height'],
                field_config=field,
                boundary_distance=65.0,
                difficulty=args.difficulty,
            )
            outcomes[result['outcome']] += 1

        if args.json:
            print(json.dumps({
                'iterations': args.iterations,
                'outcomes': dict(outcomes),
            }, indent=2))
        else:
            print(f"\nDistribution over {args.iterations} iterations:")
            print("-" * 40)
            for outcome, count in sorted(outcomes.items(), key=lambda x: -x[1]):
                pct = count / args.iterations * 100
                bar = '█' * int(pct / 2)
                print(f"  {outcome:8} {count:4} ({pct:5.1f}%) {bar}")
            print()


if __name__ == '__main__':
    main()
