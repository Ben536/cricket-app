"""
Cricket Shot Outcome Simulator

A standalone engine that determines the outcome of cricket shots based on
ball trajectory data and field configuration. No external dependencies.

Usage:
    from game_engine import simulate_delivery

    result = simulate_delivery(
        exit_speed=95.0,
        horizontal_angle=30.0,
        vertical_angle=5.0,
        landing_x=45.0,
        landing_y=-50.0,
        projected_distance=67.0,
        max_height=2.5,
        field_config=[{'x': 25, 'y': -30, 'name': 'cover'}, ...],
        boundary_distance=70.0,
        difficulty='medium'
    )
"""

import math
import random
from typing import Optional


# =============================================================================
# Difficulty Configuration
# =============================================================================

DIFFICULTY_SETTINGS = {
    'easy': {
        'regulation_catch': {'caught': 0.70, 'dropped': 0.20, 'runs': 0.10},
        'hard_catch': {'caught': 0.30, 'dropped': 0.40, 'runs': 0.30},
        'ground_fielding': {'stopped': 0.70, 'misfield_no_extra': 0.20, 'misfield_extra': 0.10},
    },
    'medium': {
        'regulation_catch': {'caught': 0.90, 'dropped': 0.08, 'runs': 0.02},
        'hard_catch': {'caught': 0.55, 'dropped': 0.30, 'runs': 0.15},
        'ground_fielding': {'stopped': 0.85, 'misfield_no_extra': 0.10, 'misfield_extra': 0.05},
    },
    'hard': {
        'regulation_catch': {'caught': 0.98, 'dropped': 0.02, 'runs': 0.00},
        'hard_catch': {'caught': 0.75, 'dropped': 0.20, 'runs': 0.05},
        'ground_fielding': {'stopped': 0.95, 'misfield_no_extra': 0.04, 'misfield_extra': 0.01},
    },
}

# =============================================================================
# Catch Difficulty Thresholds
# =============================================================================

# Height thresholds (metres)
CATCH_HEIGHT_MIN = 0.3        # Below this = half-volley, not catchable
CATCH_HEIGHT_MAX = 3.5        # Above this = uncatchable

# Ground fielding range (metres)
GROUND_FIELDING_RANGE = 4.0   # Fielder can reach 4m either side

# =============================================================================
# Fielder Movement Constants
# =============================================================================

FIELDER_REACTION_TIME = 0.25  # seconds to react and start moving
FIELDER_RUN_SPEED = 6.5       # m/s - professional fielder sprint speed
FIELDER_DIVE_RANGE = 2.0      # metres - diving catch extension
FIELDER_STATIC_RANGE = 1.5    # metres - catch without moving

# Difficulty weights for catch scoring
WEIGHT_REACTION = 0.25        # How much time pressure matters
WEIGHT_MOVEMENT = 0.35        # How far fielder must move
WEIGHT_HEIGHT = 0.20          # Awkwardness of catch height
WEIGHT_SPEED = 0.20           # Ball speed at fielder


# =============================================================================
# Field Zone Definitions
# =============================================================================

INNER_RING_RADIUS = 15.0      # metres
MID_FIELD_RADIUS = 30.0       # metres


# =============================================================================
# Helper Functions
# =============================================================================

def _normalize_angle(angle: float) -> float:
    """Normalize angle to -180 to 180 range."""
    while angle > 180:
        angle -= 360
    while angle < -180:
        angle += 360
    return angle


def _get_shot_direction_name(horizontal_angle: float, is_aerial: bool) -> str:
    """
    Get a descriptive name for the shot direction.

    Angle convention:
    - 0° = straight back down the pitch toward bowler
    - Positive = off side (for right-hander: cover, point, etc.)
    - Negative = leg side (for right-hander: midwicket, square leg, etc.)
    """
    angle = _normalize_angle(horizontal_angle)

    # Determine the shot type based on angle
    if -15 <= angle <= 15:
        return "driven straight" if not is_aerial else "lofted straight"
    elif 15 < angle <= 45:
        return "driven through cover" if not is_aerial else "lofted over cover"
    elif 45 < angle <= 75:
        return "cut" if not is_aerial else "cut in the air"
    elif 75 < angle <= 105:
        return "square cut" if not is_aerial else "upper cut"
    elif 105 < angle <= 135:
        return "late cut" if not is_aerial else "edged"
    elif angle > 135 or angle < -135:
        return "edged behind" if not is_aerial else "edged in the air"
    elif -135 <= angle < -105:
        return "glanced fine" if not is_aerial else "flicked fine"
    elif -105 <= angle < -75:
        return "swept" if not is_aerial else "swept in the air"
    elif -75 <= angle < -45:
        return "pulled" if not is_aerial else "hooked"
    elif -45 <= angle < -15:
        return "flicked through midwicket" if not is_aerial else "lofted over midwicket"

    return "played"


