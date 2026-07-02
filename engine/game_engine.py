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
    # Additional fields - Matched to TypeScript
    aerial_distance: float  # Distance traveled through air only
    rolling_distance: float  # Distance ball rolls after landing
    final_x: float  # Where ball stops (after rolling)
    final_y: float  # Where ball stops (after rolling)


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
    # Additional fields - Matched to TypeScript
    fielder_arrival_time: float  # Seconds for fielder to reach intercept
    arrived_before_landing: bool  # Did fielder arrive before ball landed


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
    # Additional fields - Matched to TypeScript
    fielding_position: Optional[dict]  # Where fielder collects ball {x, y}
    fielding_time: Optional[float]  # Total time from bat to stumps
    collection_difficulty: Optional[float]  # 0-1 how rushed was fielder
    alignment_score: Optional[float]  # 0-1 how direct ball path was
    priority_score: Optional[float]  # Combined weighted score
    fielder_arrival_time: Optional[float]  # Seconds for fielder to reach
    ball_arrival_time: Optional[float]  # Seconds for ball to reach


# =============================================================================
# Physical Constants (documented, tunable)
# =============================================================================

# Gravity
GRAVITY = 9.81  # m/s^2

# Ball starting height (bat contact point)
BAT_HEIGHT = 1.0  # metres

# Batter position offset from field center
# The boundary is a circle centered on the pitch center, not the batter
# Batter is 8.84m from pitch center (half pitch length minus crease)
BATTER_OFFSET_FROM_CENTER = 8.84  # metres

# =============================================================================
# Catch Thresholds
# =============================================================================

CATCH_HEIGHT_MIN = 0.2   # metres - below this is half-volley/scoop
CATCH_HEIGHT_MAX = 4.0   # metres - above this is uncatchable (jumping catch limit)
CATCH_OPTIMAL_MIN = 1.0  # metres - waist height  # Matched to TypeScript
CATCH_OPTIMAL_MAX = 1.8  # metres - chest height  # Matched to TypeScript

# =============================================================================
# Fielder Movement Constants
# =============================================================================

FIELDER_REACTION_TIME = 0.25  # seconds - elite fielders react in 0.15-0.25s  # Matched to TypeScript
FIELDER_RUN_SPEED = 6.0       # m/s - ~22 km/h, professional fielder sprint  # Matched to TypeScript
FIELDER_DIVE_RANGE = 1.0      # metres - full-length diving catch  # Matched to TypeScript
FIELDER_ACCEL_TIME = 0.5      # seconds to reach max speed  # Matched to TypeScript
FIELDER_STATIC_RANGE = 1.5    # metres - catch without moving (arm reach + step)
GROUND_FIELDING_RANGE = 3.0   # metres - lateral reach for ground balls


# =============================================================================
# Fielder Movement Helpers (with acceleration model)
# =============================================================================

def _get_fielder_movement_distance(movement_time: float) -> float:
    """
    Calculate distance a fielder can cover in given time.
    Uses linear acceleration model - fielder takes FIELDER_ACCEL_TIME to reach max speed.

    Args:
        movement_time: Time available for movement in seconds

    Returns:
        Distance covered in metres
    """
    if movement_time <= 0:
        return 0.0

    # Instant max speed when no acceleration time
    if FIELDER_ACCEL_TIME <= 0:
        return FIELDER_RUN_SPEED * movement_time

    accel = FIELDER_RUN_SPEED / FIELDER_ACCEL_TIME  # acceleration in m/s²

    if movement_time <= FIELDER_ACCEL_TIME:
        # Still accelerating: d = 0.5 * a * t²
        return 0.5 * accel * movement_time * movement_time
    else:
        # Reached max speed
        accel_dist = 0.5 * accel * FIELDER_ACCEL_TIME * FIELDER_ACCEL_TIME
        max_speed_time = movement_time - FIELDER_ACCEL_TIME
        return accel_dist + FIELDER_RUN_SPEED * max_speed_time


def _get_fielder_travel_time(distance: float) -> float:
    """
    Calculate time for fielder to cover a distance.
    Inverse of _get_fielder_movement_distance. Includes reaction time.

    Args:
        distance: Distance to cover in metres

    Returns:
        Total time including reaction time in seconds
    """
    if distance <= 0:
        return FIELDER_REACTION_TIME

    # Instant max speed when no acceleration time
    if FIELDER_ACCEL_TIME <= 0:
        return FIELDER_REACTION_TIME + distance / FIELDER_RUN_SPEED

    accel = FIELDER_RUN_SPEED / FIELDER_ACCEL_TIME
    accel_dist = 0.5 * accel * FIELDER_ACCEL_TIME * FIELDER_ACCEL_TIME

    if distance <= accel_dist:
        # Still in acceleration phase: d = 0.5 * a * t², so t = sqrt(2d/a)
        return FIELDER_REACTION_TIME + math.sqrt(2 * distance / accel)
    else:
        # Past acceleration
        remaining_dist = distance - accel_dist
        return FIELDER_REACTION_TIME + FIELDER_ACCEL_TIME + remaining_dist / FIELDER_RUN_SPEED


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
GROUND_FRICTION = 0.05        # deceleration factor per metre - cricket outfield  # Matched to TypeScript
MISFIELD_TIME_PENALTY = 2.0   # seconds added when ball gets past fielder (spec + TS parity)
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

