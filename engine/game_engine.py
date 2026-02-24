"""
Cricket Shot Outcome Simulator

A standalone engine that determines the outcome of cricket shots based on
ball trajectory data and field configuration. No external dependencies.

Designed for real-time operation on Raspberry Pi with radar input.

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

from __future__ import annotations

import logging
import math
import random
from typing import NamedTuple, TypedDict, Optional, Sequence

# =============================================================================
# Logging Configuration
# =============================================================================

logger = logging.getLogger(__name__)

# =============================================================================
# Type Definitions
# =============================================================================

class Fielder(NamedTuple):
    """Immutable fielder position - faster than dict access."""
    x: float
    y: float
    name: str


class Trajectory(NamedTuple):
    """Precomputed trajectory data - avoids repeated dict lookups."""
    projected_distance: float
    max_height: float
    landing_x: float
    landing_y: float
    time_of_flight: float
    horizontal_speed: float
    vertical_speed: float
    direction_x: float
    direction_y: float
    # Precomputed trig values
    sin_h: float
    cos_h: float


class CatchAnalysis(TypedDict):
    """Detailed catch difficulty breakdown."""
    can_catch: bool
    difficulty: float
    catch_type: Optional[str]
    reaction_time: float
    movement_required: float
    movement_possible: float
    ball_speed_at_fielder: float
    height_at_intercept: float
    time_to_intercept: float


class SimulationResult(TypedDict):
    """Complete simulation outcome."""
    outcome: str
    runs: int
    is_boundary: bool
    is_aerial: bool
    fielder_involved: Optional[str]
    fielder_position: Optional[dict]
    end_position: dict
    description: str
    catch_analysis: Optional[CatchAnalysis]


# =============================================================================
# Physical Constants (documented, tunable)
# =============================================================================

# Gravity
GRAVITY = 9.81  # m/s^2

# Ball starting height (bat contact point)
BAT_HEIGHT = 1.0  # metres

# =============================================================================
# Catch Thresholds
# =============================================================================

CATCH_HEIGHT_MIN = 0.2   # metres - below this is half-volley/scoop
CATCH_HEIGHT_MAX = 4.0   # metres - above this is uncatchable (jumping catch limit)
CATCH_OPTIMAL_MIN = 0.8  # metres - waist height
CATCH_OPTIMAL_MAX = 1.6  # metres - chest height

# =============================================================================
# Fielder Movement Constants
# =============================================================================

FIELDER_REACTION_TIME = 0.20  # seconds - elite fielders react in 0.15-0.25s
FIELDER_RUN_SPEED = 7.0       # m/s - 25 km/h, professional fielder sprint
FIELDER_DIVE_RANGE = 2.5      # metres - full-length diving catch
FIELDER_STATIC_RANGE = 1.5    # metres - catch without moving (arm reach + step)
GROUND_FIELDING_RANGE = 3.0   # metres - lateral reach for ground balls

# =============================================================================
# Ground Fielding Time Constants
# =============================================================================

PITCH_LENGTH = 20.12          # metres between stumps (22 yards)
TIME_FOR_FIRST_RUN = 3.5      # seconds - quick single takes 2.5-3s + reaction/call
TIME_FOR_EXTRA_RUN = 2.5      # seconds - already running, turn and sprint
THROW_SPEED = 30.0            # m/s - 108 km/h, professional throw speed
COLLECTION_TIME_DIRECT = 0.5  # seconds - ball straight to fielder, clean take
COLLECTION_TIME_MOVING = 1.0  # seconds - fielder moves to collect
COLLECTION_TIME_DIVING = 1.5  # seconds - diving stop, recover, release
PICKUP_TIME_STOPPED = 0.4     # seconds - picking up stationary ball
GROUND_FRICTION = 0.03        # deceleration factor per metre - cricket outfield
MISFIELD_TIME_PENALTY = 2.5   # seconds added when ball gets past fielder
FUMBLE_TIME_PENALTY = 1.0     # seconds added on fumble/bobble

# =============================================================================
# Difficulty Weights (for catch scoring)
# =============================================================================

WEIGHT_REACTION = 0.25   # How much time pressure matters
WEIGHT_MOVEMENT = 0.35   # How far fielder must move
WEIGHT_HEIGHT = 0.20     # Awkwardness of catch height
WEIGHT_SPEED = 0.20      # Ball speed at fielder

# =============================================================================
# Field Zone Radii
# =============================================================================

INNER_RING_RADIUS = 15.0  # metres
MID_FIELD_RADIUS = 30.0   # metres

# =============================================================================
# Simulation Thresholds
# =============================================================================

AERIAL_HEIGHT_THRESHOLD = 1.5     # metres - above this is aerial
AERIAL_ANGLE_THRESHOLD = 10.0     # degrees - above this is aerial
SIX_HEIGHT_AT_BOUNDARY = 0.5      # metres - must be above this for six
MIN_SHOT_LENGTH = 0.1             # metres - below this is no shot
TRAJECTORY_TIME_STEP = 0.05       # seconds - resolution for catch analysis
FIELDER_PATH_START_T = 0.05       # parameter - ignore intercepts at t < this
CATCH_EXTENDED_RANGE = 10.0       # metres - extra range for running catches
GROUND_EXTENDED_RANGE = 5.0       # metres - extra range for ground fielding

# =============================================================================
# Input Validation Bounds
# =============================================================================

MAX_EXIT_SPEED = 200.0      # km/h - physically impossible above this
MIN_EXIT_SPEED = 0.0        # km/h
MAX_VERTICAL_ANGLE = 90.0   # degrees - straight up
MIN_VERTICAL_ANGLE = 0.0    # degrees - flat along ground (can't hit downward)
MAX_HORIZONTAL_ANGLE = 180.0
MIN_HORIZONTAL_ANGLE = -180.0
MAX_DISTANCE = 150.0        # metres - beyond any boundary
MAX_HEIGHT = 50.0           # metres - extreme lofted shot

# =============================================================================
# Difficulty Settings
# =============================================================================

# Ground fielding probabilities by difficulty
# - stopped: clean fielding, ball returned quickly
# - misfield_no_extra: fumble but recovers, slight delay
# - misfield_extra: ball gets past, significant delay
DIFFICULTY_SETTINGS = {
    'easy': {
        'ground_fielding': {'stopped': 0.70, 'misfield_no_extra': 0.20, 'misfield_extra': 0.10},
    },
    'medium': {
        'ground_fielding': {'stopped': 0.85, 'misfield_no_extra': 0.10, 'misfield_extra': 0.05},
    },
    'hard': {
        'ground_fielding': {'stopped': 0.95, 'misfield_no_extra': 0.04, 'misfield_extra': 0.01},
    },
}

# Catch probability is calculated dynamically based on difficulty score:
#   base_prob = 0.98 - 0.52 * difficulty_score
# These modifiers scale the base probability by difficulty level
CATCH_DIFFICULTY_MODIFIER = {
    'easy': 0.85,    # More drops - amateur fielders
    'medium': 1.0,   # Standard - professional level
    'hard': 1.15,    # Fewer drops - elite fielders
}


# =============================================================================
# Input Validation
# =============================================================================

def _clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp value to range, returning boundary if outside."""
    return max(min_val, min(max_val, value))