def _get_ball_height_at_distance(
    distance_from_batter: float,
    projected_distance: float,
    max_height: float,
    vertical_angle: float
) -> float:
    """
    Calculate the height of the ball at a given distance from the batter.

    Models trajectory as a parabola. The ball starts at bat height (~1m),
    rises to max_height at the apex, then descends to land.

    For simplicity, we assume the apex occurs at roughly half the projected distance
    for typical lofted shots, adjusted by vertical angle.
    """
    if projected_distance <= 0:
        return 0.0

    # Starting height (bat contact point)
    start_height = 1.0

    # For very flat shots (low vertical angle), trajectory is more linear
    if vertical_angle < 5:
        # Nearly along the ground - linear descent from start height
        if distance_from_batter >= projected_distance:
            return 0.0
        # Gradual descent
        return max(0, start_height * (1 - distance_from_batter / projected_distance))

    # For lofted shots, use parabolic trajectory
    # Apex position depends on vertical angle - higher angle = apex closer to midpoint
    apex_fraction = 0.3 + (vertical_angle / 90) * 0.2  # 0.3 to 0.5 of distance
    apex_distance = projected_distance * apex_fraction

    if distance_from_batter <= apex_distance:
        # Ascending portion - quadratic rise from start to apex
        t = distance_from_batter / apex_distance
        height = start_height + (max_height - start_height) * (2 * t - t * t)
    else:
        # Descending portion - quadratic fall from apex to ground
        remaining = projected_distance - apex_distance
        if remaining <= 0:
            return 0.0
        t = (distance_from_batter - apex_distance) / remaining
        height = max_height * (1 - t * t)

    return max(0.0, height)


def _distance_point_to_line_segment(
    px: float, py: float,
    x1: float, y1: float,
    x2: float, y2: float
) -> tuple[float, float, float, float]:
    """
    Calculate the shortest distance from point (px, py) to line segment (x1,y1)-(x2,y2).

    Returns:
        (distance, closest_x, closest_y, t) - distance, closest point on segment, and
        parameter t (0=start, 1=end) indicating where along the segment
    """
    dx = x2 - x1
    dy = y2 - y1

    if dx == 0 and dy == 0:
        # Segment is a point
        return math.sqrt((px - x1)**2 + (py - y1)**2), x1, y1, 0.0

    # Parameter t for closest point on infinite line
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)

    # Clamp t to segment
    t_clamped = max(0, min(1, t))

    # Closest point on segment
    closest_x = x1 + t_clamped * dx
    closest_y = y1 + t_clamped * dy

    distance = math.sqrt((px - closest_x)**2 + (py - closest_y)**2)

    return distance, closest_x, closest_y, t_clamped


def _is_fielder_in_ball_path(
    fielder_x: float, fielder_y: float,
    landing_x: float, landing_y: float,
    max_lateral_distance: float
) -> bool:
    """
    Check if a fielder is positioned in the general direction of the ball's path.

    This prevents fielders behind the batter from being considered for forward shots.
    """
    # Calculate the direction of the shot (from batter at origin to landing point)
    # Use dot product to see if fielder is in the forward hemisphere of the shot

    # Normalize shot direction
    shot_length = math.sqrt(landing_x**2 + landing_y**2)
    if shot_length < 0.1:
        return False

    shot_dir_x = landing_x / shot_length
    shot_dir_y = landing_y / shot_length

    # Dot product of fielder position with shot direction
    # Positive = fielder is in front of batter in shot direction
    # Negative = fielder is behind batter relative to shot direction
    dot = fielder_x * shot_dir_x + fielder_y * shot_dir_y

    # Fielder must be at least somewhat in the direction of the shot
    # Allow a small negative value to catch edges going slightly backward
    # but not fielders completely behind the batter for forward shots
    fielder_distance = math.sqrt(fielder_x**2 + fielder_y**2)

    # For close fielders (slips, keeper), allow them to catch edges going backward
    if fielder_distance < 10:
        # Close fielder - check if ball is going toward them (backward shots)
        return dot > -5  # Allow balls going somewhat backward

    # For outfielders, they must be in the forward cone of the shot
    return dot > 0


