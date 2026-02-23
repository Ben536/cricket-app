#!/usr/bin/env python3
"""
Test script for the Cricket App database module.

Creates test data and demonstrates all query functions.
"""

import random
from datetime import datetime, timedelta
from pathlib import Path

from database import (
    init_db,
    create_player,
    get_all_players,
    get_player_by_id,
    create_session,
    get_all_sessions,
    get_sessions_by_player,
    get_session_by_id,
    insert_delivery,
    get_deliveries_for_session,
    get_session_summary_stats,
    get_scoring_breakdown_by_zone,
    get_deliveries_by_over,
    get_player_session_summaries,
    get_speed_statistics,
    delete_player,
    delete_session,
)

# Use a test database
TEST_DB_PATH = Path(__file__).parent / "test_cricket.db"


def generate_realistic_delivery(ball_number: int) -> dict:
    """
    Generate realistic delivery data based on cricket physics.

    Returns a dict with all delivery parameters.
    """
    # Determine outcome with realistic distribution
    # Most deliveries are dots/singles, fours and sixes are rarer
    outcome_weights = {
        'dot': 40,
        '1': 30,
        '2': 10,
        '3': 2,
        '4': 12,
        '6': 4,
        'caught': 1,
        'dropped': 0.5,
        'misfield': 0.5,
    }
    outcomes = list(outcome_weights.keys())
    weights = list(outcome_weights.values())
    outcome = random.choices(outcomes, weights=weights, k=1)[0]

    # Map outcome to runs
    runs_map = {
        'dot': 0, '1': 1, '2': 2, '3': 3, '4': 4, '6': 6,
        'caught': 0, 'dropped': 0, 'misfield': 1
    }
    runs = runs_map[outcome]

    # Bowling speed: typically 120-145 km/h for pace, 75-95 for spin
    # We'll assume mostly pace bowling
    bowling_speed = random.gauss(130, 8)
    bowling_speed = max(110, min(150, bowling_speed))

    # Exit speed depends on shot type
    # Defensive shots: 40-80 km/h
    # Drives: 80-120 km/h
    # Pull/cuts: 90-130 km/h
    # Big hits (6s): 120-160 km/h
    if outcome == '6':
        exit_speed = random.gauss(140, 15)
    elif outcome == '4':
        exit_speed = random.gauss(110, 15)
    elif outcome in ['dot', 'caught']:
        exit_speed = random.gauss(60, 20)
    else:
        exit_speed = random.gauss(85, 20)
    exit_speed = max(20, min(170, exit_speed))

    # Horizontal angle: where the ball goes
    # Different shots go to different areas
    if outcome == '6':
        # Big hits often go straight or leg side
        horizontal_angle = random.gauss(-10, 30)
    elif outcome == '4':
        # Boundaries spread across the field
        horizontal_angle = random.gauss(0, 40)
    else:
        # General play varies
        horizontal_angle = random.gauss(5, 35)
    horizontal_angle = max(-90, min(90, horizontal_angle))

    # Vertical angle (launch angle)
    # Ground shots: -5 to 10 degrees
    # Lofted shots: 15 to 45 degrees
    if outcome == '6':
        vertical_angle = random.gauss(30, 8)
    elif outcome in ['caught', 'dropped']:
        vertical_angle = random.gauss(35, 10)
    elif outcome == '4':
        # Mix of ground and aerial
        vertical_angle = random.gauss(8, 12)
    else:
        vertical_angle = random.gauss(3, 8)
    vertical_angle = max(-10, min(60, vertical_angle))

    # Calculate landing position based on angles and speed
    # Simplified physics model
    import math

    # Convert to radians
    h_rad = math.radians(horizontal_angle)
    v_rad = math.radians(max(0, vertical_angle))

    # Estimate distance based on exit speed and launch angle
    # Using simplified projectile motion
    g = 9.81
    v0 = exit_speed / 3.6  # Convert to m/s

    if vertical_angle > 0:
        # Time of flight for projectile
        t_flight = 2 * v0 * math.sin(v_rad) / g
        projected_distance = v0 * math.cos(v_rad) * t_flight
        max_height = (v0 * math.sin(v_rad)) ** 2 / (2 * g)
    else:
        # Ground shot - estimate based on speed
        projected_distance = v0 * 2  # Rough estimate
        max_height = 0.5  # Ball stays low

    # Add some randomness
    projected_distance *= random.uniform(0.8, 1.2)
    projected_distance = max(1, min(100, projected_distance))

    # Landing coordinates
    landing_x = projected_distance * math.sin(h_rad)
    landing_y = projected_distance * math.cos(h_rad)

    # Is it a boundary?
    is_boundary = outcome in ['4', '6']

    # Is it aerial?
    is_aerial = vertical_angle > 10 or outcome in ['6', 'caught', 'dropped']

    # Fielder position for dismissals/close calls
    fielder_positions = [
        'slip', 'gully', 'point', 'cover', 'mid-off', 'mid-on',
        'midwicket', 'square leg', 'fine leg', 'third man',
        'deep cover', 'deep midwicket', 'long-on', 'long-off'
    ]
    fielder_position = None
    if outcome in ['caught', 'dropped', 'misfield']:
        # Pick a fielder based on angle
        if horizontal_angle > 50:
            fielder_position = random.choice(['slip', 'gully', 'point', 'third man'])
        elif horizontal_angle > 10:
            fielder_position = random.choice(['cover', 'mid-off', 'deep cover'])
        elif horizontal_angle > -20:
            fielder_position = random.choice(['mid-on', 'long-on', 'long-off'])
        else:
            fielder_position = random.choice(['midwicket', 'square leg', 'fine leg', 'deep midwicket'])

    # Radar detection quality
    radar_frames_captured = random.randint(5, 30)
    detection_confidence = random.uniform(0.7, 0.99)

    # Sometimes bowling speed not captured
    if random.random() < 0.1:
        bowling_speed = None

    return {
        'ball_number': ball_number,
        'bowling_speed': round(bowling_speed, 1) if bowling_speed else None,
        'exit_speed': round(exit_speed, 1),
        'horizontal_angle': round(horizontal_angle, 1),
        'vertical_angle': round(vertical_angle, 1),
        'landing_x': round(landing_x, 2),
        'landing_y': round(landing_y, 2),
        'projected_distance': round(projected_distance, 2),
        'max_height': round(max_height, 2),
        'outcome': outcome,
        'runs': runs,
        'fielder_position': fielder_position,
        'is_boundary': is_boundary,
        'is_aerial': is_aerial,
        'radar_frames_captured': radar_frames_captured,
        'detection_confidence': round(detection_confidence, 3),
    }