def _is_valid_number(value: float) -> bool:
    """Check if value is a valid finite number."""
    return isinstance(value, (int, float)) and math.isfinite(value)


def _validate_and_sanitize_inputs(
    exit_speed: float,
    horizontal_angle: float,
    vertical_angle: float,
    landing_x: float,
    landing_y: float,
    projected_distance: float,
    max_height: float,
    boundary_distance: float,
) -> tuple[float, float, float, float, float, float, float, float, list[str]]:
    """
    Validate and sanitize all numeric inputs.

    Returns sanitized values and list of warnings.
    Invalid values are clamped to valid ranges.
    """
    warnings = []

    # Exit speed
    if not _is_valid_number(exit_speed):
        warnings.append(f"Invalid exit_speed={exit_speed}, using 0")
        exit_speed = 0.0
    elif exit_speed < MIN_EXIT_SPEED or exit_speed > MAX_EXIT_SPEED:
        warnings.append(f"exit_speed={exit_speed} out of range, clamping")
        exit_speed = _clamp(exit_speed, MIN_EXIT_SPEED, MAX_EXIT_SPEED)

    # Horizontal angle - normalize to -180 to 180
    if not _is_valid_number(horizontal_angle):
        warnings.append(f"Invalid horizontal_angle={horizontal_angle}, using 0")
        horizontal_angle = 0.0
    else:
        # Normalize angle to -180 to 180 range
        horizontal_angle = ((horizontal_angle + 180.0) % 360.0) - 180.0

    # Vertical angle - clamp to 0-90 (can't hit ball downward or straight backward)
    if not _is_valid_number(vertical_angle):
        warnings.append(f"Invalid vertical_angle={vertical_angle}, using 0")
        vertical_angle = 0.0
    elif vertical_angle < MIN_VERTICAL_ANGLE or vertical_angle > MAX_VERTICAL_ANGLE:
        warnings.append(f"vertical_angle={vertical_angle} out of range, clamping")
        vertical_angle = _clamp(vertical_angle, MIN_VERTICAL_ANGLE, MAX_VERTICAL_ANGLE)

    # Landing coordinates
    if not _is_valid_number(landing_x):
        warnings.append(f"Invalid landing_x={landing_x}, using 0")
        landing_x = 0.0
    if not _is_valid_number(landing_y):
        warnings.append(f"Invalid landing_y={landing_y}, using 0")
        landing_y = 0.0

    # Projected distance
    if not _is_valid_number(projected_distance):
        warnings.append(f"Invalid projected_distance={projected_distance}, using 0")
        projected_distance = 0.0
    elif projected_distance < 0 or projected_distance > MAX_DISTANCE:
        warnings.append(f"projected_distance={projected_distance} out of range, clamping")
        projected_distance = _clamp(projected_distance, 0, MAX_DISTANCE)

    # Max height
    if not _is_valid_number(max_height):
        warnings.append(f"Invalid max_height={max_height}, using 0")
        max_height = 0.0
    elif max_height < 0 or max_height > MAX_HEIGHT:
        warnings.append(f"max_height={max_height} out of range, clamping")
        max_height = _clamp(max_height, 0, MAX_HEIGHT)

    # Boundary distance
    if not _is_valid_number(boundary_distance) or boundary_distance <= 0:
        warnings.append(f"Invalid boundary_distance={boundary_distance}, using 70")
        boundary_distance = 70.0

    return (exit_speed, horizontal_angle, vertical_angle, landing_x, landing_y,
            projected_distance, max_height, boundary_distance, warnings)


def _convert_field_config(field_config: list[dict]) -> list[Fielder]:
    """
    Convert field config dicts to Fielder namedtuples.

    Validates each entry and skips invalid ones with a warning.
    """
    fielders = []

    if not field_config:
        logger.warning("Empty field_config provided")
        return fielders

    for i, f in enumerate(field_config):
        try:
            if not isinstance(f, dict):
                logger.warning(f"Field config entry {i} is not a dict, skipping")
                continue

            x = f.get('x')
            y = f.get('y')
            name = f.get('name', f'fielder_{i}')

            if not _is_valid_number(x) or not _is_valid_number(y):
                logger.warning(f"Invalid coordinates for fielder {name}, skipping")
                continue

            fielders.append(Fielder(x=float(x), y=float(y), name=str(name)))

        except Exception as e:
            logger.warning(f"Error processing field config entry {i}: {e}")
            continue

    return fielders


# =============================================================================
# Geometry Helpers
# =============================================================================

def _normalize_angle(angle: float) -> float:
    """Normalize angle to -180 to 180 range using modulo (no loops)."""
    return ((angle + 180.0) % 360.0) - 180.0