def _distance_from_batter(x: float, y: float) -> float:
    """Calculate distance from batter (at origin) to a point."""
    return math.sqrt(x * x + y * y)


def _get_boundary_intersection(
    landing_x: float,
    landing_y: float,
    boundary_distance: float
) -> dict:
    """Calculate the point where the ball path intersects the boundary circle."""
    distance = math.sqrt(landing_x ** 2 + landing_y ** 2)
    if distance == 0:
        return {'x': 0, 'y': -boundary_distance}  # Default: straight back
    scale = boundary_distance / distance
    return {
        'x': landing_x * scale,
        'y': landing_y * scale,
    }


def _calculate_full_trajectory(
    speed_kmh: float,
    horizontal_angle: float,
    vertical_angle: float
) -> dict:
    """
    Calculate full trajectory data including timing for fielder calculations.

    Returns:
        dict with: projected_distance, max_height, landing_x, landing_y,
                   time_of_flight, horizontal_speed, vertical_speed,
                   direction_x, direction_y
    """
    speed_ms = speed_kmh / 3.6
    h_rad = math.radians(horizontal_angle)
    v_rad = math.radians(vertical_angle)

    v_horizontal = speed_ms * math.cos(v_rad)
    v_vertical = speed_ms * math.sin(v_rad)
    g = 9.81

    if v_vertical > 0:
        t_up = v_vertical / g
        apex_height = 1 + (v_vertical ** 2) / (2 * g)
        t_down = math.sqrt(2 * apex_height / g)
        t_flight = t_up + t_down
        max_height = apex_height
    else:
        t_flight = math.sqrt(2 / g)
        max_height = 1.0

    distance = v_horizontal * t_flight
    landing_x = -distance * math.sin(h_rad)
    landing_y = -distance * math.cos(h_rad)

    # Calculate direction unit vector
    dir_mag = math.sqrt(landing_x ** 2 + landing_y ** 2)
    dir_x = landing_x / dir_mag if dir_mag > 0 else 0
    dir_y = landing_y / dir_mag if dir_mag > 0 else -1

    return {
        'projected_distance': distance,
        'max_height': max_height,
        'landing_x': landing_x,
        'landing_y': landing_y,
        'time_of_flight': t_flight,
        'horizontal_speed': v_horizontal,
        'vertical_speed': v_vertical,
        'direction_x': dir_x,
        'direction_y': dir_y,
    }


def _get_ball_position_at_time(trajectory: dict, time: float) -> tuple[float, float, float]:
    """
    Get ball position (x, y, z) at a specific time along trajectory.
    """
    g = 9.81
    horizontal_dist = trajectory['horizontal_speed'] * time
    x = horizontal_dist * trajectory['direction_x']
    y = horizontal_dist * trajectory['direction_y']
    z = 1 + trajectory['vertical_speed'] * time - 0.5 * g * time ** 2
    return x, y, max(0.0, z)


def _get_time_at_distance(trajectory: dict, distance: float) -> float:
    """Calculate time when ball reaches a given horizontal distance."""
    if trajectory['horizontal_speed'] <= 0:
        return float('inf')
    return distance / trajectory['horizontal_speed']


