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
        boundary_distance=65.0,
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

# Distance from fielder to ball path (metres)
CATCH_LATERAL_EASY = 1.0      # Ball within 1m = regulation
CATCH_LATERAL_HARD = 2.5      # Ball 1-2.5m = hard catch
CATCH_LATERAL_MAX = 3.0       # Beyond 3m = no catch possible

# Height thresholds (metres)
CATCH_HEIGHT_MIN = 0.3        # Below this = half-volley, not catchable
CATCH_HEIGHT_EASY_MIN = 0.5   # Comfortable catching zone
CATCH_HEIGHT_EASY_MAX = 2.2   # Above head height gets harder
CATCH_HEIGHT_MAX = 3.5        # Above this = uncatchable

# Speed thresholds (km/h)
CATCH_SPEED_EASY = 80         # Below this = comfortable
CATCH_SPEED_HARD = 120        # Above this = very difficult

# Ground fielding range (metres)
GROUND_FIELDING_RANGE = 4.0   # Fielder can reach 4m either side


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


def _classify_catch_difficulty(
    lateral_distance: float,
    height: float,
    ball_speed: float
) -> Optional[str]:
    """
    Classify whether a catch is possible and its difficulty.

    Returns:
        'regulation', 'hard', or None (no catch possible)
    """
    # Check if catch is possible at all
    if lateral_distance > CATCH_LATERAL_MAX:
        return None
    if height < CATCH_HEIGHT_MIN or height > CATCH_HEIGHT_MAX:
        return None

    # Determine difficulty based on multiple factors
    difficulty_score = 0

    # Lateral distance factor
    if lateral_distance > CATCH_LATERAL_EASY:
        difficulty_score += 1
    if lateral_distance > CATCH_LATERAL_HARD:
        difficulty_score += 1

    # Height factor
    if height < CATCH_HEIGHT_EASY_MIN or height > CATCH_HEIGHT_EASY_MAX:
        difficulty_score += 1
    if height < CATCH_HEIGHT_MIN + 0.1 or height > CATCH_HEIGHT_MAX - 0.3:
        difficulty_score += 1

    # Speed factor
    if ball_speed > CATCH_SPEED_EASY:
        difficulty_score += 1
    if ball_speed > CATCH_SPEED_HARD:
        difficulty_score += 1

    # Classify based on total difficulty
    if difficulty_score >= 2:
        return 'hard'
    return 'regulation'