def _distance(x1: float, y1: float, x2: float = 0.0, y2: float = 0.0) -> float:
    """Euclidean distance between two points."""
    dx = x2 - x1
    dy = y2 - y1
    return math.sqrt(dx * dx + dy * dy)


def _distance_point_to_line_segment(
    px: float, py: float,
    x1: float, y1: float,
    x2: float, y2: float
) -> tuple[float, float, float, float]:
    """
    Shortest distance from point (px, py) to line segment (x1,y1)-(x2,y2).

    Returns: (distance, closest_x, closest_y, t)
    where t is parameter along segment (0=start, 1=end)
    """
    dx = x2 - x1
    dy = y2 - y1

    length_sq = dx * dx + dy * dy
    if length_sq < 1e-10:
        # Segment is effectively a point
        return _distance(px, py, x1, y1), x1, y1, 0.0

    # Parameter t for closest point on infinite line
    t = ((px - x1) * dx + (py - y1) * dy) / length_sq

    # Clamp to segment
    t_clamped = _clamp(t, 0.0, 1.0)

    # Closest point on segment
    closest_x = x1 + t_clamped * dx
    closest_y = y1 + t_clamped * dy

    return _distance(px, py, closest_x, closest_y), closest_x, closest_y, t_clamped


# =============================================================================
# Shot Classification
# =============================================================================

def _get_shot_direction_name(horizontal_angle: float, is_aerial: bool) -> str:
    """
    Get descriptive name for shot direction.

    Angle convention:
    - 0° = straight back down the pitch toward bowler
    - Positive = off side (for right-hander)
    - Negative = leg side
    """
    angle = _normalize_angle(horizontal_angle)

    # Use absolute ranges to avoid overlap issues
    abs_angle = abs(angle)
    is_offside = angle >= 0

    if abs_angle <= 15:
        return "lofted straight" if is_aerial else "driven straight"
    elif abs_angle <= 45:
        if is_offside:
            return "lofted over cover" if is_aerial else "driven through cover"
        else:
            return "lofted over midwicket" if is_aerial else "flicked through midwicket"
    elif abs_angle <= 75:
        if is_offside:
            return "cut in the air" if is_aerial else "cut"
        else:
            return "hooked" if is_aerial else "pulled"
    elif abs_angle <= 105:
        if is_offside:
            return "upper cut" if is_aerial else "square cut"
        else:
            return "swept in the air" if is_aerial else "swept"
    elif abs_angle <= 135:
        if is_offside:
            return "edged" if is_aerial else "late cut"
        else:
            return "flicked fine" if is_aerial else "glanced fine"
    else:
        return "edged in the air" if is_aerial else "edged behind"


# =============================================================================
# Trajectory Calculations
# =============================================================================

def _calculate_trajectory(
    speed_kmh: float,
    horizontal_angle: float,
    vertical_angle: float
) -> Trajectory:
    """
    Calculate full trajectory with precomputed values.

    Returns a Trajectory namedtuple for efficient access.
    """
    # Handle edge case: zero speed
    if speed_kmh <= 0:
        return Trajectory(
            projected_distance=0.0,
            max_height=BAT_HEIGHT,
            landing_x=0.0,
            landing_y=0.0,
            time_of_flight=0.0,
            horizontal_speed=0.0,
            vertical_speed=0.0,
            direction_x=0.0,
            direction_y=-1.0,
            sin_h=0.0,
            cos_h=1.0,
        )

    speed_ms = speed_kmh / 3.6

    # Precompute trig (expensive on Pi)
    h_rad = math.radians(horizontal_angle)
    v_rad = math.radians(vertical_angle)
    sin_h = math.sin(h_rad)
    cos_h = math.cos(h_rad)
    cos_v = math.cos(v_rad)
    sin_v = math.sin(v_rad)

    v_horizontal = speed_ms * cos_v
    v_vertical = speed_ms * sin_v

    # Handle edge case: ball hit straight up
    if v_horizontal < 0.1:
        # Ball goes almost straight up, lands near batter
        if v_vertical > 0:
            t_up = v_vertical / GRAVITY
            max_height = BAT_HEIGHT + (v_vertical * v_vertical) / (2 * GRAVITY)
            t_flight = 2 * t_up
        else:
            t_flight = math.sqrt(2 * BAT_HEIGHT / GRAVITY)
            max_height = BAT_HEIGHT

        return Trajectory(
            projected_distance=0.1,
            max_height=max_height,
            landing_x=0.0,
            landing_y=0.0,
            time_of_flight=t_flight,
            horizontal_speed=0.1,
            vertical_speed=v_vertical,
            direction_x=0.0,
            direction_y=-1.0,
            sin_h=sin_h,
            cos_h=cos_h,
        )

    # Normal trajectory calculation
    if v_vertical > 0:
        t_up = v_vertical / GRAVITY
        apex_height = BAT_HEIGHT + (v_vertical * v_vertical) / (2 * GRAVITY)
        t_down = math.sqrt(2 * apex_height / GRAVITY)
        t_flight = t_up + t_down
        max_height = apex_height
    else:
        t_flight = math.sqrt(2 * BAT_HEIGHT / GRAVITY)
        max_height = BAT_HEIGHT

    distance = v_horizontal * t_flight
    landing_x = -distance * sin_h
    landing_y = distance * cos_h

    # Direction unit vector
    dir_mag = math.sqrt(landing_x * landing_x + landing_y * landing_y)
    if dir_mag > 0:
        dir_x = landing_x / dir_mag
        dir_y = landing_y / dir_mag
    else:
        dir_x = 0.0
        dir_y = -1.0

    return Trajectory(
        projected_distance=distance,
        max_height=max_height,
        landing_x=landing_x,
        landing_y=landing_y,
        time_of_flight=t_flight,
        horizontal_speed=v_horizontal,
        vertical_speed=v_vertical,
        direction_x=dir_x,
        direction_y=dir_y,
        sin_h=sin_h,
        cos_h=cos_h,
    )