def _find_catchable_intercept(
    fielder_x: float,
    fielder_y: float,
    trajectory: dict
) -> tuple[float, float, float, bool]:
    """
    Find the best point along the trajectory where a fielder could catch the ball.

    A real fielder will run to where they can make the most comfortable catch.
    Priority:
    1. Can they reach ANY catchable point? If not, no catch.
    2. Among reachable points, prefer optimal height (1.0-1.8m chest height)
    3. If they can reach optimal height with time to spare, minimal difficulty
    4. If they're rushed and must catch at awkward height, higher difficulty

    Returns:
        (time, lateral_distance, height, had_time_for_optimal)
    """
    OPTIMAL_HEIGHT_MIN = 1.0
    OPTIMAL_HEIGHT_MAX = 1.8

    # Collect ALL reachable catch points
    reachable_points = []

    time_step = 0.05
    t = 0.1

    while t < trajectory['time_of_flight']:
        x, y, z = _get_ball_position_at_time(trajectory, t)

        if CATCH_HEIGHT_MIN <= z <= CATCH_HEIGHT_MAX:
            dx = x - fielder_x
            dy = y - fielder_y
            lateral_dist = math.sqrt(dx * dx + dy * dy)

            movement_time = max(0, t - FIELDER_REACTION_TIME)
            movement_possible = movement_time * FIELDER_RUN_SPEED + FIELDER_DIVE_RANGE

            if lateral_dist <= movement_possible:
                reachable_points.append({
                    'time': t,
                    'lateral_dist': lateral_dist,
                    'height': z,
                    'is_optimal_height': OPTIMAL_HEIGHT_MIN <= z <= OPTIMAL_HEIGHT_MAX,
                    'movement_margin': movement_possible - lateral_dist,
                })

        t += time_step

    if not reachable_points:
        return float('inf'), float('inf'), 0.0, False

    # Check if ANY optimal height catch is reachable
    optimal_points = [p for p in reachable_points if p['is_optimal_height']]
    had_time_for_optimal = len(optimal_points) > 0

    # Pick the best point
    if optimal_points:
        # Pick optimal point with most margin (fielder arrives comfortably)
        best = max(optimal_points, key=lambda p: p['movement_margin'])
    else:
        # No optimal height reachable - pick closest to optimal range
        def height_distance(p):
            if p['height'] < OPTIMAL_HEIGHT_MIN:
                return OPTIMAL_HEIGHT_MIN - p['height']
            return p['height'] - OPTIMAL_HEIGHT_MAX
        best = min(reachable_points, key=height_distance)

    return best['time'], best['lateral_dist'], best['height'], had_time_for_optimal


def _analyze_catch_difficulty(
    fielder_x: float,
    fielder_y: float,
    trajectory: dict,
    intercept_distance: float,
    lateral_distance: float
) -> dict:
    """
    Calculate detailed catch difficulty based on trajectory, fielder position, and timing.

    Key insight: A fielder runs to the BEST catch position they can reach.
    - If they have time to reach optimal height (1.0-1.8m), height penalty is 0
    - If they're rushed and must catch at awkward height, height penalty applies
    - Movement penalty based on how much running/diving required

    Returns dict with:
        can_catch: bool
        difficulty: float (0-1, higher = harder)
        catch_type: 'regulation', 'hard', 'spectacular', or None
        reaction_time: float (seconds available to react)
        movement_required: float (metres fielder must move)
        movement_possible: float (metres fielder can move given time)
        ball_speed_at_fielder: float (km/h)
        height_at_intercept: float (metres)
        time_to_intercept: float (seconds until ball reaches fielder)
    """
    # Find the best catchable intercept point along the trajectory
    time_to_intercept, lateral_dist_actual, height_at_intercept, had_time_for_optimal = _find_catchable_intercept(
        fielder_x, fielder_y, trajectory
    )

    # If no catchable point found, return impossible
    if time_to_intercept == float('inf'):
        orig_time = _get_time_at_distance(trajectory, intercept_distance)
        _, _, orig_height = _get_ball_position_at_time(trajectory, orig_time)
        return {
            'can_catch': False,
            'difficulty': 1.0,
            'catch_type': None,
            'reaction_time': orig_time,
            'movement_required': lateral_distance,
            'movement_possible': 0.0,
            'ball_speed_at_fielder': trajectory['horizontal_speed'] * 3.6,
            'height_at_intercept': orig_height,
            'time_to_intercept': orig_time,
        }

    # Time available for fielder to move (after reaction)
    movement_time = max(0, time_to_intercept - FIELDER_REACTION_TIME)

    # How far fielder can move in available time
    movement_possible = movement_time * FIELDER_RUN_SPEED + FIELDER_DIVE_RANGE

    # === Calculate difficulty score (0 = easy, 1 = impossible) ===

    # 1. Reaction score: less time = harder
    reaction_score = max(0, min(1, 1 - (time_to_intercept - 0.5) / 1.5))

    # 2. Movement score: how much running/diving needed
    if lateral_dist_actual <= FIELDER_STATIC_RANGE:
        movement_score = 0  # Standing catch
    elif lateral_dist_actual <= FIELDER_STATIC_RANGE + FIELDER_DIVE_RANGE:
        movement_score = 0.3 + 0.2 * ((lateral_dist_actual - FIELDER_STATIC_RANGE) / FIELDER_DIVE_RANGE)
    else:
        run_distance = lateral_dist_actual - FIELDER_STATIC_RANGE
        max_run_distance = movement_possible - FIELDER_STATIC_RANGE
        movement_score = 0.5 + 0.5 * (run_distance / max_run_distance) if max_run_distance > 0 else 1.0

    # 3. Height score: ONLY penalize if fielder couldn't reach optimal height
    if had_time_for_optimal:
        height_score = 0  # Fielder reached optimal position
    else:
        if 1.0 <= height_at_intercept <= 1.8:
            height_score = 0
        elif height_at_intercept < 1.0:
            height_score = min(1, (1.0 - height_at_intercept) / 0.7)
        else:
            height_score = min(1, (height_at_intercept - 1.8) / 1.7)

    # 4. Speed score: faster ball = harder to judge and hold onto
    ball_speed_kmh = trajectory['horizontal_speed'] * 3.6
    speed_score = max(0, min(1, (ball_speed_kmh - 60) / 60))

    # Weighted difficulty
    difficulty = (
        WEIGHT_REACTION * reaction_score +
        WEIGHT_MOVEMENT * movement_score +
        WEIGHT_HEIGHT * height_score +
        WEIGHT_SPEED * speed_score
    )

    # Classify catch type
    if difficulty < 0.25:
        catch_type = 'regulation'
    elif difficulty < 0.6:
        catch_type = 'hard'
    else:
        catch_type = 'spectacular'

    return {
        'can_catch': True,
        'difficulty': difficulty,
        'catch_type': catch_type,
        'reaction_time': time_to_intercept,
        'movement_required': lateral_dist_actual,
        'movement_possible': movement_possible,
        'ball_speed_at_fielder': ball_speed_kmh,
        'height_at_intercept': height_at_intercept,
        'time_to_intercept': time_to_intercept,
    }