def print_section(title: str):
    """Print a formatted section header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def main():
    """Run the database test."""
    # Clean up any existing test database
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()

    print_section("Initialising Database")
    init_db(TEST_DB_PATH)
    print(f"Database created at: {TEST_DB_PATH}")

    # Create test players
    print_section("Creating Players")
    players_data = [
        ("Ben Stokes", "left"),
        ("Joe Root", "right"),
        ("Virat Kohli", "right"),
    ]

    player_ids = []
    for name, hand in players_data:
        pid = create_player(name, hand, TEST_DB_PATH)
        player_ids.append(pid)
        print(f"Created player: {name} (ID: {pid}, {hand}-handed)")

    # Test get_all_players
    print_section("Testing get_all_players")
    all_players = get_all_players(TEST_DB_PATH)
    for p in all_players:
        print(f"  - {p['name']} (ID: {p['id']}, {p['batting_hand']}-handed)")

    # Test get_player_by_id
    print_section("Testing get_player_by_id")
    player = get_player_by_id(player_ids[0], TEST_DB_PATH)
    print(f"Found player: {player}")

    # Create a test session with realistic field config
    print_section("Creating Test Session")
    field_config = {
        "fielders": [
            {"id": "1", "name": "WK", "x": 50, "y": 72, "isKeeper": True},
            {"id": "2", "name": "1st Slip", "x": 58, "y": 70},
            {"id": "3", "name": "2nd Slip", "x": 62, "y": 68},
            {"id": "4", "name": "Gully", "x": 68, "y": 62},
            {"id": "5", "name": "Point", "x": 75, "y": 45},
            {"id": "6", "name": "Cover", "x": 78, "y": 30},
            {"id": "7", "name": "Mid-off", "x": 55, "y": 18},
            {"id": "8", "name": "Mid-on", "x": 42, "y": 20},
            {"id": "9", "name": "Midwicket", "x": 28, "y": 35},
            {"id": "10", "name": "Fine Leg", "x": 25, "y": 75},
        ],
        "preset": "Standard Pace"
    }

    session_id = create_session(
        player_id=player_ids[0],
        date="2024-01-15",
        field_config=field_config,
        notes="Net session at the indoor facility. Working on front foot drives.",
        db_path=TEST_DB_PATH
    )
    print(f"Created session ID: {session_id}")

    # Create a second session for player progress tracking
    session_id_2 = create_session(
        player_id=player_ids[0],
        date="2024-01-20",
        field_config=field_config,
        notes="Follow-up session focusing on back foot play.",
        db_path=TEST_DB_PATH
    )

    # Test session queries
    print_section("Testing Session Queries")
    print("\nAll sessions:")
    for s in get_all_sessions(TEST_DB_PATH):
        print(f"  - Session {s['id']}: {s['player_name']} on {s['date']}")

    print(f"\nSessions for player {player_ids[0]}:")
    for s in get_sessions_by_player(player_ids[0], TEST_DB_PATH):
        print(f"  - Session {s['id']} on {s['date']}: {s['notes'][:50]}...")

    # Insert 25 deliveries with realistic data
    print_section("Inserting Deliveries")
    base_time = datetime(2024, 1, 15, 14, 0, 0)

    for i in range(25):
        delivery = generate_realistic_delivery(i + 1)
        timestamp = (base_time + timedelta(minutes=i * 2)).isoformat()

        insert_delivery(
            session_id=session_id,
            timestamp=timestamp,
            db_path=TEST_DB_PATH,
            **delivery
        )

    print(f"Inserted 25 deliveries for session {session_id}")

    # Insert 20 deliveries for second session
    base_time_2 = datetime(2024, 1, 20, 14, 0, 0)
    for i in range(20):
        delivery = generate_realistic_delivery(i + 1)
        timestamp = (base_time_2 + timedelta(minutes=i * 2)).isoformat()
        insert_delivery(
            session_id=session_id_2,
            timestamp=timestamp,
            db_path=TEST_DB_PATH,
            **delivery
        )
    print(f"Inserted 20 deliveries for session {session_id_2}")

    # Test query functions
    print_section("Testing get_deliveries_for_session (Wagon Wheel Data)")
    deliveries = get_deliveries_for_session(session_id, TEST_DB_PATH)
    print(f"Retrieved {len(deliveries)} deliveries")
    print("\nFirst 5 deliveries:")
    for d in deliveries[:5]:
        print(f"  Ball {d['ball_number']}: {d['outcome']} | "
              f"Exit: {d['exit_speed']} km/h | "
              f"Angle: {d['horizontal_angle']}° | "
              f"Distance: {d['projected_distance']}m")

    print_section("Testing get_session_summary_stats")
    stats = get_session_summary_stats(session_id, TEST_DB_PATH)
    print(f"Session Summary:")
    print(f"  Total Runs: {stats['total_runs']}")
    print(f"  Balls Faced: {stats['balls_faced']}")
    print(f"  Strike Rate: {stats['strike_rate']}")
    print(f"  Fours: {stats['fours']}")
    print(f"  Sixes: {stats['sixes']}")
    print(f"  Dismissals: {stats['dismissals']}")

    print_section("Testing get_scoring_breakdown_by_zone")
    zones = get_scoring_breakdown_by_zone(session_id, TEST_DB_PATH)
    print("Scoring by Zone:")
    for z in zones:
        avg_speed = z['avg_exit_speed'] or 0
        print(f"  {z['zone']:12} | Runs: {z['total_runs']:3} | "
              f"Balls: {z['balls']:2} | Avg Exit Speed: {avg_speed:.1f} km/h")

    print_section("Testing get_deliveries_by_over (Manhattan Chart Data)")
    overs = get_deliveries_by_over(session_id, TEST_DB_PATH)
    print("Runs per Over:")
    for o in overs:
        bar = '█' * o['runs']
        print(f"  Over {o['over_number']:2}: {o['runs']:2} runs | "
              f"Balls: {o['balls']} | Dots: {o['dots']} | "
              f"Boundaries: {o['boundaries']} | {bar}")

    print_section("Testing get_player_session_summaries (Progress Tracking)")
    summaries = get_player_session_summaries(player_ids[0], TEST_DB_PATH)
    print(f"Session history for player {player_ids[0]}:")
    for s in summaries:
        print(f"  {s['date']}: {s['total_runs']} runs off {s['balls_faced']} balls "
              f"(SR: {s['strike_rate']}) | 4s: {s['fours']} | 6s: {s['sixes']} | "
              f"Avg Exit Speed: {s['avg_exit_speed']} km/h")

    print_section("Testing get_speed_statistics")
    speeds = get_speed_statistics(session_id, TEST_DB_PATH)
    print("Speed Statistics:")
    print(f"  Average Exit Speed: {speeds['avg_exit_speed']} km/h")
    print(f"  Max Exit Speed: {speeds['max_exit_speed']} km/h")
    print(f"  Average Bowling Speed: {speeds['avg_bowling_speed']} km/h")
    print(f"  Max Bowling Speed: {speeds['max_bowling_speed']} km/h")

    # Test delete functions
    print_section("Testing Delete Functions")
    # Create a temporary player to delete
    temp_player = create_player("Temp Player", "right", TEST_DB_PATH)
    temp_session = create_session(temp_player, "2024-01-01", db_path=TEST_DB_PATH)
    print(f"Created temp player (ID: {temp_player}) and session (ID: {temp_session})")

    deleted = delete_session(temp_session, TEST_DB_PATH)
    print(f"Deleted session {temp_session}: {deleted}")

    deleted = delete_player(temp_player, TEST_DB_PATH)
    print(f"Deleted player {temp_player}: {deleted}")

    # Verify deletion
    player_check = get_player_by_id(temp_player, TEST_DB_PATH)
    print(f"Player after deletion: {player_check}")

    print_section("All Tests Complete")
    print(f"\nTest database saved at: {TEST_DB_PATH}")
    print("You can inspect it with: sqlite3 db/test_cricket.db")


if __name__ == "__main__":
    main()