def _get_ball_position_at_time(
    traj: Trajectory,
    time: float,
    landing_x: float = None,
    landing_y: float = None,
) -> tuple[float, float, float]:
    """Get ball position (x, y, z) at specific time along trajectory."""
    horizontal_dist = traj.horizontal_speed * time

    # Use actual landing coordinates for direction if provided
    if landing_x is not None and landing_y is not None:
        actual_dist = _distance(landing_x, landing_y)
        if actual_dist > MIN_SHOT_LENGTH:
            dir_x = landing_x / actual_dist
            dir_y = landing_y / actual_dist
        else:
            dir_x = traj.direction_x
            dir_y = traj.direction_y
    else:
        dir_x = traj.direction_x
        dir_y = traj.direction_y

    x = horizontal_dist * dir_x
    y = horizontal_dist * dir_y
    z = BAT_HEIGHT + traj.vertical_speed * time - 0.5 * GRAVITY * time * time
    return x, y, max(0.0, z)


def _get_ball_height_at_distance(
    distance_from_batter: float,
    projected_distance: float,
    max_height: float,
    vertical_angle: float
) -> float:
    """Calculate ball height at given distance from batter."""
    if projected_distance <= 0:
        return 0.0

    if distance_from_batter >= projected_distance:
        return 0.0

    # For flat shots, linear descent
    if vertical_angle < 5:
        return max(0.0, BAT_HEIGHT * (1 - distance_from_batter / projected_distance))

    # For lofted shots, parabolic trajectory
    apex_fraction = 0.3 + (vertical_angle / 90.0) * 0.2
    apex_distance = projected_distance * apex_fraction

    if distance_from_batter <= apex_distance:
        t = distance_from_batter / apex_distance
        height = BAT_HEIGHT + (max_height - BAT_HEIGHT) * (2 * t - t * t)
    else:
        remaining = projected_distance - apex_distance
        if remaining <= 0:
            return 0.0
        t = (distance_from_batter - apex_distance) / remaining
        height = max_height * (1 - t * t)

    return max(0.0, height)


# =============================================================================
# Fielder Path Analysis
# =============================================================================

def _is_fielder_in_ball_path(
    fielder: Fielder,
    landing_x: float,
    landing_y: float
) -> bool:
    """Check if fielder is positioned in general direction of ball path."""
    shot_length = _distance(landing_x, landing_y)
    if shot_length < MIN_SHOT_LENGTH:
        return False

    # Normalize shot direction
    shot_dir_x = landing_x / shot_length
    shot_dir_y = landing_y / shot_length

    # Dot product: positive = fielder in forward hemisphere
    dot = fielder.x * shot_dir_x + fielder.y * shot_dir_y
    fielder_distance = _distance(fielder.x, fielder.y)

    # Close fielders can catch edges going backward
    if fielder_distance < 10:
        return dot > -5

    # Outfielders must be in forward cone
    return dot > 0


def _get_boundary_intersection(
    landing_x: float,
    landing_y: float,
    boundary_distance: float
) -> dict:
    """Calculate point where ball path intersects boundary circle."""
    dist = _distance(landing_x, landing_y)
    if dist < MIN_SHOT_LENGTH:
        return {'x': 0.0, 'y': -boundary_distance}

    scale = boundary_distance / dist
    return {'x': landing_x * scale, 'y': landing_y * scale}


# =============================================================================
# Catch Analysis
# =============================================================================

def _find_catchable_intercept(
    fielder: Fielder,
    traj: Trajectory,
    landing_x: float,
    landing_y: float,
    projected_distance: float,
    max_height: float = None,
) -> tuple[float, float, float, bool]:
    """
    Find best point along trajectory where fielder could catch.

    Returns: (time, lateral_distance, height, had_time_for_optimal)
    """
    # Early exit if no flight time
    if traj.time_of_flight <= 0:
        return float('inf'), float('inf'), 0.0, False

    # Use passed-in landing coordinates for direction (may differ from trajectory physics)
    actual_dist = _distance(landing_x, landing_y)
    if actual_dist > MIN_SHOT_LENGTH:
        dir_x = landing_x / actual_dist
        dir_y = landing_y / actual_dist
    else:
        dir_x = traj.direction_x
        dir_y = traj.direction_y

    # Scale time of flight to match actual distance
    if traj.horizontal_speed > 0:
        actual_flight_time = projected_distance / traj.horizontal_speed
    else:
        actual_flight_time = traj.time_of_flight

    # Calculate height scaling if max_height is provided
    # This allows test cases to override the physics-based trajectory
    if max_height is not None and traj.max_height > BAT_HEIGHT:
        height_scale = (max_height - BAT_HEIGHT) / (traj.max_height - BAT_HEIGHT)
    else:
        height_scale = 1.0

    # Extract values for tight loop (avoid repeated attribute access)
    h_speed = traj.horizontal_speed
    v_speed = traj.vertical_speed
    flight_time = actual_flight_time
    fx, fy = fielder.x, fielder.y

    best_optimal = None  # Best point at optimal height
    best_any = None      # Best point at any catchable height
    best_optimal_margin = -1.0
    best_any_height_dist = float('inf')

    t = 0.1
    while t < flight_time:
        # Ball position at time t
        h_dist = h_speed * t
        x = h_dist * dir_x
        y = h_dist * dir_y
        # Calculate height with optional scaling to match provided max_height
        raw_z = BAT_HEIGHT + v_speed * t - 0.5 * GRAVITY * t * t
        z = BAT_HEIGHT + (raw_z - BAT_HEIGHT) * height_scale if height_scale != 1.0 else raw_z

        # Check if at catchable height
        if CATCH_HEIGHT_MIN <= z <= CATCH_HEIGHT_MAX:
            # Distance from fielder to ball
            dx = x - fx
            dy = y - fy
            lateral_dist = math.sqrt(dx * dx + dy * dy)

            # Can fielder reach this point?
            movement_time = max(0.0, t - FIELDER_REACTION_TIME)
            movement_possible = movement_time * FIELDER_RUN_SPEED + FIELDER_DIVE_RANGE

            if lateral_dist <= movement_possible:
                margin = movement_possible - lateral_dist
                is_optimal = CATCH_OPTIMAL_MIN <= z <= CATCH_OPTIMAL_MAX

                if is_optimal and margin > best_optimal_margin:
                    best_optimal = (t, lateral_dist, z)
                    best_optimal_margin = margin

                # Track best non-optimal by height distance from optimal range
                if z < CATCH_OPTIMAL_MIN:
                    height_dist = CATCH_OPTIMAL_MIN - z
                elif z > CATCH_OPTIMAL_MAX:
                    height_dist = z - CATCH_OPTIMAL_MAX
                else:
                    height_dist = 0.0

                if height_dist < best_any_height_dist:
                    best_any = (t, lateral_dist, z)
                    best_any_height_dist = height_dist

        t += TRAJECTORY_TIME_STEP

    # Return best point found
    if best_optimal is not None:
        return (*best_optimal, True)
    elif best_any is not None:
        return (*best_any, False)
    else:
        return float('inf'), float('inf'), 0.0, False