def _roll_catch_outcome(
    catch_analysis: dict,
    difficulty: str
) -> str:
    """
    Roll catch outcome using continuous difficulty score.
    Maps difficulty (0-1) to catch probability with difficulty setting modifier.

    Returns:
        'caught' or 'dropped'
    """
    # Base catch probability curves based on difficulty score
    # At difficulty=0: 98% catch, at difficulty=1: 46% catch
    base_catch_prob = 0.98 - 0.52 * catch_analysis['difficulty']

    # Difficulty setting modifiers (affect fielder skill)
    modifiers = {
        'easy': 0.85,     # Fielders worse - easier for batter
        'medium': 1.0,    # Baseline
        'hard': 1.10,     # Fielders better - harder for batter
    }

    mod = modifiers.get(difficulty, 1.0)
    catch_prob = min(0.99, base_catch_prob * mod)

    return 'caught' if random.random() < catch_prob else 'dropped'


def _roll_ground_fielding_outcome(probabilities: dict) -> str:
    """
    Roll a random ground fielding outcome.

    Returns:
        'stopped', 'misfield_no_extra', 'misfield_extra'
    """
    probs = probabilities['ground_fielding']
    roll = random.random()

    if roll < probs['stopped']:
        return 'stopped'
    elif roll < probs['stopped'] + probs['misfield_no_extra']:
        return 'misfield_no_extra'
    else:
        return 'misfield_extra'


def _calculate_runs_for_distance(
    distance: float,
    is_stopped: bool,
    hit_firmly: bool
) -> int:
    """
    Calculate runs based on where the ball ends up.

    Args:
        distance: Distance from batter where ball is fielded/stops
        is_stopped: Whether a fielder stopped the ball
        hit_firmly: Whether the ball was hit firmly (high exit speed)
    """
    if is_stopped:
        if hit_firmly:
            # Firm hit but fielder got there - might sneak a single
            return 1
        else:
            # Hit straight to fielder
            return 0

    # Ball not stopped by fielder
    if distance >= MID_FIELD_RADIUS:
        # Deep in outfield
        return random.choice([2, 2, 3])  # Usually 2, sometimes 3
    elif distance >= INNER_RING_RADIUS:
        # Mid-field
        return random.choice([1, 1, 2])  # Usually 1, sometimes 2
    else:
        # Inner ring with no fielder
        return 1