# Difficulty settings - Matched to TypeScript
# Catch probabilities:
# - regulation_catch: standard catches with time to prepare
# - hard_catch: diving, running, or awkward catches
# Ground fielding probabilities:
# - stopped: clean fielding, ball returned quickly
# - misfield_no_extra: fumble but recovers, slight delay
# - misfield_extra: ball gets past, significant delay
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

# Catch probability is calculated dynamically based on difficulty score:
#   base_prob = 0.98 - 0.52 * difficulty_score
# These modifiers scale the base probability by difficulty level
CATCH_DIFFICULTY_MODIFIER = {
    'easy': 0.85,    # More drops - amateur fielders
    'medium': 1.0,   # Standard - professional level
    'hard': 1.10,    # Fewer drops - elite fielders (spec + TS parity)
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


def get_boundary_distance_at_angle(horizontal_angle: float, boundary_radius: float = 70.0) -> float:
    """
    Calculate the actual boundary distance from the batter at a given shot angle.

    The boundary is a circle centered on the pitch center, but the batter is
    offset from the pitch center by BATTER_OFFSET_FROM_CENTER (8.84m).

    This means:
    - Shots toward the bowler (0°) travel further to reach boundary (~79m)
    - Shots toward the keeper (180°) reach boundary sooner (~61m)
    - Square shots (90°) are roughly the nominal boundary distance (~69m)

    Args:
        horizontal_angle: Shot angle in degrees (0° = toward bowler, ±90° = square)
        boundary_radius: Nominal boundary radius from pitch center (default 70m)

    Returns:
        Actual distance from batter to boundary at this angle
    """
    angle_rad = math.radians(horizontal_angle)
    cos_angle = math.cos(angle_rad)
    sin_angle = math.sin(angle_rad)

    offset = BATTER_OFFSET_FROM_CENTER
    offset_sq = offset * offset

    # Ray-circle intersection formula
    # Batter at (0, -offset) from pitch center, boundary circle radius R
    # Distance = offset * cos(θ) + √(R² - offset² * sin²(θ))
    return offset * cos_angle + math.sqrt(boundary_radius * boundary_radius - offset_sq * sin_angle * sin_angle)


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

def _calculate_rolling_distance(horizontal_speed_ms: float, vertical_angle: float) -> float:
    """
    Calculate how far the ball rolls after landing.
    Uses exponential decay model: v = v0 * e^(-k*d)

    Args:
        horizontal_speed_ms: Horizontal component of ball speed in m/s
        vertical_angle: Vertical angle in degrees (0=flat, 90=straight up)

    Returns:
        Rolling distance in metres
    """
    # Energy retention on landing depends on impact angle
    # Low flat shots retain more speed, high lofted shots bounce and slow more
    # At 0 degrees: 85% retention, at 45 degrees: 40% retention, at 90 degrees: 5%
    impact_retention = 0.85 - 0.8 * math.sin(math.radians(vertical_angle))
    landing_speed = horizontal_speed_ms * impact_retention

    # Ball stops when it slows to ~1.5 m/s
    stop_threshold = 1.5
    if landing_speed <= stop_threshold:
        return 0.0

    # Exponential decay: d = ln(v0/threshold) / k
    rolling_distance = math.log(landing_speed / stop_threshold) / GROUND_FRICTION

    return max(0.0, rolling_distance)


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
            aerial_distance=0.0,
            rolling_distance=0.0,
            final_x=0.0,
            final_y=0.0,
        )

    speed_ms = speed_kmh / 3.6

    # Normalize angle to -180 to 180 range (handles both 0-360 and -180 to 180 input)
    horizontal_angle = _normalize_angle(horizontal_angle)

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

        # Minimal horizontal travel - negligible rolling
        aerial_distance = 0.1
        rolling_dist = _calculate_rolling_distance(0.1, vertical_angle)
        total_distance = aerial_distance + rolling_dist

        return Trajectory(
            projected_distance=total_distance,
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
            aerial_distance=aerial_distance,
            rolling_distance=rolling_dist,
            final_x=0.0,
            final_y=0.0,
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

    # Aerial distance (flight only)
    aerial_distance = v_horizontal * t_flight

    # Rolling distance after landing
    rolling_dist = _calculate_rolling_distance(v_horizontal, vertical_angle)

    # Total distance = air + rolling
    total_distance = aerial_distance + rolling_dist

    # Landing position (where ball hits ground after air travel)
    landing_x = -aerial_distance * sin_h
    landing_y = aerial_distance * cos_h

    # Final position (where ball stops after rolling)
    final_x = -total_distance * sin_h
    final_y = total_distance * cos_h

    # Direction unit vector
    dir_mag = math.sqrt(landing_x * landing_x + landing_y * landing_y)
    if dir_mag > 0:
        dir_x = landing_x / dir_mag
        dir_y = landing_y / dir_mag
    else:
        dir_x = 0.0
        dir_y = -1.0

    return Trajectory(
        projected_distance=total_distance,
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
        aerial_distance=aerial_distance,
        rolling_distance=rolling_dist,
        final_x=final_x,
        final_y=final_y,
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

    # Side exclusion: if ball going to one side and fielder clearly on opposite side
    # Allow fielders within 8m of center line to field either side
    ball_going_off_side = landing_x < -5   # Off side is negative X
    ball_going_leg_side = landing_x > 5    # Leg side is positive X
    fielder_on_leg_side = fielder.x > 8    # Fielder clearly on leg side
    fielder_on_off_side = fielder.x < -8   # Fielder clearly on off side

    if ball_going_off_side and fielder_on_leg_side:
        return False
    if ball_going_leg_side and fielder_on_off_side:
        return False

    # Normalize shot direction
    shot_dir_x = landing_x / shot_length
    shot_dir_y = landing_y / shot_length

    # Calculate perpendicular distance from fielder to ball path
    cross_product = fielder.x * shot_dir_y - fielder.y * shot_dir_x
    perpendicular_dist = abs(cross_product)

    # If fielder is more than 20m laterally from ball path, exclude them
    if perpendicular_dist > 20:
        return False

    fielder_distance = _distance(fielder.x, fielder.y)

    # For forward shots, exclude distant fielders who are behind the batter
    # They can't intercept a ball going away from them at speed
    ball_going_forward = landing_y > 5
    if ball_going_forward and fielder.y < 0 and fielder_distance > 10:
        return False

    # Dot product: positive = fielder in forward hemisphere
    dot = fielder.x * shot_dir_x + fielder.y * shot_dir_y

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
            movement_possible = _get_fielder_movement_distance(movement_time) + FIELDER_DIVE_RANGE

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

    # Can't catch - no arrival possible
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
            fielder_arrival_time=0.0,
            arrived_before_landing=False,
        )

    # Movement calculations
    movement_time = max(0.0, time_to_intercept - FIELDER_REACTION_TIME)
    movement_possible = _get_fielder_movement_distance(movement_time) + FIELDER_DIVE_RANGE

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

    # Calculate fielder arrival time - Matched to TypeScript
    run_distance = max(0.0, lateral_dist_actual - FIELDER_STATIC_RANGE)
    fielder_arrival_time = _get_fielder_travel_time(run_distance)

    # Did fielder arrive before ball landed?
    arrived_before_landing = fielder_arrival_time <= traj.time_of_flight

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
        fielder_arrival_time=fielder_arrival_time,
        arrived_before_landing=arrived_before_landing,
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


def _roll_ground_fielding_outcome(probs: dict, collection_difficulty: float = 0.0) -> str:
    """
    Roll ground fielding outcome with probability modified by collection difficulty.

    Args:
        probs: Difficulty settings dict with 'ground_fielding' probabilities
        collection_difficulty: 0-1, how rushed the fielder was
            0 = routine (arrived very early)
            0.3 = easy (arrived early, set for ball)
            0.5 = moderate (had to hustle)
            1.0 = hard (barely made it, diving)

    Returns:
        'stopped', 'misfield_no_extra', or 'misfield_extra'
    """
    gf = probs['ground_fielding']

    # Super easy - fielder arrived with plenty of time, routine collection
    if collection_difficulty < 0.15:
        return 'stopped'  # 100% clean stop for routine collections

    stopped_prob = gf['stopped']
    misfield_no_extra_prob = gf['misfield_no_extra']

    if collection_difficulty > 0.7:
        # Hard collection - diving/rushing, high misfield chance
        stopped_prob = gf['stopped'] * 0.6
        misfield_no_extra_prob = 0.30
    elif collection_difficulty > 0.3:
        # Moderate - had to move quickly but under control
        stopped_prob = gf['stopped'] * 0.88
        misfield_no_extra_prob = gf['misfield_no_extra'] + 0.05
    # else (0.15-0.3): easy collection - use base probabilities

    roll = random.random()
    if roll < stopped_prob:
        return 'stopped'
    if roll < stopped_prob + misfield_no_extra_prob:
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
    """Distance to nearest set of stumps.

    Batting-end stumps at the origin; bowler's-end stumps at (0, +PITCH_LENGTH)
    (+Y = toward bowler, per the CLAUDE.md coordinate system).
    """
    dist_batting = _distance(x, y)
    dist_bowling = _distance(x, y - PITCH_LENGTH)
    return max(0.1, min(dist_batting, dist_bowling))  # Avoid zero


def _calculate_fielding_time(
    exit_speed: float,
    intercept_distance: float,
    lateral_distance: float,
    intercept_x: float,
    intercept_y: float,
    aerial_distance: float,
    time_of_flight: float,
) -> float:
    """
    Total time from ball leaving bat to reaching stumps.

    Accounts for fielder movement during ball flight using acceleration model.
    """
    # Ball travel time = air time + rolling time
    if intercept_distance <= aerial_distance:
        # Ball still in air at intercept point
        if aerial_distance > 0:
            ball_travel_time = time_of_flight * (intercept_distance / aerial_distance)
        else:
            ball_travel_time = 0.0
    else:
        # Ball has landed, need to roll to intercept
        rolling_distance = intercept_distance - aerial_distance
        rolling_speed = _get_ground_ball_speed(exit_speed, rolling_distance)
        rolling_time = rolling_distance / rolling_speed
        ball_travel_time = time_of_flight + rolling_time

    # Fielder can move toward intercept point during ball flight
    available_movement_time = max(0.0, ball_travel_time - FIELDER_REACTION_TIME)
    distance_covered = _get_fielder_movement_distance(available_movement_time)

    # Effective lateral distance after accounting for movement during flight
    effective_lateral = max(0.0, lateral_distance - distance_covered)

    # Collection time based on remaining lateral distance
    if effective_lateral < 0.5:
        collection_time = COLLECTION_TIME_DIRECT
    elif effective_lateral < 2.0:
        collection_time = COLLECTION_TIME_MOVING
    else:
        collection_time = COLLECTION_TIME_DIVING

    # Throw from where ball is collected (intercept position)
    throw_distance = _get_throw_distance(intercept_x, intercept_y)
    throw_time = throw_distance / THROW_SPEED

    return ball_travel_time + collection_time + throw_time


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


def _calculate_alignment_score(
    intercept_x: float,
    intercept_y: float,
    landing_x: float,
    landing_y: float,
) -> float:
    """
    Calculate how direct the ball path is from intercept point to stumps.

    Args:
        intercept_x: X position where ball is intercepted
        intercept_y: Y position where ball is intercepted
        landing_x: X landing coordinate (ball direction)
        landing_y: Y landing coordinate (ball direction)

    Returns:
        0-1 score where 1.0 = perfect alignment (ball going straight at stumps)
    """
    # Calculate throw distance to both ends (bowler's stumps at (0, +PITCH_LENGTH))
    dist_to_batting = _distance(intercept_x, intercept_y)
    dist_to_bowling = _distance(intercept_x, intercept_y - PITCH_LENGTH)

    # Use the closer stumps
    throw_dist = min(dist_to_batting, dist_to_bowling)
    is_batting_end = dist_to_batting <= dist_to_bowling

    # Calculate angle between ball path and throw path
    ball_path_length = _distance(landing_x, landing_y)
    if ball_path_length < MIN_SHOT_LENGTH:
        return 1.0  # Ball not moving, trivially aligned

    # Unit vector of ball path
    ball_dir_x = landing_x / ball_path_length
    ball_dir_y = landing_y / ball_path_length

    # Vector from intercept point to target stumps
    if is_batting_end:
        throw_dir_x = -intercept_x
        throw_dir_y = -intercept_y
    else:
        throw_dir_x = -intercept_x
        throw_dir_y = PITCH_LENGTH - intercept_y

    throw_dir_length = math.sqrt(throw_dir_x * throw_dir_x + throw_dir_y * throw_dir_y)
    if throw_dir_length < 0.1:
        return 1.0  # At stumps already

    throw_dir_x /= throw_dir_length
    throw_dir_y /= throw_dir_length

    # Dot product gives alignment (1 = same direction, -1 = opposite)
    # We want 1 when ball is going toward stumps (opposite to throw direction)
    alignment = -(ball_dir_x * throw_dir_x + ball_dir_y * throw_dir_y)

    # Convert from [-1, 1] to [0, 1]
    return _clamp((alignment + 1.0) / 2.0, 0.0, 1.0)


def _calculate_priority_score(
    collection_difficulty: float,
    alignment_score: float,
    fielding_time: float,
) -> float:
    """
    Calculate combined priority score for fielding assessment.

    Weights:
    - Collection difficulty (40%): How easy was it to field
    - Alignment (30%): How direct is the throw path
    - Fielding time (30%): How quickly can ball reach stumps

    Args:
        collection_difficulty: 0-1 how rushed was fielder (lower = easier)
        alignment_score: 0-1 how direct ball path was (higher = better)
        fielding_time: Total time from bat to stumps

    Returns:
        0-1 priority score where higher = better fielding opportunity
    """
    # Invert collection_difficulty (lower difficulty = higher score)
    ease_score = 1.0 - collection_difficulty

    # Normalize fielding time (faster = higher score)
    # Assume 2s is excellent (1.0), 8s is poor (0.0)
    time_score = _clamp((8.0 - fielding_time) / 6.0, 0.0, 1.0)

    # Weighted combination
    priority = (
        0.4 * ease_score +
        0.3 * alignment_score +
        0.3 * time_score
    )

    return _clamp(priority, 0.0, 1.0)


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
    # New output fields - Matched to TypeScript
    fielding_position: Optional[dict] = None,
    fielding_time: Optional[float] = None,
    collection_difficulty: Optional[float] = None,
    alignment_score: Optional[float] = None,
    priority_score: Optional[float] = None,
    fielder_arrival_time: Optional[float] = None,
    ball_arrival_time: Optional[float] = None,
) -> dict:
    """Build standardized result dictionary."""
    result = {
        'outcome': outcome,
        'runs': runs,
        'is_boundary': is_boundary,
        'is_aerial': is_aerial,
        'fielder_involved': fielder.name if fielder else None,
        'fielder_position': fielder_pos if fielder_pos else ({'x': fielder.x, 'y': fielder.y} if fielder else None),
        'end_position': {'x': end_x, 'y': end_y},
        'description': description,
        # Always include catch_analysis (may be None for non-catch outcomes)
        'catch_analysis': None,
        # New output fields - always include (may be None)
        'fielding_position': fielding_position,
        'fielding_time': fielding_time,
        'collection_difficulty': collection_difficulty,
        'alignment_score': alignment_score,
        'priority_score': priority_score,
        'fielder_arrival_time': fielder_arrival_time,
        'ball_arrival_time': ball_arrival_time,
    }

    if catch_analysis:
        result['catch_analysis'] = dict(catch_analysis)
        # For catches, extract timing info from catch_analysis if not already set
        if result['fielder_arrival_time'] is None and 'fielder_arrival_time' in catch_analysis:
            result['fielder_arrival_time'] = catch_analysis['fielder_arrival_time']
        if result['ball_arrival_time'] is None and 'time_to_intercept' in catch_analysis:
            result['ball_arrival_time'] = catch_analysis['time_to_intercept']
        if result['collection_difficulty'] is None and 'difficulty' in catch_analysis:
            result['collection_difficulty'] = catch_analysis['difficulty']
        if result['fielding_position'] is None:
            # For catches, fielding_position is where the catch is made (end_position)
            result['fielding_position'] = {'x': end_x, 'y': end_y}

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
    # TS:1037 - Any ball at catchable height can be caught (regardless of isAerial)
    if max_height < CATCH_HEIGHT_MIN:
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
    """Calculate runs when catch is dropped.

    Matched to TypeScript: calculateRunsForDistance(distance, false, exitSpeed > 80)
    """
    # TS: if (distance >= MID_FIELD_RADIUS) return Math.random() < 0.33 ? 3 : 2
    if projected_distance >= MID_FIELD_RADIUS:
        return 3 if random.random() < 0.33 else 2
    # TS: if (distance >= INNER_RING_RADIUS) return Math.random() < 0.33 ? 2 : 1
    elif projected_distance >= INNER_RING_RADIUS:
        return 2 if random.random() < 0.33 else 1
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