def _analyze_catch_difficulty(
    fielder: Fielder,
    traj: Trajectory,
    intercept_distance: float,
    lateral_distance: float,
    landing_x: float,
    landing_y: float,
    projected_distance: float,
    max_height: float = None,
) -> CatchAnalysis:
    """Calculate detailed catch difficulty based on trajectory and position."""
    time_to_intercept, lateral_dist_actual, height, had_optimal = _find_catchable_intercept(
        fielder, traj, landing_x, landing_y, projected_distance, max_height
    )

    # Can't catch
    if time_to_intercept == float('inf'):
        return CatchAnalysis(
            can_catch=False,
            difficulty=1.0,
            catch_type=None,
            reaction_time=0.0,
            movement_required=lateral_distance,
            movement_possible=0.0,
            ball_speed_at_fielder=traj.horizontal_speed * 3.6,
            height_at_intercept=0.0,
            time_to_intercept=0.0,
        )

    # Movement calculations
    movement_time = max(0.0, time_to_intercept - FIELDER_REACTION_TIME)
    movement_possible = movement_time * FIELDER_RUN_SPEED + FIELDER_DIVE_RANGE

    # Difficulty components
    reaction_score = _clamp(1.0 - (time_to_intercept - 0.5) / 1.5, 0.0, 1.0)

    if lateral_dist_actual <= FIELDER_STATIC_RANGE:
        movement_score = 0.0
    elif lateral_dist_actual <= FIELDER_STATIC_RANGE + FIELDER_DIVE_RANGE:
        movement_score = 0.3 + 0.2 * ((lateral_dist_actual - FIELDER_STATIC_RANGE) / FIELDER_DIVE_RANGE)
    else:
        run_dist = lateral_dist_actual - FIELDER_STATIC_RANGE
        max_run = max(0.01, movement_possible - FIELDER_STATIC_RANGE)
        movement_score = 0.5 + 0.5 * (run_dist / max_run)

    # Height score only if couldn't reach optimal
    if had_optimal:
        height_score = 0.0
    elif CATCH_OPTIMAL_MIN <= height <= CATCH_OPTIMAL_MAX:
        height_score = 0.0
    elif height < CATCH_OPTIMAL_MIN:
        height_score = min(1.0, (CATCH_OPTIMAL_MIN - height) / 0.7)
    else:
        height_score = min(1.0, (height - CATCH_OPTIMAL_MAX) / 1.7)

    ball_speed_kmh = traj.horizontal_speed * 3.6
    speed_score = _clamp((ball_speed_kmh - 60) / 60, 0.0, 1.0)

    difficulty = (
        WEIGHT_REACTION * reaction_score +
        WEIGHT_MOVEMENT * movement_score +
        WEIGHT_HEIGHT * height_score +
        WEIGHT_SPEED * speed_score
    )

    if difficulty < 0.25:
        catch_type = 'regulation'
    elif difficulty < 0.6:
        catch_type = 'hard'
    else:
        catch_type = 'spectacular'

    return CatchAnalysis(
        can_catch=True,
        difficulty=difficulty,
        catch_type=catch_type,
        reaction_time=time_to_intercept,
        movement_required=lateral_dist_actual,
        movement_possible=movement_possible,
        ball_speed_at_fielder=ball_speed_kmh,
        height_at_intercept=height,
        time_to_intercept=time_to_intercept,
    )


# =============================================================================
# Outcome Rolling
# =============================================================================

def _roll_catch_outcome(analysis: CatchAnalysis, difficulty: str) -> str:
    """Roll catch outcome based on difficulty score."""
    base_prob = 0.98 - 0.52 * analysis['difficulty']
    modifier = CATCH_DIFFICULTY_MODIFIER.get(difficulty, 1.0)
    catch_prob = min(0.99, base_prob * modifier)

    return 'caught' if random.random() < catch_prob else 'dropped'


def _roll_ground_fielding_outcome(probs: dict) -> str:
    """Roll ground fielding outcome."""
    gf = probs['ground_fielding']
    roll = random.random()

    if roll < gf['stopped']:
        return 'stopped'
    elif roll < gf['stopped'] + gf['misfield_no_extra']:
        return 'misfield_no_extra'
    return 'misfield_extra'


# =============================================================================
# Ground Fielding Time Calculations
# =============================================================================

def _get_ground_ball_speed(exit_speed_kmh: float, distance: float) -> float:
    """Average ball speed on ground accounting for friction."""
    if exit_speed_kmh <= 0 or distance <= 0:
        return 3.0  # Minimum rolling speed

    exit_speed_ms = exit_speed_kmh / 3.6
    friction_factor = math.exp(-GROUND_FRICTION * distance * 0.5)
    return max(3.0, exit_speed_ms * friction_factor)