# =============================================================================
# Main Simulation Function
# =============================================================================

def simulate_delivery(
    exit_speed: float,
    horizontal_angle: float,
    vertical_angle: float,
    landing_x: float,
    landing_y: float,
    projected_distance: float,
    max_height: float,
    field_config: list[dict],
    boundary_distance: float = 70.0,
    difficulty: str = 'medium'
) -> dict:
    """
    Simulate the outcome of a cricket shot.

    Args:
        exit_speed: Ball speed off the bat in km/h
        horizontal_angle: Direction in degrees (0° = straight, +ve = off side, -ve = leg side)
        vertical_angle: Elevation in degrees (0° = ground, 45° = lofted)
        landing_x: X coordinate where ball lands (metres from batter)
        landing_y: Y coordinate where ball lands (negative = toward bowler)
        projected_distance: Total distance from batter in metres
        max_height: Peak height of trajectory in metres
        field_config: List of fielder dicts with 'x', 'y', 'name' keys
        boundary_distance: Boundary radius in metres (default 70)
        difficulty: 'easy', 'medium', or 'hard'

    Returns:
        Dictionary with:
        - outcome: 'dot', '1', '2', '3', '4', '6', 'caught', 'dropped', 'misfield'
        - runs: Numeric runs scored
        - is_boundary: Boolean
        - is_aerial: Boolean
        - fielder_involved: Name of fielder or None
        - description: Human-readable summary
    """
    # Validate difficulty
    if difficulty not in DIFFICULTY_SETTINGS:
        difficulty = 'medium'

    probs = DIFFICULTY_SETTINGS[difficulty]

    # Determine if shot is aerial (significant height)
    is_aerial = max_height > 1.5 or vertical_angle > 10

    # Get shot description prefix
    shot_name = _get_shot_direction_name(horizontal_angle, is_aerial)

    # Ball trajectory: from batter (0,0) toward landing point
    batter_x, batter_y = 0.0, 0.0

    # ---------------------------------------------------------------------
    # Check 1: Does the ball reach the boundary?
    # ---------------------------------------------------------------------
    if projected_distance >= boundary_distance:
        # Check height at boundary
        height_at_boundary = _get_ball_height_at_distance(
            boundary_distance, projected_distance, max_height, vertical_angle
        )

        if is_aerial and height_at_boundary > 0.5:
            # Six - cleared the boundary in the air
            boundary_point = _get_boundary_intersection(landing_x, landing_y, boundary_distance)
            return {
                'outcome': '6',
                'runs': 6,
                'is_boundary': True,
                'is_aerial': True,
                'fielder_involved': None,
                'end_position': boundary_point,
                'description': f"{shot_name.capitalize()} for six!"
            }
        else:
            # Four - reached boundary along the ground (or bounced over)
            # But first check if a fielder near the boundary could catch/stop it
            pass  # Continue to fielder checks

    # Build full trajectory data for catch analysis
    trajectory = _calculate_full_trajectory(exit_speed, horizontal_angle, vertical_angle)

    # ---------------------------------------------------------------------
    # Check 2: Catching chances (aerial balls only)
    # ---------------------------------------------------------------------
    if is_aerial:
        catching_chances = []

        for fielder in field_config:
            fx, fy = fielder['x'], fielder['y']
            fname = fielder['name']

            # Check if fielder is in the ball's path direction
            if not _is_fielder_in_ball_path(fx, fy, landing_x, landing_y, GROUND_FIELDING_RANGE + 5):
                continue

            # Distance from batter to this fielder
            fielder_distance = _distance_from_batter(fx, fy)

            # Only consider if fielder is between batter and landing point (extended for running catches)
            if fielder_distance > projected_distance + 10:
                continue

            # Find closest point on ball trajectory to fielder
            lateral_dist, closest_x, closest_y, t = _distance_point_to_line_segment(
                fx, fy,
                batter_x, batter_y,
                landing_x, landing_y
            )

            # Skip if the intercept is at the very start (t≈0) - fielder not actually in path
            if t < 0.05:
                continue

            # Distance along trajectory where ball is closest to fielder
            intercept_distance = _distance_from_batter(closest_x, closest_y)

            # Analyze catch difficulty with full trajectory data
            analysis = _analyze_catch_difficulty(
                fx, fy,
                trajectory,
                intercept_distance,
                lateral_dist
            )

            if analysis['can_catch']:
                catching_chances.append({
                    'fielder': fname,
                    'fielder_x': fx,
                    'fielder_y': fy,
                    'analysis': analysis,
                    'intercept_distance': intercept_distance
                })

        # Sort by intercept distance (closest fielder to batter gets first chance)
        catching_chances.sort(key=lambda x: x['intercept_distance'])

        # Evaluate each catching chance
        for chance in catching_chances:
            analysis = chance['analysis']
            outcome = _roll_catch_outcome(analysis, difficulty)

            if outcome == 'caught':
                # Determine catch description based on difficulty and movement
                if analysis['catch_type'] == 'spectacular':
                    catch_desc = "Spectacular catch"
                elif analysis['catch_type'] == 'hard':
                    catch_desc = "Great catch"
                else:
                    catch_desc = "Caught"

                # Add detail about running catches
                if analysis['movement_required'] > FIELDER_STATIC_RANGE + 1:
                    catch_desc += f" (running {analysis['movement_required']:.1f}m)"
                elif analysis['movement_required'] > FIELDER_STATIC_RANGE:
                    catch_desc += " (diving)"

                fielder_pos = {'x': chance['fielder_x'], 'y': chance['fielder_y']}
                # Calculate where the ball was when caught (intercept point)
                catch_x, catch_y, _ = _get_ball_position_at_time(trajectory, analysis['time_to_intercept'])
                return {
                    'outcome': 'caught',
                    'runs': 0,
                    'is_boundary': False,
                    'is_aerial': True,
                    'fielder_involved': chance['fielder'],
                    'fielder_position': fielder_pos,
                    'end_position': {'x': catch_x, 'y': catch_y},  # Where ball was caught
                    'description': f"{catch_desc} at {chance['fielder']}!",
                    'catch_analysis': analysis
                }
            elif outcome == 'dropped':
                # Dropped - but ball might still be fielded
                dropped_fielder = chance['fielder']
                fielder_pos = {'x': chance['fielder_x'], 'y': chance['fielder_y']}

                # After a drop, check if ball still reaches boundary
                if projected_distance >= boundary_distance:
                    boundary_point = _get_boundary_intersection(landing_x, landing_y, boundary_distance)
                    return {
                        'outcome': '4',
                        'runs': 4,
                        'is_boundary': True,
                        'is_aerial': True,
                        'fielder_involved': dropped_fielder,
                        'fielder_position': fielder_pos,
                        'end_position': boundary_point,
                        'description': f"{shot_name.capitalize()}, dropped at {dropped_fielder}, four!",
                        'catch_analysis': analysis
                    }

                # Otherwise, runs off the drop
                runs = _calculate_runs_for_distance(projected_distance, False, exit_speed > 80)
                return {
                    'outcome': 'dropped',
                    'runs': runs,
                    'is_boundary': False,
                    'is_aerial': True,
                    'fielder_involved': dropped_fielder,
                    'fielder_position': fielder_pos,
                    'end_position': {'x': landing_x, 'y': landing_y},
                    'description': f"{shot_name.capitalize()}, dropped at {dropped_fielder}, runs {runs}",
                    'catch_analysis': analysis
                }
            # If 'runs' outcome, continue to next fielder or ground fielding

    # ---------------------------------------------------------------------
    # Check 3: Boundary reached (no catch taken)
    # ---------------------------------------------------------------------
    if projected_distance >= boundary_distance:
        boundary_point = _get_boundary_intersection(landing_x, landing_y, boundary_distance)
        return {
            'outcome': '4',
            'runs': 4,
            'is_boundary': True,
            'is_aerial': is_aerial,
            'fielder_involved': None,
            'end_position': boundary_point,
            'description': f"{shot_name.capitalize()} to the boundary for four!"
        }

    # ---------------------------------------------------------------------
    # Check 4: Ground fielding
    # ---------------------------------------------------------------------
    ground_fielding_chances = []

    for fielder in field_config:
        fx, fy = fielder['x'], fielder['y']
        fname = fielder['name']

        # Check if fielder is in the ball's path direction
        if not _is_fielder_in_ball_path(fx, fy, landing_x, landing_y, GROUND_FIELDING_RANGE):
            continue

        # Find closest point on ball path to fielder
        lateral_dist, closest_x, closest_y, t = _distance_point_to_line_segment(
            fx, fy,
            batter_x, batter_y,
            landing_x, landing_y
        )

        # Skip if the intercept is at the very start (t≈0) - fielder not actually in path
        if t < 0.05:
            continue

        # Check if fielder can reach the ball
        if lateral_dist <= GROUND_FIELDING_RANGE:
            intercept_distance = _distance_from_batter(closest_x, closest_y)

            # Fielder must be positioned to intercept (not behind the ball's path)
            fielder_distance = _distance_from_batter(fx, fy)
            if fielder_distance <= projected_distance + GROUND_FIELDING_RANGE:
                ground_fielding_chances.append({
                    'fielder': fname,
                    'fielder_x': fx,
                    'fielder_y': fy,
                    'lateral_distance': lateral_dist,
                    'intercept_distance': intercept_distance,
                    'fielder_distance': fielder_distance
                })

    # Sort by how directly the ball goes to the fielder
    ground_fielding_chances.sort(key=lambda x: x['lateral_distance'])

    hit_firmly = exit_speed > 80

    for chance in ground_fielding_chances:
        outcome = _roll_ground_fielding_outcome(probs)

        # Ball hit straight to fielder vs into gap
        hit_to_fielder = chance['lateral_distance'] < 1.5

        fielder_pos = {'x': chance['fielder_x'], 'y': chance['fielder_y']}

        if outcome == 'stopped':
            if hit_to_fielder and not hit_firmly:
                # Clean stop, dot ball
                return {
                    'outcome': 'dot',
                    'runs': 0,
                    'is_boundary': False,
                    'is_aerial': is_aerial,
                    'fielder_involved': chance['fielder'],
                    'fielder_position': fielder_pos,
                    'end_position': fielder_pos,
                    'description': f"{shot_name.capitalize()} straight to {chance['fielder']}"
                }
            else:
                # Fielder stops it but batters can run
                runs = 1
                return {
                    'outcome': '1',
                    'runs': runs,
                    'is_boundary': False,
                    'is_aerial': is_aerial,
                    'fielder_involved': chance['fielder'],
                    'fielder_position': fielder_pos,
                    'end_position': fielder_pos,
                    'description': f"{shot_name.capitalize()}, {chance['fielder']} fields, {runs} run"
                }

        elif outcome == 'misfield_no_extra':
            # Misfield but no extra runs - always at least 1 run on a misfield
            runs = max(1, _calculate_runs_for_distance(
                chance['fielder_distance'], True, hit_firmly
            ))
            return {
                'outcome': 'misfield',
                'runs': runs,
                'is_boundary': False,
                'is_aerial': is_aerial,
                'fielder_involved': chance['fielder'],
                'fielder_position': fielder_pos,
                'end_position': fielder_pos,  # Ball stopped near fielder
                'description': f"{shot_name.capitalize()}, misfield by {chance['fielder']}, {runs} run{'s' if runs > 1 else ''}"
            }

        elif outcome == 'misfield_extra':
            # Misfield with extra runs - ball got past the fielder
            base_runs = _calculate_runs_for_distance(chance['fielder_distance'], False, hit_firmly)
            runs = min(base_runs + 1, 3)  # Misfield adds a run, max 3
            return {
                'outcome': 'misfield',
                'runs': runs,
                'is_boundary': False,
                'is_aerial': is_aerial,
                'fielder_involved': chance['fielder'],
                'fielder_position': fielder_pos,
                'end_position': {'x': landing_x, 'y': landing_y},  # Ball got past fielder
                'description': f"{shot_name.capitalize()}, misfield by {chance['fielder']}, {runs} runs"
            }

    # ---------------------------------------------------------------------
    # No fielder involved - runs based on distance
    # ---------------------------------------------------------------------
    runs = _calculate_runs_for_distance(projected_distance, False, hit_firmly)

    return {
        'outcome': str(runs) if runs > 0 else 'dot',
        'runs': runs,
        'is_boundary': False,
        'is_aerial': is_aerial,
        'fielder_involved': None,
        'end_position': {'x': landing_x, 'y': landing_y},
        'description': f"{shot_name.capitalize()} into the gap for {runs}" if runs > 0 else f"{shot_name.capitalize()}, no run"
    }