def _find_best_ground_intercept(
    fielder_x: float,
    fielder_y: float,
    final_x: float,
    final_y: float,
    exit_speed_kmh: float,
    aerial_distance: float,
    time_of_flight: float,
    projected_distance: float,
) -> Optional[dict]:
    """
    Find the BEST (easiest) point along the ball path where a fielder can intercept.

    Scans all reachable points and returns the one with lowest collection difficulty.
    This ensures fielders collect at their natural position rather than sprinting
    to cut off the ball early when they could collect more comfortably later.

    Args:
        fielder_x: Fielder X position
        fielder_y: Fielder Y position
        final_x: Where ball ends up (after rolling)
        final_y: Where ball ends up (after rolling)
        exit_speed_kmh: Ball exit speed in km/h
        aerial_distance: Distance ball travels in air
        time_of_flight: Time ball is in air
        projected_distance: Total distance (aerial + rolling)

    Returns:
        Dict with intercept details, or None if fielder cannot intercept
        {
            'intercept_x': float,
            'intercept_y': float,
            'intercept_distance': float,
            'lateral_distance': float,
            'collection_difficulty': float,
            'fielder_time': float,
            'ball_time': float,
        }
    """
    # TS:785-788 - Direction unit vector of ball path
    path_length = math.sqrt(final_x * final_x + final_y * final_y)
    if path_length < 0.1:
        return None
    dir_x = final_x / path_length
    dir_y = final_y / path_length

    # TS:791-793 - Sample points along ball path, find one with lowest difficulty
    step_size = 2.0
    best_intercept: Optional[dict] = None
    lowest_difficulty = float('inf')

    # TS:795 - Loop from 5m to projected distance
    dist = 5.0
    while dist <= projected_distance:
        point_x = dir_x * dist
        point_y = dir_y * dist

        # TS:799-809 - Calculate ball travel time to this point
        if dist <= aerial_distance:
            # Ball still in air
            ball_time = time_of_flight * (dist / aerial_distance) if aerial_distance > 0 else 0.0
        else:
            # Ball has landed, rolling
            rolling_dist = dist - aerial_distance
            rolling_speed = _get_ground_ball_speed(exit_speed_kmh, rolling_dist)
            ball_time = time_of_flight + (rolling_dist / rolling_speed)

        # TS:811-819 - Calculate fielder travel time to this point
        dx = point_x - fielder_x
        dy = point_y - fielder_y
        fielder_dist = math.sqrt(dx * dx + dy * dy)

        # Fielder can reach within GROUND_FIELDING_RANGE of the point
        dist_to_travel = max(0.0, fielder_dist - GROUND_FIELDING_RANGE)

        # TS:821-841 - Calculate time for fielder to cover this distance
        if dist_to_travel <= 0:
            fielder_time = FIELDER_REACTION_TIME  # Already in range
        else:
            fielder_time = _get_fielder_travel_time(dist_to_travel)

        # TS:844 - Can fielder reach before ball? (0.1s grace for diving/stretching)
        if fielder_time <= ball_time + 0.1:
            # TS:846-858 - Calculate collection difficulty based on time ratio
            time_ratio = fielder_time / ball_time if ball_time > 0 else 0.0

            if time_ratio < 0.6:
                collection_difficulty = 0.0  # Easy - arrived with plenty of time
            elif time_ratio < 0.9:
                collection_difficulty = (time_ratio - 0.6) / 0.3 * 0.5  # 0 to 0.5
            else:
                collection_difficulty = 0.5 + (time_ratio - 0.9) / 0.2 * 0.5  # 0.5 to 1.0

            collection_difficulty = min(1.0, collection_difficulty)

            # TS:863-876 - Keep track of easiest intercept point
            if collection_difficulty < lowest_difficulty:
                lowest_difficulty = collection_difficulty
                best_intercept = {
                    'intercept_x': point_x,
                    'intercept_y': point_y,
                    'intercept_distance': dist,
                    'lateral_distance': min(fielder_dist, GROUND_FIELDING_RANGE + 2.5),
                    'collection_difficulty': collection_difficulty,
                    'fielder_time': fielder_time,
                    'ball_time': ball_time,
                }

        dist += step_size

    return best_intercept