def _get_ball_travel_time(exit_speed_kmh: float, distance: float) -> float:
    """Time for ball to travel distance along ground."""
    if distance <= 0:
        return 0.0
    avg_speed = _get_ground_ball_speed(exit_speed_kmh, distance)
    return distance / avg_speed


def _get_throw_distance(x: float, y: float) -> float:
    """Distance to nearest set of stumps."""
    dist_batting = _distance(x, y)
    dist_bowling = _distance(x, y + PITCH_LENGTH)
    return max(0.1, min(dist_batting, dist_bowling))  # Avoid zero


def _calculate_fielding_time(
    exit_speed: float,
    intercept_distance: float,
    lateral_distance: float,
    fielder_x: float,
    fielder_y: float
) -> float:
    """
    Total time from ball leaving bat to reaching stumps.

    Accounts for fielder movement during ball flight - the fielder can
    cover ground while the ball is traveling, reducing effective lateral distance.
    """
    ball_time = _get_ball_travel_time(exit_speed, intercept_distance)

    # Fielder can move toward intercept point during ball flight
    available_movement_time = max(0.0, ball_time - FIELDER_REACTION_TIME)
    distance_covered = available_movement_time * FIELDER_RUN_SPEED

    # Effective lateral distance after accounting for movement during flight
    effective_lateral = max(0.0, lateral_distance - distance_covered)

    # Collection time based on remaining distance to cover
    if effective_lateral < 0.5:
        collection = COLLECTION_TIME_DIRECT
    elif effective_lateral < 2.0:
        collection = COLLECTION_TIME_MOVING
    else:
        collection = COLLECTION_TIME_DIVING

    throw_dist = _get_throw_distance(fielder_x, fielder_y)
    throw_time = throw_dist / THROW_SPEED

    return ball_time + collection + throw_time


def _calculate_runs_from_fielding_time(fielding_time: float, is_misfield: bool) -> int:
    """Calculate runs based on total fielding time."""
    effective_time = fielding_time + MISFIELD_TIME_PENALTY if is_misfield else fielding_time

    if effective_time < TIME_FOR_FIRST_RUN:
        return 0

    runs = 1
    remaining = effective_time - TIME_FOR_FIRST_RUN

    if remaining >= TIME_FOR_EXTRA_RUN:
        runs = 2
        remaining -= TIME_FOR_EXTRA_RUN

    if remaining >= TIME_FOR_EXTRA_RUN:
        runs = 3

    return runs


# =============================================================================
# Result Builders
# =============================================================================

def _build_result(
    outcome: str,
    runs: int,
    is_boundary: bool,
    is_aerial: bool,
    fielder: Optional[Fielder],
    end_x: float,
    end_y: float,
    description: str,
    catch_analysis: Optional[CatchAnalysis] = None,
    fielder_pos: Optional[dict] = None,
) -> dict:
    """Build standardized result dictionary."""
    result = {
        'outcome': outcome,
        'runs': runs,
        'is_boundary': is_boundary,
        'is_aerial': is_aerial,
        'fielder_involved': fielder.name if fielder else None,
        'end_position': {'x': end_x, 'y': end_y},
        'description': description,
    }

    if fielder_pos:
        result['fielder_position'] = fielder_pos
    elif fielder:
        result['fielder_position'] = {'x': fielder.x, 'y': fielder.y}

    if catch_analysis:
        result['catch_analysis'] = dict(catch_analysis)

    return result


# =============================================================================
# Main Simulation - Decomposed Checks
# =============================================================================

def _check_six(
    traj: Trajectory,
    projected_distance: float,
    max_height: float,
    vertical_angle: float,
    boundary_distance: float,
    is_aerial: bool,
    shot_name: str,
    landing_x: float,
    landing_y: float,
) -> Optional[dict]:
    """Check if shot is a six (over boundary on full)."""
    if projected_distance < boundary_distance:
        return None

    height_at_boundary = _get_ball_height_at_distance(
        boundary_distance, projected_distance, max_height, vertical_angle
    )

    if is_aerial and height_at_boundary > SIX_HEIGHT_AT_BOUNDARY:
        boundary_point = _get_boundary_intersection(landing_x, landing_y, boundary_distance)
        return _build_result(
            outcome='6',
            runs=6,
            is_boundary=True,
            is_aerial=True,
            fielder=None,
            end_x=boundary_point['x'],
            end_y=boundary_point['y'],
            description=f"{shot_name.capitalize()} for six!",
        )

    return None