def _roll_fielding_outcome(
    outcome_type: str,
    probabilities: dict
) -> str:
    """
    Roll a random outcome based on difficulty probabilities.

    Args:
        outcome_type: 'regulation_catch', 'hard_catch', or 'ground_fielding'
        probabilities: Dict with outcome probabilities

    Returns:
        'caught', 'dropped', 'runs' for catches
        'stopped', 'misfield_no_extra', 'misfield_extra' for ground fielding
    """
    probs = probabilities[outcome_type]
    roll = random.random()

    if outcome_type in ('regulation_catch', 'hard_catch'):
        if roll < probs['caught']:
            return 'caught'
        elif roll < probs['caught'] + probs['dropped']:
            return 'dropped'
        else:
            return 'runs'
    else:  # ground_fielding
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
    boundary_distance: float = 65.0,
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
        boundary_distance: Boundary radius in metres (default 65)
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
            return {
                'outcome': '6',
                'runs': 6,
                'is_boundary': True,
                'is_aerial': True,
                'fielder_involved': None,
                'description': f"{shot_name.capitalize()} for six!"
            }
        else:
            # Four - reached boundary along the ground (or bounced over)
            # But first check if a fielder near the boundary could catch/stop it
            pass  # Continue to fielder checks

    # ---------------------------------------------------------------------
    # Check 2: Catching chances (aerial balls only)
    # ---------------------------------------------------------------------
    if is_aerial:
        catching_chances = []

        for fielder in field_config:
            fx, fy = fielder['x'], fielder['y']
            fname = fielder['name']

            # Check if fielder is in the ball's path direction
            if not _is_fielder_in_ball_path(fx, fy, landing_x, landing_y, CATCH_LATERAL_MAX):
                continue

            # Distance from batter to this fielder
            fielder_distance = _distance_from_batter(fx, fy)

            # Only consider if fielder is between batter and landing point
            if fielder_distance > projected_distance + 5:
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

            # Height of ball at that point
            ball_height = _get_ball_height_at_distance(
                intercept_distance, projected_distance, max_height, vertical_angle
            )

            # Classify catch difficulty
            catch_type = _classify_catch_difficulty(lateral_dist, ball_height, exit_speed)

            if catch_type:
                catching_chances.append({
                    'fielder': fname,
                    'lateral_distance': lateral_dist,
                    'height': ball_height,
                    'catch_type': catch_type,
                    'intercept_distance': intercept_distance
                })

        # Sort by intercept distance (closest fielder to batter gets first chance)
        catching_chances.sort(key=lambda x: x['intercept_distance'])

        # Evaluate each catching chance
        for chance in catching_chances:
            outcome = _roll_fielding_outcome(
                f"{chance['catch_type']}_catch",
                probs
            )

            if outcome == 'caught':
                catch_desc = "Caught" if chance['catch_type'] == 'regulation' else "Great catch"
                return {
                    'outcome': 'caught',
                    'runs': 0,
                    'is_boundary': False,
                    'is_aerial': True,
                    'fielder_involved': chance['fielder'],
                    'description': f"{catch_desc} at {chance['fielder']}!"
                }
            elif outcome == 'dropped':
                # Dropped - but ball might still be fielded
                # Continue to check if boundary is reached or runs scored
                dropped_fielder = chance['fielder']

                # After a drop, check if ball still reaches boundary
                if projected_distance >= boundary_distance:
                    return {
                        'outcome': '4',
                        'runs': 4,
                        'is_boundary': True,
                        'is_aerial': True,
                        'fielder_involved': dropped_fielder,
                        'description': f"{shot_name.capitalize()}, dropped at {dropped_fielder}, four!"
                    }

                # Otherwise, runs off the drop
                runs = _calculate_runs_for_distance(projected_distance, False, exit_speed > 80)
                return {
                    'outcome': 'dropped',
                    'runs': runs,
                    'is_boundary': False,
                    'is_aerial': True,
                    'fielder_involved': dropped_fielder,
                    'description': f"{shot_name.capitalize()}, dropped at {dropped_fielder}, runs {runs}"
                }
            # If 'runs' outcome, continue to next fielder or ground fielding

    # ---------------------------------------------------------------------
    # Check 3: Boundary reached (no catch taken)
    # ---------------------------------------------------------------------
    if projected_distance >= boundary_distance:
        return {
            'outcome': '4',
            'runs': 4,
            'is_boundary': True,
            'is_aerial': is_aerial,
            'fielder_involved': None,
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
                    'lateral_distance': lateral_dist,
                    'intercept_distance': intercept_distance,
                    'fielder_distance': fielder_distance
                })

    # Sort by how directly the ball goes to the fielder
    ground_fielding_chances.sort(key=lambda x: x['lateral_distance'])

    hit_firmly = exit_speed > 80

    for chance in ground_fielding_chances:
        outcome = _roll_fielding_outcome('ground_fielding', probs)

        # Ball hit straight to fielder vs into gap
        hit_to_fielder = chance['lateral_distance'] < 1.5

        if outcome == 'stopped':
            if hit_to_fielder and not hit_firmly:
                # Clean stop, dot ball
                return {
                    'outcome': 'dot',
                    'runs': 0,
                    'is_boundary': False,
                    'is_aerial': is_aerial,
                    'fielder_involved': chance['fielder'],
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
                'description': f"{shot_name.capitalize()}, misfield by {chance['fielder']}, {runs} run{'s' if runs > 1 else ''}"
            }

        elif outcome == 'misfield_extra':
            # Misfield with extra runs
            base_runs = _calculate_runs_for_distance(chance['fielder_distance'], False, hit_firmly)
            runs = min(base_runs + 1, 3)  # Misfield adds a run, max 3
            return {
                'outcome': 'misfield',
                'runs': runs,
                'is_boundary': False,
                'is_aerial': is_aerial,
                'fielder_involved': chance['fielder'],
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
        'description': f"{shot_name.capitalize()} into the gap for {runs}" if runs > 0 else f"{shot_name.capitalize()}, no run"
    }