def _evaluate_ground_fielding(
    fielders: list[Fielder],
    projected_distance: float,
    landing_x: float,
    landing_y: float,
    exit_speed: float,
    is_aerial: bool,
    shot_name: str,
    probs: dict,
    aerial_distance: float,
    time_of_flight: float,
    boundary_distance: float = 70.0,
) -> Optional[dict]:
    """
    Evaluate ground fielding chances.

    For boundary balls, fielders must intercept BEFORE the boundary.
    If they misfield a boundary ball, it's automatically a four.

    Uses weighted priority scoring to select the best fielder:
    - 0.5: alignment (is ball going toward them?)
    - 0.25: collection difficulty (how easy is the stop?)
    - 0.25: normalized intercept distance (closer intercept = can return faster)
    """
    # Check if ball is heading to boundary - fielders must intercept before boundary
    is_boundary_ball = projected_distance >= boundary_distance
    max_intercept_distance = boundary_distance if is_boundary_ball else projected_distance

    # Calculate ball path direction for alignment scoring
    ball_path_length = math.sqrt(landing_x * landing_x + landing_y * landing_y)
    ball_dir_x = landing_x / ball_path_length if ball_path_length > 0 else 0.0
    ball_dir_y = landing_y / ball_path_length if ball_path_length > 0 else 1.0

    chances = []

    for fielder in fielders:
        # TS:1178 - Skip fielders not in ball path
        if not _is_fielder_in_ball_path(fielder, landing_x, landing_y):
            continue

        # TS:1184-1193 - Find best intercept point using findBestGroundIntercept
        # This scans all points along ball path (5m to maxInterceptDistance) to find
        # the easiest intercept point for this fielder
        intercept = _find_best_ground_intercept(
            fielder.x,
            fielder.y,
            landing_x,
            landing_y,
            exit_speed,
            aerial_distance,
            time_of_flight,
            max_intercept_distance,  # Use boundary as limit for potential fours
        )

        if intercept:
            # TS:1196-1201 - Calculate alignment: perpendicular distance from fielder to ball path
            cross_product = fielder.x * ball_dir_y - fielder.y * ball_dir_x
            perpendicular_dist = abs(cross_product)
            # Normalize to 0-1 range (0 = directly on path, 1 = 30m+ away from path)
            alignment_score = min(1.0, perpendicular_dist / 30.0)

            # TS:1203-1211 - Weighted priority score
            normalized_intercept = min(1.0, intercept['intercept_distance'] / projected_distance) if projected_distance > 0 else 0.0
            priority_score = (
                0.5 * alignment_score +
                0.25 * intercept['collection_difficulty'] +
                0.25 * normalized_intercept
            )

            chances.append({
                'fielder': fielder,
                'lateral_distance': intercept['lateral_distance'],
                'intercept_distance': intercept['intercept_distance'],
                'intercept_x': intercept['intercept_x'],
                'intercept_y': intercept['intercept_y'],
                'alignment_score': alignment_score,
                'collection_difficulty': intercept['collection_difficulty'],
                'priority_score': priority_score,
                'ball_arrival_time': intercept['ball_time'],
                'fielder_arrival_time': intercept['fielder_time'],
            })

    # Sort by priority score (lower = higher priority)
    chances.sort(key=lambda x: x['priority_score'])

    for chance in chances:
        fielder = chance['fielder']
        lat_dist = chance['lateral_distance']
        intercept_dist = chance['intercept_distance']
        intercept_x = chance['intercept_x']
        intercept_y = chance['intercept_y']
        alignment_score = chance['alignment_score']
        collection_difficulty = chance['collection_difficulty']
        priority_score = chance['priority_score']
        ball_time = chance['ball_arrival_time']
        fielder_time = chance['fielder_arrival_time']

        outcome = _roll_ground_fielding_outcome(probs, collection_difficulty)

        fielding_time = _calculate_fielding_time(
            exit_speed, intercept_dist, lat_dist,
            intercept_x, intercept_y,
            aerial_distance, time_of_flight
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
                    end_x=intercept_x,
                    end_y=intercept_y,
                    description=f"{shot_name.capitalize()} fielded by {fielder.name}, no run",
                    fielding_position={'x': intercept_x, 'y': intercept_y},
                    fielding_time=fielding_time,
                    collection_difficulty=collection_difficulty,
                    alignment_score=alignment_score,
                    priority_score=priority_score,
                    fielder_arrival_time=fielder_time,
                    ball_arrival_time=ball_time,
                )

            return _build_result(
                outcome=str(runs),
                runs=runs,
                is_boundary=False,
                is_aerial=is_aerial,
                fielder=fielder,
                end_x=intercept_x,
                end_y=intercept_y,
                description=f"{shot_name.capitalize()}, {fielder.name} fields, {runs} run{'s' if runs > 1 else ''}",
                fielding_position={'x': intercept_x, 'y': intercept_y},
                fielding_time=fielding_time,
                collection_difficulty=collection_difficulty,
                alignment_score=alignment_score,
                priority_score=priority_score,
                fielder_arrival_time=fielder_time,
                ball_arrival_time=ball_time,
            )

        elif outcome == 'misfield_no_extra':
            runs = max(1, _calculate_runs_from_fielding_time(fielding_time + FUMBLE_TIME_PENALTY, False))
            return _build_result(
                outcome='misfield',
                runs=runs,
                is_boundary=False,
                is_aerial=is_aerial,
                fielder=fielder,
                end_x=intercept_x,
                end_y=intercept_y,
                description=f"{shot_name.capitalize()}, misfield by {fielder.name}, {runs} run{'s' if runs > 1 else ''}",
                fielding_position={'x': intercept_x, 'y': intercept_y},
                fielding_time=fielding_time + FUMBLE_TIME_PENALTY,
                collection_difficulty=collection_difficulty,
                alignment_score=alignment_score,
                priority_score=priority_score,
                fielder_arrival_time=fielder_time,
                ball_arrival_time=ball_time,
            )

        else:  # misfield_extra - ball gets past fielder
            # If it was a boundary ball, misfield = four
            if is_boundary_ball:
                boundary_point = _get_boundary_intersection(landing_x, landing_y, boundary_distance)
                return _build_result(
                    outcome='4',
                    runs=4,
                    is_boundary=True,
                    is_aerial=is_aerial,
                    fielder=fielder,
                    end_x=boundary_point['x'],
                    end_y=boundary_point['y'],
                    description=f"{shot_name.capitalize()}, misfield by {fielder.name}, four!",
                    fielding_position={'x': intercept_x, 'y': intercept_y},
                    fielding_time=fielding_time,
                    collection_difficulty=collection_difficulty,
                    alignment_score=alignment_score,
                    priority_score=priority_score,
                    fielder_arrival_time=fielder_time,
                    ball_arrival_time=ball_time,
                )

            # Non-boundary ball - they must chase and throw from further back
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
                fielding_position={'x': intercept_x, 'y': intercept_y},
                fielding_time=fielding_time,
                collection_difficulty=collection_difficulty,
                alignment_score=alignment_score,
                priority_score=priority_score,
                fielder_arrival_time=fielder_time,
                ball_arrival_time=ball_time,
            )

    # No fielder intercepted - if it was a boundary ball, it's a four
    if is_boundary_ball:
        boundary_point = _get_boundary_intersection(landing_x, landing_y, boundary_distance)
        return _build_result(
            outcome='4',
            runs=4,
            is_boundary=True,
            is_aerial=is_aerial,
            fielder=None,
            end_x=boundary_point['x'],
            end_y=boundary_point['y'],
            description=f"{shot_name.capitalize()} to the boundary for four!",
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

    # Prefer fielders on the correct side AND in the ball's direction
    # Off side = negative X, Leg side = positive X
    # Forward = positive Y, Backward = negative Y
    ball_going_off_side = landing_x < -5
    ball_going_leg_side = landing_x > 5
    ball_going_forward = landing_y > 5

    # Filter to fielders who can realistically retrieve
    same_side_fielders = []
    for f in fielders:
        # Side filtering
        if ball_going_off_side and f.x > 8:
            continue  # Skip leg-side fielders for off-side shots
        if ball_going_leg_side and f.x < -8:
            continue  # Skip off-side fielders for leg-side shots
        # Forward/backward filtering - exclude fielders behind the play
        # For forward shots, fielders behind batter can't retrieve unless close
        if ball_going_forward and f.y < 0:
            fielder_dist_to_batter = _distance(f.x, f.y)
            if fielder_dist_to_batter > 10:
                continue  # Skip distant backward fielders for forward shots
        same_side_fielders.append(f)

    # Use same-side fielders if available, otherwise use all fielders
    candidate_fielders = same_side_fielders if same_side_fielders else fielders

    # Calculate final position where ball stops (not just landing position)
    # The ball rolls from landing position in the same direction
    landing_dist = _distance(landing_x, landing_y)
    if landing_dist > 0.1:
        dir_x = landing_x / landing_dist
        dir_y = landing_y / landing_dist
        final_x = dir_x * projected_distance
        final_y = dir_y * projected_distance
    else:
        final_x, final_y = landing_x, landing_y

    # Find nearest fielder to FINAL position (where ball stops) from candidates
    nearest = min(candidate_fielders, key=lambda f: _distance(f.x, f.y, final_x, final_y))
    nearest_dist = _distance(nearest.x, nearest.y, final_x, final_y)

    # Time calculation with fielder movement during flight
    ball_time = _get_ball_travel_time(exit_speed, projected_distance)
    available_run_time = max(0.0, ball_time - FIELDER_REACTION_TIME)
    covered = _get_fielder_movement_distance(available_run_time)
    remaining = max(0.0, nearest_dist - covered)
    additional_run = _get_fielder_travel_time(remaining) - FIELDER_REACTION_TIME if remaining > 0 else 0
    fielder_arrival_time = ball_time + additional_run

    # Throw distance is from final position (where ball stopped)
    throw_dist = _get_throw_distance(final_x, final_y)
    throw_time = throw_dist / THROW_SPEED

    total_time = ball_time + additional_run + PICKUP_TIME_STOPPED + throw_time
    runs = _calculate_runs_from_fielding_time(total_time, False)

    # Calculate collection difficulty - for fallback, it's always a chase so difficulty is higher
    if ball_time > 0:
        time_ratio = fielder_arrival_time / ball_time
        if time_ratio < 1.2:
            collection_difficulty = 0.3  # Arrived soon after ball
        elif time_ratio < 1.8:
            collection_difficulty = 0.5  # Had to chase a bit
        else:
            collection_difficulty = 0.7  # Long chase
    else:
        collection_difficulty = 0.5

    # Calculate alignment and priority scores
    alignment_score = _calculate_alignment_score(final_x, final_y, final_x, final_y)
    priority_score = _calculate_priority_score(collection_difficulty, alignment_score, total_time)

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
        end_x=final_x,
        end_y=final_y,
        description=desc,
        fielding_position={'x': final_x, 'y': final_y},
        fielding_time=total_time,
        collection_difficulty=collection_difficulty,
        alignment_score=alignment_score,
        priority_score=priority_score,
        fielder_arrival_time=fielder_arrival_time,
        ball_arrival_time=ball_time,
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

    # Calculate actual boundary distance at this shot angle
    # The boundary is a circle centered on the pitch, not the batter
    actual_boundary = get_boundary_distance_at_angle(horizontal_angle, boundary_distance)

    # Convert field config to efficient format
    fielders = _convert_field_config(field_config)

    # Determine shot characteristics
    is_aerial = max_height > AERIAL_HEIGHT_THRESHOLD or vertical_angle > AERIAL_ANGLE_THRESHOLD
    shot_name = _get_shot_direction_name(horizontal_angle, is_aerial)

    # Calculate trajectory
    traj = _calculate_trajectory(exit_speed, horizontal_angle, vertical_angle)

    logger.debug(f"Shot: {shot_name}, speed={exit_speed:.1f}km/h, "
                 f"h_angle={horizontal_angle:.1f}°, v_angle={vertical_angle:.1f}°, "
                 f"distance={projected_distance:.1f}m, height={max_height:.1f}m, "
                 f"boundary={actual_boundary:.1f}m")

    # Check 1: Six
    result = _check_six(traj, projected_distance, max_height, vertical_angle,
                        actual_boundary, is_aerial, shot_name, landing_x, landing_y)
    if result:
        result['boundary_distance'] = actual_boundary
        logger.info(f"Result: SIX - {result['description']}")
        return result

    # Check 2: Catches
    result = _evaluate_catches(fielders, traj, projected_distance, max_height,
                               landing_x, landing_y, actual_boundary, difficulty,
                               exit_speed, shot_name, is_aerial)
    if result:
        result['boundary_distance'] = actual_boundary
        logger.info(f"Result: {result['outcome'].upper()} - {result['description']}")
        return result

    # Check 3: Ground fielding (including potential boundary balls)
    # For boundary balls, fielders must intercept BEFORE the boundary
    # This check comes before _check_boundary_four so fielders get a chance to intercept
    result = _evaluate_ground_fielding(fielders, projected_distance, landing_x, landing_y,
                                        exit_speed, is_aerial, shot_name, probs,
                                        traj.aerial_distance, traj.time_of_flight,
                                        actual_boundary)
    if result:
        result['boundary_distance'] = actual_boundary
        logger.info(f"Result: {result['outcome'].upper()} - {result['description']}")
        return result

    # Check 4: Four (only reached if no fielder intercepted boundary ball)
    result = _check_boundary_four(projected_distance, actual_boundary,
                                   landing_x, landing_y, is_aerial, shot_name)
    if result:
        result['boundary_distance'] = actual_boundary
        logger.info(f"Result: FOUR - {result['description']}")
        return result

    # Fallback: Nearest fielder retrieves
    result = _fallback_nearest_fielder(fielders, landing_x, landing_y, projected_distance,
                                        exit_speed, is_aerial, shot_name)
    result['boundary_distance'] = actual_boundary
    logger.info(f"Result: {result['outcome'].upper()} - {result['description']}")
    return result