def _evaluate_catches(
    fielders: list[Fielder],
    traj: Trajectory,
    projected_distance: float,
    max_height: float,
    landing_x: float,
    landing_y: float,
    boundary_distance: float,
    difficulty: str,
    exit_speed: float,
    shot_name: str,
    is_aerial: bool,
) -> Optional[dict]:
    """Evaluate catching chances for all fielders."""
    # Only evaluate catches for aerial shots
    if not is_aerial or max_height < CATCH_HEIGHT_MIN:
        return None

    chances = []

    for fielder in fielders:
        if not _is_fielder_in_ball_path(fielder, landing_x, landing_y):
            continue

        fielder_dist = _distance(fielder.x, fielder.y)
        if fielder_dist > projected_distance + CATCH_EXTENDED_RANGE:
            continue

        lat_dist, closest_x, closest_y, t = _distance_point_to_line_segment(
            fielder.x, fielder.y, 0.0, 0.0, landing_x, landing_y
        )

        if t < FIELDER_PATH_START_T:
            continue

        intercept_dist = _distance(closest_x, closest_y)
        analysis = _analyze_catch_difficulty(
            fielder, traj, intercept_dist, lat_dist, landing_x, landing_y, projected_distance, max_height
        )

        if analysis['can_catch']:
            chances.append((fielder, analysis, intercept_dist))

    # Sort by intercept distance
    chances.sort(key=lambda x: x[2])

    for fielder, analysis, _ in chances:
        outcome = _roll_catch_outcome(analysis, difficulty)

        if outcome == 'caught':
            catch_x, catch_y, _ = _get_ball_position_at_time(
                traj, analysis['time_to_intercept'], landing_x, landing_y
            )

            if analysis['catch_type'] == 'spectacular':
                desc = "Spectacular catch"
            elif analysis['catch_type'] == 'hard':
                desc = "Great catch"
            else:
                desc = "Caught"

            if analysis['movement_required'] > FIELDER_STATIC_RANGE + 1:
                desc += f" (running {analysis['movement_required']:.1f}m)"
            elif analysis['movement_required'] > FIELDER_STATIC_RANGE:
                desc += " (diving)"

            return _build_result(
                outcome='caught',
                runs=0,
                is_boundary=False,
                is_aerial=True,
                fielder=fielder,
                end_x=catch_x,
                end_y=catch_y,
                description=f"{desc} at {fielder.name}!",
                catch_analysis=analysis,
            )

        elif outcome == 'dropped':
            if projected_distance >= boundary_distance:
                bp = _get_boundary_intersection(landing_x, landing_y, boundary_distance)
                return _build_result(
                    outcome='4',
                    runs=4,
                    is_boundary=True,
                    is_aerial=True,
                    fielder=fielder,
                    end_x=bp['x'],
                    end_y=bp['y'],
                    description=f"{shot_name.capitalize()}, dropped at {fielder.name}, four!",
                    catch_analysis=analysis,
                )

            runs = _calculate_runs_for_dropped(projected_distance, exit_speed)
            return _build_result(
                outcome='dropped',
                runs=runs,
                is_boundary=False,
                is_aerial=True,
                fielder=fielder,
                end_x=landing_x,
                end_y=landing_y,
                description=f"{shot_name.capitalize()}, dropped at {fielder.name}, runs {runs}",
                catch_analysis=analysis,
            )

    return None


def _calculate_runs_for_dropped(projected_distance: float, exit_speed: float) -> int:
    """Calculate runs when catch is dropped."""
    if projected_distance >= MID_FIELD_RADIUS:
        return random.choice([2, 2, 3])
    elif projected_distance >= INNER_RING_RADIUS:
        return random.choice([1, 1, 2])
    return 1


def _check_boundary_four(
    projected_distance: float,
    boundary_distance: float,
    landing_x: float,
    landing_y: float,
    is_aerial: bool,
    shot_name: str,
) -> Optional[dict]:
    """Check if shot reaches boundary for four."""
    if projected_distance < boundary_distance:
        return None

    bp = _get_boundary_intersection(landing_x, landing_y, boundary_distance)
    return _build_result(
        outcome='4',
        runs=4,
        is_boundary=True,
        is_aerial=is_aerial,
        fielder=None,
        end_x=bp['x'],
        end_y=bp['y'],
        description=f"{shot_name.capitalize()} to the boundary for four!",
    )


def _evaluate_ground_fielding(
    fielders: list[Fielder],
    projected_distance: float,
    landing_x: float,
    landing_y: float,
    exit_speed: float,
    is_aerial: bool,
    shot_name: str,
    probs: dict,
) -> Optional[dict]:
    """Evaluate ground fielding chances."""
    chances = []

    for fielder in fielders:
        if not _is_fielder_in_ball_path(fielder, landing_x, landing_y):
            continue

        lat_dist, closest_x, closest_y, t = _distance_point_to_line_segment(
            fielder.x, fielder.y, 0.0, 0.0, landing_x, landing_y
        )

        if t < FIELDER_PATH_START_T:
            continue

        intercept_dist = _distance(closest_x, closest_y)
        fielder_dist = _distance(fielder.x, fielder.y)

        # Check if fielder can reach the ball
        # Account for movement during ball flight
        ball_travel_time = _get_ball_travel_time(exit_speed, intercept_dist)
        available_movement = max(0.0, ball_travel_time - FIELDER_REACTION_TIME) * FIELDER_RUN_SPEED
        max_reach = GROUND_FIELDING_RANGE + available_movement

        if lat_dist <= max_reach and fielder_dist <= projected_distance + max_reach:
            chances.append((fielder, lat_dist, intercept_dist))

    chances.sort(key=lambda x: x[1])  # Sort by lateral distance

    for fielder, lat_dist, intercept_dist in chances:
        outcome = _roll_ground_fielding_outcome(probs)

        fielding_time = _calculate_fielding_time(
            exit_speed, intercept_dist, lat_dist, fielder.x, fielder.y
        )

        if outcome == 'stopped':
            runs = _calculate_runs_from_fielding_time(fielding_time, False)

            if runs == 0:
                return _build_result(
                    outcome='dot',
                    runs=0,
                    is_boundary=False,
                    is_aerial=is_aerial,
                    fielder=fielder,
                    end_x=fielder.x,
                    end_y=fielder.y,
                    description=f"{shot_name.capitalize()} fielded by {fielder.name}, no run",
                )

            return _build_result(
                outcome=str(runs),
                runs=runs,
                is_boundary=False,
                is_aerial=is_aerial,
                fielder=fielder,
                end_x=fielder.x,
                end_y=fielder.y,
                description=f"{shot_name.capitalize()}, {fielder.name} fields, {runs} run{'s' if runs > 1 else ''}",
            )

        elif outcome == 'misfield_no_extra':
            runs = max(1, _calculate_runs_from_fielding_time(fielding_time + FUMBLE_TIME_PENALTY, False))
            return _build_result(
                outcome='misfield',
                runs=runs,
                is_boundary=False,
                is_aerial=is_aerial,
                fielder=fielder,
                end_x=fielder.x,
                end_y=fielder.y,
                description=f"{shot_name.capitalize()}, misfield by {fielder.name}, {runs} run{'s' if runs > 1 else ''}",
            )

        else:  # misfield_extra
            runs = _calculate_runs_from_fielding_time(fielding_time, True)
            return _build_result(
                outcome='misfield',
                runs=runs,
                is_boundary=False,
                is_aerial=is_aerial,
                fielder=fielder,
                end_x=landing_x,
                end_y=landing_y,
                description=f"{shot_name.capitalize()}, misfield by {fielder.name}, {runs} run{'s' if runs > 1 else ''}",
            )

    return None


def _fallback_nearest_fielder(
    fielders: list[Fielder],
    landing_x: float,
    landing_y: float,
    projected_distance: float,
    exit_speed: float,
    is_aerial: bool,
    shot_name: str,
) -> dict:
    """Fallback when no fielder in direct path - nearest fielder retrieves."""
    if not fielders:
        logger.warning("No fielders in config, returning boundary")
        return _build_result(
            outcome='4',
            runs=4,
            is_boundary=True,
            is_aerial=is_aerial,
            fielder=None,
            end_x=landing_x,
            end_y=landing_y,
            description=f"{shot_name.capitalize()} to the boundary",
        )

    # Find nearest fielder to landing point
    nearest = min(fielders, key=lambda f: _distance(f.x, f.y, landing_x, landing_y))
    nearest_dist = _distance(nearest.x, nearest.y, landing_x, landing_y)

    # Time calculation with fielder movement during flight
    ball_time = _get_ball_travel_time(exit_speed, projected_distance)
    available_run = max(0.0, ball_time - FIELDER_REACTION_TIME)
    covered = available_run * FIELDER_RUN_SPEED
    remaining = max(0.0, nearest_dist - covered)
    additional_run = remaining / FIELDER_RUN_SPEED if FIELDER_RUN_SPEED > 0 else 0

    throw_dist = _get_throw_distance(landing_x, landing_y)
    throw_time = throw_dist / THROW_SPEED

    total_time = ball_time + additional_run + PICKUP_TIME_STOPPED + throw_time
    runs = _calculate_runs_from_fielding_time(total_time, False)

    if runs == 0:
        desc = f"{shot_name.capitalize()}, {nearest.name} collects, no run"
    else:
        desc = f"{shot_name.capitalize()}, {nearest.name} retrieves, {runs} run{'s' if runs > 1 else ''}"

    return _build_result(
        outcome=str(runs) if runs > 0 else 'dot',
        runs=runs,
        is_boundary=False,
        is_aerial=is_aerial,
        fielder=nearest,
        end_x=landing_x,
        end_y=landing_y,
        description=desc,
    )


# =============================================================================
# Main Entry Point
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
    difficulty: str = 'medium',
) -> dict:
    """
    Simulate the outcome of a cricket shot.

    Args:
        exit_speed: Ball speed off bat in km/h
        horizontal_angle: Direction (0°=straight, +ve=off, -ve=leg)
        vertical_angle: Elevation (0°=ground, 45°=lofted)
        landing_x: X landing coordinate (metres from batter)
        landing_y: Y landing coordinate (positive=toward bowler)
        projected_distance: Total distance in metres
        max_height: Peak trajectory height in metres
        field_config: List of fielder dicts with 'x', 'y', 'name'
        boundary_distance: Boundary radius in metres
        difficulty: 'easy', 'medium', or 'hard'

    Returns:
        Dict with outcome, runs, description, fielder info, etc.
    """
    # Validate and sanitize inputs
    (exit_speed, horizontal_angle, vertical_angle, landing_x, landing_y,
     projected_distance, max_height, boundary_distance, warnings) = _validate_and_sanitize_inputs(
        exit_speed, horizontal_angle, vertical_angle, landing_x, landing_y,
        projected_distance, max_height, boundary_distance
    )

    for warning in warnings:
        logger.warning(warning)

    # Validate difficulty
    if difficulty not in DIFFICULTY_SETTINGS:
        logger.warning(f"Unknown difficulty '{difficulty}', using 'medium'")
        difficulty = 'medium'

    probs = DIFFICULTY_SETTINGS[difficulty]

    # Convert field config to efficient format
    fielders = _convert_field_config(field_config)

    # Determine shot characteristics
    is_aerial = max_height > AERIAL_HEIGHT_THRESHOLD or vertical_angle > AERIAL_ANGLE_THRESHOLD
    shot_name = _get_shot_direction_name(horizontal_angle, is_aerial)

    # Calculate trajectory
    traj = _calculate_trajectory(exit_speed, horizontal_angle, vertical_angle)

    logger.debug(f"Shot: {shot_name}, speed={exit_speed:.1f}km/h, "
                 f"h_angle={horizontal_angle:.1f}°, v_angle={vertical_angle:.1f}°, "
                 f"distance={projected_distance:.1f}m, height={max_height:.1f}m")

    # Check 1: Six
    result = _check_six(traj, projected_distance, max_height, vertical_angle,
                        boundary_distance, is_aerial, shot_name, landing_x, landing_y)
    if result:
        logger.info(f"Result: SIX - {result['description']}")
        return result

    # Check 2: Catches
    result = _evaluate_catches(fielders, traj, projected_distance, max_height,
                               landing_x, landing_y, boundary_distance, difficulty,
                               exit_speed, shot_name, is_aerial)
    if result:
        logger.info(f"Result: {result['outcome'].upper()} - {result['description']}")
        return result

    # Check 3: Four
    result = _check_boundary_four(projected_distance, boundary_distance,
                                   landing_x, landing_y, is_aerial, shot_name)
    if result:
        logger.info(f"Result: FOUR - {result['description']}")
        return result

    # Check 4: Ground fielding
    result = _evaluate_ground_fielding(fielders, projected_distance, landing_x, landing_y,
                                        exit_speed, is_aerial, shot_name, probs)
    if result:
        logger.info(f"Result: {result['outcome'].upper()} - {result['description']}")
        return result

    # Fallback: Nearest fielder retrieves
    result = _fallback_nearest_fielder(fielders, landing_x, landing_y, projected_distance,
                                        exit_speed, is_aerial, shot_name)
    logger.info(f"Result: {result['outcome'].upper()} - {result['description']}")
    return result
